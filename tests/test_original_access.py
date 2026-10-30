# SPDX-License-Identifier: MIT
"""UI/API and CLI retrieve exactly the original bound to an ontology revision."""

import hashlib
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology import cli
from autoontology.config import Settings
from autoontology.ontology.model import MergedOntology, OClass, ODocument, OEvidence
from autoontology.store.db import Store
from autoontology.store.operations import Operations
from autoontology.store.provenance import SourceArchive
from autoontology.web.app import create_app


@pytest.fixture
def originals(tmp_path, monkeypatch):
    settings = replace(
        Settings.from_env(), home=tmp_path / "home", exports_dir=tmp_path / "exports"
    )
    store = Store.for_home(settings.home)
    path = tmp_path / "규정.md"
    original = "# 규정\n3억원 이상은 대표이사 승인".encode()
    path.write_bytes(original)
    source = store.add_source(
        name="rules", kind="documents", locator=json.dumps({"path": str(path), "recursive": False})
    )
    archive = SourceArchive(store)
    captured = archive.capture_files(source, [path], run_id="read", root=tmp_path)
    archive.mark_read(captured.id, "read", summary={"documents": 1})
    model = MergedOntology("org", "https://example.test/#", documents_sources=["rules"])
    sha = hashlib.sha256(original).hexdigest()
    model.documents["doc-one"] = ODocument("doc-one", "규정", sha, str(path), "rules")
    model.evidence["ev-one"] = OEvidence("ev-one", "doc-one", "3억원 이상은 대표이사 승인", 1, True)
    model.classes["concept-rule"] = OClass(
        "concept-rule", "승인 기준", "document", derived_from=["ev-one"], documents_source="rules"
    )
    run = store.create_run(kind="build", source_id=None)
    first = store.save_snapshot(run.id, model)
    store.update_run(run.id, status="succeeded")
    ops = Operations(store)  # existing workspace migration baseline
    path.write_text("Current content no longer matches the old original")
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    return store, settings, first, model, archive, original, ops


def test_api_lists_revision_bound_originals_and_downloads_old_bytes(originals):
    store, settings, first, model, archive, original, _ = originals
    client = TestClient(create_app(settings))
    result = client.get("/api/ontology/documents")
    assert result.status_code == 200
    data = result.json()
    assert data["revision"] == first
    item = data["items"][0]
    assert item["original"]["status"] == "archived"
    response = client.get(item["original"]["url"])
    assert response.status_code == 200 and response.content == original
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-original-sha256"] == model.documents["doc-one"].sha256
    assert "attachment" in response.headers["content-disposition"]
    detail = client.get("/api/ontology/concepts/concept-rule").json()
    assert detail["evidence"][0]["original"]["url"] == item["original"]["url"]
    revisions = client.get("/api/sources/rules/revisions").json()["items"]
    assert revisions[0]["id"] == archive.reference(store.get_source("rules").id)
    assert str(settings.home) not in json.dumps(data)


def test_api_and_cli_never_read_the_live_file_when_original_missing_or_corrupt(originals, tmp_path):
    _, settings, _, model, archive, _, _ = originals
    client = TestClient(create_app(settings))
    doc = model.documents["doc-one"]
    saved = archive.original("rules", doc.sha256, "규정.md")
    saved.chmod(0o600)
    saved.write_bytes(b"Tampered")
    response = client.get("/api/ontology/documents/doc-one/original")
    assert response.status_code == 409 and response.json()["error"] == "original_corrupt"
    saved.unlink()
    response = client.get("/api/ontology/documents/doc-one/original")
    assert response.status_code == 404 and response.json()["error"] == "original_unavailable"
    out = tmp_path / "download.md"
    result = CliRunner().invoke(cli.app, ["ops", "original", "doc-one", "--out", str(out)])
    assert result.exit_code == 1 and not out.exists()
    assert json.loads(result.output)["error"] == "original_unavailable"


def test_cli_inspect_download_and_source_revisions_match_api(originals, tmp_path):
    _, _, first, _, _, original, _ = originals
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ops", "documents"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["revision"] == first
    result = runner.invoke(cli.app, ["ops", "source-revisions", "rules"])
    assert result.exit_code == 0 and len(json.loads(result.output)["items"]) == 1
    target = tmp_path / "download.md"
    result = runner.invoke(cli.app, ["ops", "original", "doc-one", "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == original
    refused = runner.invoke(cli.app, ["ops", "original", "doc-one", "--out", str(target)])
    assert refused.exit_code == 1 and target.read_bytes() == original


def test_candidate_originals_need_explicit_cli_preview_and_legacy_is_unavailable(originals):
    store, settings, _, model, _, _, _ = originals
    model.documents["old-missing"] = ODocument(
        "old-missing", "Legacy", "legacy-sha", "/private/legacy.md", "rules"
    )
    run = store.create_run(kind="build", source_id=None)
    candidate = store.save_snapshot(run.id, model)
    store.update_run(run.id, status="succeeded")
    runner = CliRunner()
    assert runner.invoke(cli.app, ["ops", "documents", "--revision", str(candidate)]).exit_code == 1
    result = runner.invoke(cli.app, ["ops", "documents", "--revision", str(candidate), "--preview"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["preview"] is True
    client = TestClient(create_app(settings))
    data = client.get(f"/api/ontology/documents?revision={candidate}").json()
    old = next(item for item in data["items"] if item["id"] == "old-missing")
    assert old["original"]["status"] == "not_archived"
    assert (
        client.get(f"/api/ontology/documents/old-missing/original?revision={candidate}").status_code
        == 404
    )
    assert client.get("/api/ontology/documents/unknown/original").status_code == 404


def test_preserve_original_only_recovers_a_matching_registered_file(originals, tmp_path):
    from pathlib import Path

    store, settings, first, model, archive, original, ops = originals
    doc = model.documents["doc-one"]
    archive.original("rules", doc.sha256, "규정.md").unlink()
    client = TestClient(create_app(settings))
    response = client.post(f"/api/ontology/documents/doc-one/archive?revision={first}")
    assert response.status_code == 409 and response.json()["error"] == "original_changed"
    assert ops.state()["published_revision"] == first
    Path(doc.path).write_bytes(original)
    response = client.post(f"/api/ontology/documents/doc-one/archive?revision={first}")
    assert response.status_code == 200, response.text
    assert response.json()["original"]["status"] == "archived"
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ops", "preserve-original", "doc-one"])
    assert result.exit_code == 0 and json.loads(result.output)["original"]["status"] == "archived"
    assert store.get_snapshot(first).model == model.to_dict()
    assert client.get(response.json()["original"]["url"]).content == original


def test_recovery_finds_identical_bytes_after_the_registered_source_moves_and_renames(
    originals, tmp_path
):
    from pathlib import Path

    store, settings, first, model, archive, original, _ = originals
    doc = model.documents["doc-one"]
    archive.original("rules", doc.sha256, "규정.md").unlink()
    Path(doc.path).unlink()
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "renamed.md").write_bytes(original)
    with store._conn() as conn:
        conn.execute(
            "UPDATE sources SET locator=? WHERE name='rules'",
            (json.dumps({"path": str(moved), "recursive": False}),),
        )
    client = TestClient(create_app(settings))
    response = client.post(f"/api/ontology/documents/doc-one/archive?revision={first}")
    assert response.status_code == 200, response.text
    assert response.json()["original"]["status"] == "archived"
    assert client.get(response.json()["original"]["url"]).content == original
