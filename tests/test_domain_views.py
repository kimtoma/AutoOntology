# SPDX-License-Identifier: MIT
"""A scoped view cannot expose another source through a merged concept or cache."""

import json
from copy import deepcopy

import pytest

from autoontology.ontology.model import (
    MergedOntology,
    OAxiom,
    OClass,
    ODocument,
    OEvidence,
    OIndividual,
    OMerge,
    OProperty,
)
from autoontology.ontology.projection import project
from autoontology.security.context import acting_as
from autoontology.security.views import knowledge_view, publication_authority
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.store.operations import digest


def model():
    m = MergedOntology(
        "org",
        "https://example.test/#",
        structured_sources=["contracts"],
        documents_sources=["policy", "hr"],
    )
    m.documents = {
        "doc-policy": ODocument("doc-policy", "계약 규정", "a" * 64, "/policy.md", "policy"),
        "doc-hr": ODocument("doc-hr", "HR_PRIVATE", "b" * 64, "/hr-secret.md", "hr"),
    }
    m.evidence = {
        "ev-policy": OEvidence("ev-policy", "doc-policy", "계약 승인 기준", 1, True),
        "ev-hr": OEvidence("ev-hr", "doc-hr", "PRIVATE_SALARY", 1, True),
    }
    m.classes["term-contract"] = OClass(
        "term-contract", "계약", "structured", structured_source="contracts", term_id="contract"
    )
    m.classes["term-mixed"] = OClass(
        "term-mixed",
        "공통 개념",
        "structured",
        structured_source="contracts",
        documents_source="policy",
        definitions=["공개 규칙", "PRIVATE_SALARY"],
        derived_from=["ev-policy", "ev-hr"],
        term_id="mixed",
    )
    m.individuals["concept-rule"] = OIndividual(
        "concept-rule",
        "승인 기준",
        "BusinessRule",
        "rule",
        comment="계약 승인",
        derived_from=["ev-policy"],
        documents_source="policy",
    )
    m.individuals["concept-pay"] = OIndividual(
        "concept-pay",
        "PRIVATE_SALARY",
        "BusinessRule",
        "rule",
        derived_from=["ev-hr"],
        documents_source="hr",
    )
    m.properties["rel-safe"] = OProperty(
        "rel-safe",
        "object",
        "승인",
        "term-contract",
        "BusinessRule",
        "document",
        owner="policy",
        subject_node="term-contract",
        object_node="concept-rule",
        derived_from=["ev-policy"],
    )
    m.properties["rel-hidden"] = OProperty(
        "rel-hidden",
        "object",
        "PRIVATE_LINK",
        "term-contract",
        "BusinessRule",
        "document",
        owner="hr",
        subject_node="term-contract",
        object_node="concept-pay",
        derived_from=["ev-hr"],
    )
    m.merges = [
        OMerge("concept-policy", "공개 개념", "entity", "term-mixed", 0.9, ["ev-policy"], "policy"),
        OMerge("concept-secret", "PRIVATE_ALIAS", "entity", "term-mixed", 0.9, ["ev-hr"], "hr"),
    ]
    m.axioms = [
        OAxiom(
            "term-contract",
            "related",
            "term-mixed",
            "alignment",
            ["ev-hr"],
            rationale="PRIVATE_REASON",
        )
    ]
    m.unaligned = [{"source": "hr", "name": "PRIVATE_DIAGNOSTIC"}]
    m.decisions = [{"note": "PRIVATE_DECISION"}]
    m.expected_classes = 2
    return m


def test_projection_preserves_complete_visible_components_without_private_enrichment():
    source = model()
    before = deepcopy(source.to_dict())
    view = project(source, {"contracts", "policy"})
    assert set(view.classes) == {"term-contract"}
    assert set(view.individuals) == {"concept-rule"}
    assert set(view.properties) == {"rel-safe"}
    assert set(view.documents) == {"doc-policy"}
    assert set(view.evidence) == {"ev-policy"}
    assert not view.merges and not view.axioms and not view.decisions
    assert "PRIVATE" not in json.dumps(view.to_dict()) and "hr-secret" not in json.dumps(
        view.to_dict()
    )
    assert view.expected_classes is None and view.counts()["classes"] == 1
    assert source.to_dict() == before
    assert "term-mixed" in project(source, {"contracts", "policy", "hr"}).classes


def test_unknown_origin_and_missing_evidence_do_not_become_public():
    source = model()
    source.classes["unknown"] = OClass("unknown", "PRIVATE_UNKNOWN", "document")
    source.classes["broken"] = OClass(
        "broken", "PRIVATE_BROKEN", "document", documents_source="policy", derived_from=["missing"]
    )
    source.classes["owner-missing"] = OClass(
        "owner-missing",
        "PRIVATE_OWNER",
        "structured",
        documents_source="policy",
        derived_from=["ev-policy"],
    )
    source.axioms.append(
        OAxiom(
            "concept-rule", "related", "concept-rule", "document", rationale="PRIVATE_UNATTRIBUTED"
        )
    )
    source.properties["owner-missing"] = OProperty(
        "owner-missing",
        "object",
        "PRIVATE_OWNER",
        "concept-rule",
        "Concept",
        "document",
        derived_from=["ev-policy"],
    )
    assert "PRIVATE" not in json.dumps(project(source, {"policy"}).to_dict())


def test_legacy_cross_database_term_fields_require_all_structured_sources():
    source = model()
    source.structured_sources.append("private_people")
    source.classes["term-contract"].comment = "PRIVATE_SHARED_TERM_FIELD"
    limited = project(source, {"contracts", "policy"})
    assert "term-contract" not in limited.classes
    assert "PRIVATE" not in json.dumps(limited.to_dict())
    assert "term-contract" in project(source, {"contracts", "private_people", "policy"}).classes


@pytest.mark.parametrize("format", ["ttl", "jsonld", "graphml"])
def test_export_serializes_only_the_selected_view(format):
    from xml.etree import ElementTree

    from rdflib import Graph

    from autoontology.export.view import render

    visible = project(model(), {"contracts", "policy"})
    raw = render(visible, format)
    assert b"PRIVATE" not in raw and b"hr-secret" not in raw
    if format == "graphml":
        assert ElementTree.fromstring(raw).tag.endswith("graphml")
    else:
        assert len(Graph().parse(data=raw, format="turtle" if format == "ttl" else "json-ld")) > 0


@pytest.fixture
def scoped(tmp_path):
    import hashlib

    from autoontology.store.provenance import SourceArchive

    store = Store.for_home(tmp_path / "workspace")
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long test owner password")
    owner = identity.authenticate(issued.secret.reveal())
    finance = identity.create_domain(owner, "finance", "Finance")
    hr = identity.create_domain(owner, "hr", "HR")
    sources = [
        store.add_source(
            name=name, kind="documents" if name != "contracts" else "files", locator="{}"
        )
        for name in ("contracts", "policy", "hr")
    ]
    for source in sources:
        identity.assign_source(owner, source.id, hr["id"] if source.name == "hr" else finance["id"])
    identity.create_human(
        owner,
        "finance",
        "Finance viewer",
        "long finance test password",
        ["viewer"],
        domains=[finance["id"]],
    )
    viewer = identity.authenticate(
        identity.login("finance", "long finance test password").secret.reveal()
    )
    run = store.create_run(kind="build", source_id=None)
    content = model()
    for document in content.documents.values():
        path = tmp_path / (document.local + ".md")
        path.write_text("계약 승인 기준" if document.source == "policy" else "PRIVATE_SALARY")
        document.path = str(path)
        document.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        SourceArchive(store).capture_files(
            store.get_source(document.source), [path], run_id=run.id, root=tmp_path
        )
    revision = store.save_snapshot(
        run.id,
        content,
        sources=[
            {"name": source.name, "source_id": source.id, "kind": source.kind} for source in sources
        ],
    )
    store.update_run(run.id, status="succeeded")
    return store, identity, owner, viewer, finance, hr, sources, revision


def test_current_and_historical_source_classification_constrain_each_read(scoped):
    store, identity, owner, viewer, _, hr, sources, revision = scoped
    with acting_as(store, viewer):
        first = knowledge_view(store, store.get_snapshot(revision), action="knowledge.read")
        assert first.restricted and set(first.model.classes) == {"term-contract"}
        assert "PRIVATE" not in json.dumps(first.model.to_dict())
    identity.assign_source(owner, sources[0].id, hr["id"])
    with acting_as(store, viewer):
        second = knowledge_view(store, store.get_snapshot(revision), action="knowledge.read")
        assert not second.model.classes
    with acting_as(store, owner):
        assert not knowledge_view(
            store, store.get_snapshot(revision), action="knowledge.read"
        ).restricted


def test_unknown_historical_source_identity_never_uses_today_same_name(scoped):
    store, _, _, viewer, _, _, _, revision = scoped
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET sources_json='[]' WHERE revision=?", (revision,)
        )
    with acting_as(store, viewer):
        view = knowledge_view(store, store.get_snapshot(revision), action="knowledge.read")
        assert view.restricted and not view.model.classes and not view.model.documents


def test_saved_snapshot_and_another_users_cached_view_are_not_modified(scoped):
    store, _, owner, viewer, _, _, _, revision = scoped
    original = store.get_snapshot(revision)
    with acting_as(store, viewer):
        limited = knowledge_view(store, original, action="knowledge.read")
        assert limited.snapshot.counts == limited.model.counts()
        assert all(s["name"] != "hr" for s in limited.snapshot.sources)
    with acting_as(store, owner):
        full = knowledge_view(store, original, action="knowledge.read")
        assert "term-mixed" in full.model.classes
    assert store.get_snapshot(revision) == original


def pin(store, revision, authority):
    manifest = {
        "model_sha256": digest(store.get_snapshot(revision).model),
        "source_authority": authority,
    }
    sha = digest(manifest)
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO publications VALUES(?,?,?,?,'committed','fixture','fixture')",
            ("pub-" + sha, revision, json.dumps(manifest), sha),
        )


def test_publication_pins_old_domains_and_current_classification_can_only_restrict(scoped):
    store, identity, owner, viewer, finance, hr, sources, revision = scoped
    with acting_as(store, owner):
        authority = publication_authority(
            store, model(), [{"sources": store.get_snapshot(revision).sources}]
        )
    assert authority["sources"]["contracts"]["domains"] == [finance["id"]]
    pin(store, revision, authority)
    identity.create_human(
        owner, "hr_viewer", "HR viewer", "long HR viewer password", ["viewer"], domains=[hr["id"]]
    )
    hr_viewer = identity.authenticate(
        identity.login("hr_viewer", "long HR viewer password").secret.reveal()
    )
    identity.assign_source(owner, sources[0].id, hr["id"])
    for actor in (viewer, hr_viewer):
        with acting_as(store, actor):
            assert (
                "term-contract"
                not in knowledge_view(store, store.get_snapshot(revision)).model.classes
            )
    run = store.create_run(kind="build", source_id=None)
    new = store.save_snapshot(run.id, model(), sources=store.get_snapshot(revision).sources)
    with acting_as(store, owner):
        newer_authority = publication_authority(
            store, model(), [{"sources": store.get_snapshot(new).sources}]
        )
    pin(store, new, newer_authority)
    with acting_as(store, hr_viewer):
        assert "term-contract" in knowledge_view(store, store.get_snapshot(new)).model.classes


def test_unknown_or_foreign_workspace_publication_binding_cannot_fall_back(scoped):
    store, _, _, viewer, _, _, _, revision = scoped
    pin(store, revision, {"workspace_id": "another-workspace", "sources": {}})
    with acting_as(store, viewer):
        view = knowledge_view(store, store.get_snapshot(revision))
        assert not view.model.classes and not view.model.documents


def test_a_known_new_input_does_not_relabel_unknown_older_input_history(scoped):
    store, _, owner, _, _, _, _, revision = scoped
    bindings = [
        {"sources": store.get_snapshot(revision).sources},
        {"sources": [{"name": "contracts"}]},
    ]
    with acting_as(store, owner):
        authority = publication_authority(store, model(), bindings)
    assert authority["sources"]["contracts"]["status"] == "unknown"
    assert authority["sources"]["contracts"]["domains"] == []


def test_http_graph_concepts_originals_and_generated_exports_share_the_scoped_view(
    scoped, isolated_env, monkeypatch
):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from autoontology.config import Settings
    from autoontology.web.app import create_app

    store, identity, owner, _, _, _, _, revision = scoped
    settings = replace(
        Settings.from_env(), home=store.path.parent, exports_dir=store.path.parent / "exports"
    )
    finance = identity.login("finance", "long finance test password")
    admin = identity.login("owner", "long test owner password")
    headers = {"Authorization": "Bearer " + finance.secret.reveal()}
    with TestClient(create_app(settings)) as client:
        full = client.get(
            "/api/ontology/concepts", headers={"Authorization": "Bearer " + admin.secret.reveal()}
        )
        assert full.status_code == 200 and "PRIVATE" in full.text
        for path in (
            "/api/ontology",
            "/api/ontology/graph?evidence=true",
            "/api/ontology/concepts",
            "/api/ontology/documents",
            "/api/ontology/export",
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert "PRIVATE" not in response.text and "hr-secret" not in response.text
        graph = client.get("/api/ontology/graph", headers=headers).json()
        assert graph["access"]["restricted"] and graph["revision"] == revision
        assert client.get("/api/ontology/concepts/term-mixed", headers=headers).status_code == 404
        assert (
            client.get("/api/ontology/documents/doc-hr/original", headers=headers).status_code
            == 404
        )
        original = client.get("/api/ontology/documents/doc-policy/original", headers=headers)
        assert original.status_code == 200 and original.content.decode() == "계약 승인 기준"
        for format in ("ttl", "jsonld", "graphml"):
            response = client.get("/api/ontology/export/" + format, headers=headers)
            assert response.status_code == 200 and response.headers["x-ontology-scoped"] == "true"
            assert b"PRIVATE" not in response.content
        assert client.get("/api/exports", headers=headers).status_code == 403
        assert client.get("/api/settings", headers=headers).status_code == 403
        assert client.post("/api/runs", headers=headers, json={"kind": "build"}).status_code == 403
        identity.set_roles(
            owner,
            finance.principal["id"],
            ["viewer"],
            domains=[identity.create_domain(owner, "empty", "Empty")["id"]],
        )
        limited = client.get("/api/ontology/concepts", headers=headers)
        assert limited.status_code == 200 and not limited.json()["items"]


def test_native_ontology_original_and_export_view_use_the_same_selection(
    scoped, isolated_env, monkeypatch, tmp_path
):
    from typer.testing import CliRunner

    from autoontology.cli import app
    from autoontology.config import Settings
    from autoontology.security.credential_file import save_credential

    store, identity, _, _, _, _, _, _ = scoped
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(store.path.parent))
    monkeypatch.setenv("AUTOONTOLOGY_EXPORTS_DIR", str(tmp_path / "operational-exports"))
    settings = Settings.from_env()
    assert settings.home == store.path.parent
    private = tmp_path / "session"
    private.mkdir(mode=0o700)
    credential = private / "credential.json"
    issued = identity.login("finance", "long finance test password")
    save_credential(
        credential,
        issued,
        workspace_id=identity.status()["workspace_id"],
        origin=settings.service_url,
    )
    runner = CliRunner()

    def command(*args):
        return runner.invoke(app, ["--credential-file", str(credential), *args])

    summary = command("ontology")
    assert summary.exit_code == 0, summary.exception
    assert "contracts" in summary.output and "PRIVATE" not in summary.output
    documents = command("ops", "documents")
    assert documents.exit_code == 0 and "HR_PRIVATE" not in documents.output
    original = tmp_path / "original.md"
    assert command("ops", "original", "doc-policy", "--out", str(original)).exit_code == 0
    assert original.read_text() == "계약 승인 기준"
    assert (
        command("ops", "original", "doc-hr", "--out", str(tmp_path / "foreign.md")).exit_code != 0
    )
    assert not (tmp_path / "foreign.md").exists()
    output = tmp_path / "knowledge.ttl"
    result = command("ops", "export-view", "--out", str(output))
    assert result.exit_code == 0, result.exception
    assert json.loads(result.output)["restricted"] and b"PRIVATE" not in output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    assert command("ops", "export-view", "--out", str(output)).exit_code != 0
    assert (
        command(
            "ops", "export-view", "--out", str(settings.exports_dir / "r0001" / "view.ttl")
        ).exit_code
        != 0
    )


def test_view_file_effect_refuses_policy_drift_and_model_mutation(scoped, tmp_path, monkeypatch):
    from autoontology.export import view as writer
    from autoontology.security.policy import AccessError
    from autoontology.security.views import verify_view_authority

    store, identity, owner, viewer, _, _, _, revision = scoped
    with acting_as(store, viewer):
        selected = knowledge_view(store, store.get_snapshot(revision), action="exports.read")
        selected.model.classes["term-contract"].label = "modified without selection"
        with pytest.raises(AccessError, match="changed"):
            verify_view_authority(store, selected.model, "exports.read")
        selected = knowledge_view(store, store.get_snapshot(revision), action="exports.read")
        original = writer.render

        def drift(model, format):
            result = original(model, format)
            identity.create_domain(owner, "drift", "Policy drift")
            return result

        monkeypatch.setattr(writer, "render", drift)
        with pytest.raises(AccessError, match="changed"):
            writer.save(store, selected, "ttl", tmp_path / "result.ttl")
    assert not (tmp_path / "result.ttl").exists()
    assert not list(tmp_path.glob(".knowledge-view-*"))
