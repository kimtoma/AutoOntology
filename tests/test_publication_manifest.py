# SPDX-License-Identifier: MIT
"""Publication receipts bind approved content to originals, vectors and artifacts."""

import hashlib
import json
from dataclasses import replace

import pytest

from autoontology.config import Settings
from autoontology.engine.operations import run_publish, validate
from autoontology.ontology.model import MergedOntology, OClass, ODocument, OEvidence
from autoontology.store.db import Store
from autoontology.store.operations import Operations
from autoontology.store.provenance import SourceArchive
from autoontology.store.publications import PublicationError, Publications


class Answer:
    async def stream(self, messages):
        yield "대표이사 승인입니다. [E1]"


def embed(texts, kind):
    return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
def prepared(tmp_path):
    settings = replace(
        Settings.from_env(), home=tmp_path / "home", exports_dir=tmp_path / "exports"
    )
    store = Store.for_home(settings.home)
    ops = Operations(store)
    path = tmp_path / "rules.md"
    raw = "3억원 이상은 대표이사 승인".encode()
    path.write_bytes(raw)
    source = store.add_source(
        name="rules", kind="documents", locator=json.dumps({"path": str(path), "recursive": False})
    )
    arc = SourceArchive(store)
    capture = arc.capture_files(source, [path], run_id="read", root=tmp_path)
    arc.mark_read(capture.id, "read", summary={"documents": 1})
    model = MergedOntology("org", "https://example.test/#", documents_sources=["rules"])
    model.documents["doc"] = ODocument(
        "doc", "규정", hashlib.sha256(raw).hexdigest(), str(path), "rules"
    )
    model.evidence["ev"] = OEvidence("ev", "doc", raw.decode(), 1, True)
    model.classes["concept-rule"] = OClass(
        "concept-rule", "승인", "document", derived_from=["ev"], documents_source="rules"
    )
    run = store.create_run(kind="build", source_id=None)
    revision = store.save_snapshot(
        run.id,
        model,
        sources=[
            {
                "name": "rules",
                "kind": "documents",
                "source_id": source.id,
                "source_revision": capture.id,
            }
        ],
    )
    store.update_run(run.id, status="succeeded")
    for item in ops.changes():
        ops.decide(item["id"], "accept", candidate=revision, note="Original checked")
    ops.add_check("승인권자는?", "대표이사")
    validation = validate(
        store, settings, revision=revision, embedder=(embed, "test"), llm=Answer()
    )
    assert validation["ok"]
    return store, settings, revision, validation, capture


def publish(prepared, **options):
    store, settings, revision, validation, _ = prepared
    return run_publish(
        store,
        settings,
        revision=revision,
        token=validation["token"],
        embedder=(embed, "test"),
        **options,
    )


def test_manifest_binds_model_originals_source_versions_validation_vectors_and_exports(prepared):
    store, settings, candidate, validation, capture = prepared
    result = publish(prepared)
    publication = Publications(store).get(result.revision)
    assert publication["status"] == "committed"
    manifest = publication["manifest"]
    assert manifest["candidate_revision"] == candidate
    assert manifest["previous_revision"] is None
    assert manifest["validation"]["token"] == validation["token"]
    assert manifest["model_sha256"] == Publications(store).model_digest(result.revision)
    assert manifest["originals"][0]["sha256"]
    assert capture.id in manifest["originals"][0]["source_revisions"]
    assert manifest["index"]["scope"] != f"org@r{result.revision}"
    folder = settings.exports_dir / f"r{result.revision:04}"
    assert json.loads((folder / "publication.json").read_text())["id"] == publication["id"]
    assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]
    assert "password" not in json.dumps(manifest).lower()


def test_configured_publication_pins_identified_current_domain_and_later_moves_only_restrict(
    prepared,
):
    from autoontology.security.context import acting_as
    from autoontology.security.views import knowledge_view
    from autoontology.store.identity import Identity

    store, settings, candidate, _, _ = prepared
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long publication owner password")
    owner = identity.authenticate(issued.secret.reveal())
    finance = identity.create_domain(owner, "finance", "Finance")
    hr = identity.create_domain(owner, "hr", "HR")
    source = store.get_source("rules")
    identity.assign_source(owner, source.id, finance["id"])
    identity.create_human(
        owner,
        "reader",
        "Finance reader",
        "long finance reader password",
        ["viewer"],
        domains=[finance["id"]],
    )
    reader = identity.authenticate(
        identity.login("reader", "long finance reader password").secret.reveal()
    )
    with acting_as(store, owner):
        for item in Operations(store).changes():
            Operations(store).decide(
                item["id"], "accept", candidate=candidate, note="Original and scope checked"
            )
        validation = validate(
            store, settings, revision=candidate, embedder=(embed, "test"), llm=Answer()
        )
        result = run_publish(
            store, settings, revision=candidate, token=validation["token"], embedder=(embed, "test")
        )
        publication = Publications(store).get(result.revision)
        binding = publication["manifest"]["source_authority"]["sources"]["rules"]
        assert binding["source_ids"] == [source.id] and binding["domains"] == [finance["id"]]
        assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]
    with acting_as(store, reader):
        selected = knowledge_view(store, store.get_snapshot(result.revision))
        assert "concept-rule" in selected.model.classes
    identity.assign_source(owner, source.id, hr["id"])
    with acting_as(store, reader):
        assert not knowledge_view(store, store.get_snapshot(result.revision)).model.classes
    assert Publications(store).get(result.revision) == publication


def test_missing_original_or_changed_validation_vectors_blocks_publication(prepared):
    store, settings, _, validation, _ = prepared
    with store._conn() as conn:
        conn.execute(
            "UPDATE playground_index SET embedding=? WHERE scope=?", (b"", validation["scope"])
        )
    with pytest.raises(PublicationError, match="validation.*index|index.*validation"):
        publish(prepared)
    assert Operations(store).state()["published_revision"] is None


def test_artifact_tampering_and_model_tampering_are_detected(prepared):
    store, settings, _, _, _ = prepared
    result = publish(prepared)
    folder = settings.exports_dir / f"r{result.revision:04}"
    original = (folder / "ontology.ttl").read_bytes()
    (folder / "ontology.ttl").write_text("changed")
    with pytest.raises(PublicationError, match="artifact"):
        Publications(store).verify(result.revision, settings.exports_dir)
    (folder / "ontology.ttl").write_bytes(original)
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET model_json='{}' WHERE revision=?", (result.revision,)
        )
    with pytest.raises(PublicationError, match="model"):
        Publications(store).verify(result.revision, settings.exports_dir)


def test_failed_preparation_cannot_leave_an_operational_manifest(prepared):
    store, settings, _, _, _ = prepared

    def broken(*args):
        raise RuntimeError("prepared artifact failure")

    with pytest.raises(RuntimeError):
        publish(prepared, exporter=broken)
    assert Operations(store).state()["published_revision"] is None
    assert Publications(store).listing() == []


def test_committed_pointer_and_run_receipt_survive_a_post_commit_exception(prepared, monkeypatch):
    store, _, _, _, _ = prepared
    real = Operations.commit

    def interrupt_after_commit(self, *args, **kwargs):
        real(self, *args, **kwargs)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(Operations, "commit", interrupt_after_commit)
    with pytest.raises(RuntimeError, match="response lost"):
        publish(prepared)
    head = Operations(store).state()["published_revision"]
    assert head is not None
    assert Publications(store).get(head)["status"] == "committed"
    assert store.get_run(store.get_snapshot(head).run_id).status == "succeeded"


def test_manifested_export_reuses_frozen_files_without_live_backend(prepared, tmp_path):
    from autoontology.engine.export_job import run_export

    store, settings, _, _, _ = prepared
    result = publish(prepared)

    class NoBackend:
        def __getattr__(self, name):
            pytest.fail(f"published export must not call live backend: {name}")

    export = run_export(store, settings, format="okf", out=tmp_path / "copy", client=NoBackend())
    assert (export.export_dir / "okf/index.md").is_file()
    assert not (export.export_dir / "ontology.ttl").exists()
    marker = json.loads((export.export_dir / "export.json").read_text())
    assert marker["view_of_publication"] == Publications(store).get(result.revision)["id"]
    assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]


def test_missing_original_blocks_even_successful_question_validation(prepared):
    store, _, _, _, _ = prepared
    from autoontology.store.provenance import SourceArchive

    for path in SourceArchive(store).root.rglob("rules.md"):
        path.unlink()
    with pytest.raises(PublicationError, match="original"):
        publish(prepared)
    assert Operations(store).state()["published_revision"] is None


def test_frozen_retrieval_never_reembeds_after_model_change_or_vector_tampering(prepared):
    from autoontology.ontology.model import MergedOntology
    from autoontology.playground.service import Playground, PlaygroundError

    store, _, _, _, _ = prepared
    result = publish(prepared)
    model = MergedOntology.from_dict(store.get_snapshot(result.revision).model)

    def forbidden(*args):
        pytest.fail("a publication must never reembed its frozen passages")

    service = Playground(
        store=store,
        model_for=lambda: (result.revision, model),
        embedder_factory=lambda: (forbidden, "test"),
        llm_factory=lambda: Answer(),
    )
    assert service.index(service.scope()).reused > 0
    service.embedder_factory = lambda: (forbidden, "new-model")
    with pytest.raises(PlaygroundError, match="selection changed"):
        service.index(service.scope())
    service.embedder_factory = lambda: (forbidden, "test")
    with store._conn() as conn:
        conn.execute(
            "UPDATE playground_index SET embedding=NULL WHERE scope=?",
            (Publications(store).get(result.revision)["manifest"]["index"]["scope"],),
        )
    with pytest.raises(PlaygroundError, match="index"):
        service.index(service.scope())


def test_publication_cli_and_api_share_verification_and_explicit_legacy_receipts(
    prepared, monkeypatch
):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.web.app import create_app

    store, settings, candidate, _, _ = prepared
    result = publish(prepared)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    output = CliRunner().invoke(
        cli.app, ["ops", "publication", "--revision", str(result.revision), "--verify"]
    )
    assert output.exit_code == 0, output.output
    client = TestClient(create_app(settings))
    response = client.get(f"/api/ops/publications/{result.revision}?verify=true")
    assert response.status_code == 200
    assert response.json() == json.loads(output.output)
    assert client.get(f"/api/ops/publications/{candidate}").status_code == 409
    assert client.get("/api/ops/publications/999").status_code == 404
    assert (
        client.get("/api/ops/publications").json()["items"][0]["id"]
        == Publications(store).get(result.revision)["id"]
    )


def test_download_refuses_changed_publication_and_serves_only_verified_bytes(prepared):
    from fastapi.testclient import TestClient

    from autoontology.web.app import create_app

    _, settings, _, _, _ = prepared
    result = publish(prepared)
    client = TestClient(create_app(settings))
    path = settings.exports_dir / f"r{result.revision:04}" / "ontology.ttl"
    response = client.get(f"/api/exports/{result.revision}/ontology.ttl")
    assert response.status_code == 200 and response.content == path.read_bytes()
    assert response.headers["x-content-type-options"] == "nosniff"
    path.write_text("damaged")
    response = client.get(f"/api/exports/{result.revision}/ontology.ttl")
    assert response.status_code == 409 and response.json()["error"] == "publication_invalid"


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_death_has_one_authoritative_publication_outcome(prepared, after_commit):
    import os
    import subprocess
    import sys
    from pathlib import Path

    store, settings, candidate, validation, _ = prepared
    script = """
import os, sys
from dataclasses import replace
from pathlib import Path
from autoontology.config import Settings
from autoontology.store.db import Store
from autoontology.store.operations import Operations
from autoontology.engine.operations import run_publish
from tests.test_publication_manifest import embed
settings = replace(Settings.from_env(), home=Path(sys.argv[1]), exports_dir=Path(sys.argv[2]))
store = Store.for_home(settings.home)
real = Operations.commit
def die(self, *args, **kwargs):
    if sys.argv[5] == 'True':
        real(self, *args, **kwargs)
    os._exit(73)
Operations.commit = die
run_publish(store, settings, revision=int(sys.argv[3]), token=sys.argv[4], embedder=(embed,'test'))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(settings.home),
            str(settings.exports_dir),
            str(candidate),
            validation["token"],
            str(after_commit),
        ],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 73, child.stderr.decode()
    restarted = Store.for_home(settings.home)
    state = Operations(restarted).state()
    if after_commit:
        head = state["published_revision"]
        assert Publications(restarted).verify(head, settings.exports_dir)["ok"]
        assert restarted.get_run(restarted.get_snapshot(head).run_id).status == "succeeded"
    else:
        assert state["published_revision"] is None
        assert Publications(restarted).listing() == []
        from fastapi.testclient import TestClient

        from autoontology.web.app import create_app

        client = TestClient(create_app(settings))
        assert client.get("/api/ontology").status_code == 404
        assert client.get("/api/exports").json() == {"exports": []}


def test_pre_manifest_operational_history_is_explicitly_unverifiable(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from autoontology.web.app import create_app
    from tests.test_operations import example, snapshot

    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    store = Store.for_home(tmp_path)
    revision = snapshot(store, example())
    Operations(store)
    client = TestClient(create_app(settings))
    receipt = client.get(f"/api/ops/publications/{revision}?verify=true").json()
    assert receipt == {"revision": revision, "legacy": True, "manifest": None}
    assert Publications(store).verify(revision, settings.exports_dir)["ok"] is False


def test_source_input_manifest_tampering_is_detected(prepared):
    store, settings, _, _, capture = prepared
    result = publish(prepared)
    with store._conn() as conn:
        row = conn.execute(
            "SELECT manifest_json FROM source_revisions WHERE id=?", (capture.id,)
        ).fetchone()
        changed = {**json.loads(row[0]), "purpose": "tampered"}
        conn.execute(
            "UPDATE source_revisions SET manifest_json=? WHERE id=?",
            (json.dumps(changed), capture.id),
        )
    with pytest.raises(PublicationError, match="source input"):
        Publications(store).verify(result.revision, settings.exports_dir)


def test_integrity_errors_have_machine_readable_cli_and_http_codes(prepared, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.web.app import create_app

    _, settings, _, _, _ = prepared
    result = publish(prepared)
    (settings.exports_dir / f"r{result.revision:04}" / "ontology.ttl").write_text("broken")
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    output = CliRunner().invoke(
        cli.app, ["ops", "publication", "--revision", str(result.revision), "--verify"]
    )
    assert output.exit_code == 1
    assert json.loads(output.output)["error"] == "publication_invalid"
    response = TestClient(create_app(settings)).get(
        f"/api/ops/publications/{result.revision}?verify=true"
    )
    assert response.status_code == 409 and response.json()["error"] == "publication_invalid"
