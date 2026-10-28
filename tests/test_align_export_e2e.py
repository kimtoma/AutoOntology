"""Integration: real ``autoontology align hanbit-docs --structured-source hanbit`` against the
local Ollama (judge through the service's /v1, bge-m3 embeddings) and the upstream backend,
then the real ``autoontology export hanbit-docs`` with the Turtle round-trip asserted against
counts taken independently from the upstream API and the store.

Needs ``autoontology up`` plus the T2/T3 data (``build hanbit``, ``extract hanbit-docs``). After
a first real run, align is a no-op re-run (cached verdicts, nothing re-patched), which is
also what the idempotence asserts check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from rdflib import Graph
from rdflib.namespace import OWL, PROV, RDF, RDFS
from typer.testing import CliRunner

from autoontology.config import REPO_ROOT, Settings

pytestmark = pytest.mark.integration

DOCS = "hanbit-docs"
STRUCTURED = "hanbit"


@pytest.fixture
def live(monkeypatch):
    from autoontology.engine.client import BackendClient
    from autoontology.engine.jobs import service_alive
    from autoontology.store.db import Store

    settings = Settings.from_env()
    if not service_alive(settings):
        pytest.skip("services are down (uv run autoontology up)")
    store = Store.for_home(settings.home)
    if store.get_source(DOCS) is None or store.get_source(STRUCTURED) is None:
        pytest.skip(f"needs sources {STRUCTURED} (build) and {DOCS} (extract)")
    monkeypatch.chdir(REPO_ROOT)
    client = BackendClient(settings.backend_url)
    yield settings, store, client
    client.close()


def test_align_hanbit_docs_applies_same_alignments(live):
    from autoontology.engine import align as align_job
    from autoontology.store.alignments import AlignmentStore
    from autoontology.store.documents import DocumentStore

    settings, store, client = live
    res = align_job.run_align(DOCS, settings=settings, store=store,
                              structured_source=STRUCTURED)
    assert res.status == "succeeded", res
    src = store.get_source(DOCS)
    rows = AlignmentStore(store).list(src.id)
    accepted = [a for a in rows if a.accepted]
    assert accepted, [(a.concept_name, a.relation, a.confidence) for a in rows]
    assert all(a.relation == "same" and a.structured_source == STRUCTURED for a in accepted)
    applied = [a for a in accepted if a.applied.get("synonyms_added")]
    assert applied, "no accepted alignment has applied synonyms"
    for a in accepted:  # every accepted concept name is now a synonym of its Term
        synonyms = {s.casefold() for s in client.get_term(a.term_id)["synonyms"]}
        assert a.concept_name.casefold() in synonyms, (a.concept_name, a.term_name, synonyms)
    names = {a.concept_name: a.term_name for a in accepted}
    # upstream names Terms singular or plural depending on the compile (claude: singular)
    assert names.get("계약") in ("Contract", "Contracts") or \
        names.get("고객") in ("Customer", "Customers") or \
        names.get("청구서") in ("Invoice", "Invoices"), names
    # identity policy (review #4): the role concept 승인권자 is never merged into a Term
    assert "승인권자" not in names, names
    # no attribute concept was applied as a Term synonym
    kinds = {c.id: c.kind for c in DocumentStore(store).list_concepts(src.id)}
    assert all(kinds[a.concept_id] != "attribute" for a in accepted)

    # idempotent: a re-run judges nothing new and patches nothing
    before = {a.term_id: client.get_term(a.term_id)["synonyms"] for a in accepted}
    again = align_job.run_align(DOCS, settings=settings, store=store,
                                structured_source=STRUCTURED)
    assert again.status == "succeeded", again
    run_id = again.run_id
    steps = {s.name: s.detail for s in store.get_steps(run_id)}
    assert steps["judge"]["judged"] == 0 and steps["apply"]["applied"] == 0, steps["judge"]
    assert {t: client.get_term(t)["synonyms"] for t in before} == before


def test_export_hanbit_docs_roundtrip(live):
    from autoontology.cli import app
    from autoontology.store.alignments import AlignmentStore
    from autoontology.store.documents import DocumentStore
    from autoontology.structured.semantic import database_id_for

    settings, store, client = live
    res = CliRunner().invoke(app, ["export", DOCS])
    assert res.exit_code == 0, res.output
    out = Path(re.search(r"^dir: (.+)$", res.output, re.M).group(1).strip())
    assert {p.name for p in out.iterdir()} >= {"ontology.ttl", "ontology.jsonld",
                                                "graph.graphml", "report.md", "model.yaml",
                                                "ossie.zip"}
    # independent expectations: upstream Terms of hanbit + unaligned class-kind concepts
    db_id, _ = database_id_for(client, store.get_source(STRUCTURED))
    terms = client.semantic_graph(db_id)["nodes"]
    src = store.get_source(DOCS)
    concepts = DocumentStore(store).list_concepts(src.id)
    folded = {a.concept_id for a in AlignmentStore(store).list(src.id)
              if a.accepted and a.relation == "same"}
    unaligned = [c for c in concepts if c.kind in ("entity", "metric", "attribute")
                 and c.id not in folded]
    g = Graph().parse(str(out / "ontology.ttl"), format="turtle")
    classes = set(g.subjects(RDF.type, OWL.Class))
    assert len(classes) == len(terms) + len(unaligned)
    props = set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(
        g.subjects(RDF.type, OWL.DatatypeProperty))
    assert props and all((p, RDFS.domain, None) in g and (p, RDFS.range, None) in g
                         for p in props)
    base = f"https://autoontology.local/ontology/{DOCS}#"
    by_label: dict[str, set] = {}
    for s, _, o in g.triples((None, RDFS.label, None)):
        by_label.setdefault(str(o), set()).add(s)
    for c in concepts:  # every document concept has provenance (own node or its Term)
        nodes = [s for s in by_label.get(c.name, set()) if str(s).startswith(base)]
        if c.id in folded:
            nodes = [n for n in g.subjects(RDF.type, OWL.Class)
                     if any(str(o) == c.name for o in g.objects(n, None))]
        assert nodes and any((n, PROV.wasDerivedFrom, None) in g for n in nodes), c.name
    assert "Merged ontology report" in (out / "report.md").read_text(encoding="utf-8")
