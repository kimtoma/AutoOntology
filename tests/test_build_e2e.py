# SPDX-License-Identifier: MIT
"""The unified build end to end on the samples: real Postgres, upstream backend and models.

Run with (the service must be up on the ``.env`` ports: ``uv run autoontology up``):

    AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_build_e2e.py -s

Registers ``hanbit`` (samples/quickstart/structured) and ``hanbit-docs``
(samples/quickstart/documents) when
missing, runs ``build`` (a first run on the local qwen3:8b profile takes ~30-45 min; a
re-run only redoes the cheap steps), ``export``, then checks the unified ontology through
the API of the running service.
"""

from pathlib import Path

import httpx
import pytest

from autoontology.config import REPO_ROOT, Settings
from autoontology.engine import export_job
from autoontology.engine.build import run_build
from autoontology.engine.extract import ensure_documents_source
from autoontology.engine.jobs import service_alive, setup_job_logging
from autoontology.ontology.model import MergedOntology
from autoontology.store.db import Store
from autoontology.structured import pipeline

pytestmark = pytest.mark.integration

STRUCTURED, DOCS = "hanbit", "hanbit-docs"


@pytest.fixture(scope="module")
def live():
    settings = Settings.from_env()
    if not service_alive(settings):
        pytest.skip("service not running (uv run autoontology up)")
    try:
        httpx.get(f"{settings.backend_url}/api/health/live", timeout=5).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("upstream backend not running (uv run autoontology up)")
    store = Store.for_home(settings.home)
    if store.get_source(STRUCTURED) is None:
        pipeline.add_source(
            store, str(REPO_ROOT / "samples" / "quickstart" / "structured"), name=STRUCTURED
        )
    if store.get_source(DOCS) is None:
        ensure_documents_source(store, DOCS, REPO_ROOT / "samples" / "quickstart" / "documents")
    setup_job_logging(settings, console=True)
    return settings, store


@pytest.fixture(scope="module")
def built(live):
    settings, store = live
    result = run_build(store, settings)
    assert result.revision is not None, result
    return result


def test_build_produces_one_unified_snapshot(live, built):
    settings, store = live
    assert built.status == "succeeded", built.problems
    assert built.steps["merge"] == built.steps["snapshot"] == "succeeded"
    for step in (
        "structured:hanbit:compile",
        "documents:hanbit-docs:consolidate",
        "align:hanbit-docs",
    ):
        assert built.steps[step] == "succeeded", step
    snap = store.latest_snapshot()
    assert snap.revision == built.revision
    model = MergedOntology.from_dict(snap.model)
    counts = model.counts()
    concepts = counts["classes_document"] + counts["individuals"] + counts["merged_concepts"]
    print(f"\nrevision {snap.revision}: {counts}")
    assert counts["classes_structured"] >= 3
    assert concepts >= 50
    assert counts["merged_concepts"] >= 1  # an accepted `same` folded into a Term
    assert model.aligned_terms()


def test_export_writes_the_revision_folder(live, built):
    settings, store = live
    result = export_job.run_export(store, settings)
    assert result.status == "succeeded"
    folder = Path(result.export_dir)
    assert folder.name == f"r{built.revision:04d}"
    for name in (
        "ontology.ttl",
        "ontology.jsonld",
        "ontology.graphml",
        "report.md",
        "upstream/hanbit.model.yaml",
        "upstream/hanbit.ossie.zip",
    ):
        assert (folder / name).is_file(), name
    assert result.details["serialize"]["roundtrip"]["ok"]


def test_api_serves_the_unified_ontology(live, built):
    settings, _ = live
    base = f"{settings.service_url}/api"
    body = httpx.get(f"{base}/ontology", timeout=30).json()
    assert body["revision"] == built.revision
    assert {s["name"] for s in body["sources"]} >= {STRUCTURED, DOCS}
    graph = httpx.get(f"{base}/ontology/graph", params={"layer": "all"}, timeout=60).json()
    origins = graph["counts"]["origins"]
    print(f"\ngraph: {graph['counts']}")
    assert origins.get("structured", 0) + origins.get("aligned", 0) >= 3
    assert origins.get("aligned", 0) >= 1 and origins.get("document", 0) >= 50
    concepts = httpx.get(f"{base}/ontology/concepts", params={"size": 5}, timeout=30).json()
    assert concepts["total"] >= 53 and concepts["items"][0]["kind"] == "term"
