"""Real end-to-end structured build (Postgres + upstream backend + Ollama).

Needs `uv run autoontology up` first. Takes minutes with qwen3:8b.
Run with: AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_structured_e2e.py -s
"""

import zipfile

import pytest

from autoontology.config import REPO_ROOT, Settings
from autoontology.engine import jobs
from autoontology.engine.client import BackendClient, BackendUnavailable
from autoontology.store.db import Store
from autoontology.structured import pipeline

pytestmark = pytest.mark.integration

SOURCE = "hanbit"


@pytest.fixture(scope="module")
def ctx():
    settings = Settings.from_env()
    client = BackendClient(settings.backend_url)
    try:
        client.health()
    except BackendUnavailable:
        pytest.skip("services are down; run `uv run autoontology up` first")
    store = Store.for_home(settings.home)
    return settings, store, client


def test_hanbit_build_produces_ontology(ctx):
    settings, store, client = ctx
    if store.get_source(SOURCE) is None:
        pipeline.add_source(store, str(REPO_ROOT / "samples" / "structured"), name=SOURCE)
    jobs.setup_job_logging(settings, console=True)
    result = jobs.run_build(SOURCE, resume=True, settings=settings, store=store, client=client)
    assert result.status == "succeeded"
    run = store.get_run(result.run_id)
    steps = {s.name: s for s in store.get_steps(run.id)}
    assert all(s.status == "succeeded" for s in steps.values()), steps
    assert steps["load"].detail["rows"] == 155

    db_id = client.database_id_by_name("autoontology_src_hanbit")
    graph = client.semantic_graph(db_id)
    counts = jobs.semantic_counts(client, db_id, graph)
    assert counts["terms_total"] >= 3, counts  # this database's Terms only
    assert not all(jobs.is_fallback_term(n) for n in graph["nodes"]), graph["nodes"]
    assert counts["semantic_fk_links"] >= 1, counts

    out = settings.exports_dir / SOURCE / run.id
    assert (out / "model.yaml").stat().st_size > 0
    assert (out / "summary.json").is_file()
    with zipfile.ZipFile(out / "ossie.zip") as zf:
        assert {"semantic_model.yaml", "ontology.yaml", "mapping.yaml"} <= {
            n.rsplit("/", 1)[-1] for n in zf.namelist()}
