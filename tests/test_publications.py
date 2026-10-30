# SPDX-License-Identifier: MIT
"""All default consumers use the same operational snapshot, with explicit previews."""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from autoontology import cli
from autoontology.config import Settings
from autoontology.engine import export_job
from autoontology.ontology.model import MergedOntology, OClass, ODocument, OEvidence
from autoontology.store.db import Store
from autoontology.store.operations import Operations, OperationsError, read_snapshot


def snapshot(store, label="Published", *, complete=True):
    model = MergedOntology("org", "https://example.test/ontology#", documents_sources=["rules"])
    model.documents["doc"] = ODocument("doc", "Policy", label, "/policy.md", "rules")
    model.evidence["ev"] = OEvidence("ev", "doc", label, 1, True)
    model.classes["term-one"] = OClass(
        "term-one", label, "document", derived_from=["ev"], documents_source="rules"
    )
    run = store.create_run(kind="build", source_id=None)
    revision = store.save_snapshot(run.id, model)
    if complete:
        store.update_run(run.id, status="succeeded")
    return revision


def workspace(tmp_path):
    store = Store.for_home(tmp_path)
    first = snapshot(store)
    ops = Operations(store)
    candidate = snapshot(store, "Unreviewed")
    return store, ops, first, candidate


def test_default_and_explicit_reads_respect_publication(tmp_path):
    store, ops, first, candidate = workspace(tmp_path)
    assert read_snapshot(store).revision == first
    with pytest.raises(OperationsError, match="unpublished"):
        read_snapshot(store, candidate)
    assert read_snapshot(store, candidate, preview=True).revision == candidate
    assert read_snapshot(store, first).revision == first
    with pytest.raises(OperationsError, match="explicit revision"):
        read_snapshot(store, preview=True)
    assert ops.state()["published_revision"] == first


def test_legacy_reads_do_not_activate_operations(tmp_path):
    store = Store.for_home(tmp_path)
    snapshot(store)
    latest = snapshot(store, "Legacy latest")
    assert read_snapshot(store).revision == latest
    with store._conn() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ops_state'").fetchone()


def test_first_completed_baseline_is_shared_and_incomplete_snapshots_stay_private(tmp_path):
    store = Store.for_home(tmp_path)
    ops = Operations(store)
    incomplete = snapshot(store, complete=False)
    assert read_snapshot(store) is None
    first = snapshot(store)
    snapshot(store, "Candidate")
    assert read_snapshot(store).revision == first
    assert ops.state()["published_revision"] == first
    with pytest.raises(OperationsError, match="unpublished"):
        read_snapshot(store, incomplete)


def test_missing_publication_state_never_falls_back_to_candidate(tmp_path):
    store, _, _, candidate = workspace(tmp_path)
    with store._conn() as conn:
        conn.execute("DELETE FROM ops_state")
    assert read_snapshot(store) is None
    with pytest.raises(OperationsError, match="unpublished"):
        read_snapshot(store, candidate)


def test_missing_published_snapshot_never_falls_back_to_candidate(tmp_path):
    store, _, _, _ = workspace(tmp_path)
    with store._conn() as conn:
        conn.execute("UPDATE ops_state SET published_revision=999 WHERE id=1")
    with pytest.raises(OperationsError, match="published snapshot is missing"):
        read_snapshot(store)


def test_web_graph_index_sparql_and_export_guards_share_the_published_head(tmp_path, isolated_env):
    from fastapi.testclient import TestClient

    from autoontology.web.app import create_app

    store, _, first, candidate = workspace(tmp_path)
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    client = TestClient(create_app(settings))
    assert client.get("/api/ontology").json()["revision"] == first
    graph = client.get("/api/ontology/graph").json()
    assert graph["revision"] == first
    assert graph["nodes"][0]["label"] == "Published"
    assert client.get("/api/playground/index").json()["revision"] == first
    result = client.post(
        "/api/playground/sparql",
        json={
            "query": "SELECT ?name WHERE { ?c <http://www.w3.org/2000/01/rdf-schema#label> ?name }"
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["revision"] == first
    assert "Published" in result.text and "Unreviewed" not in result.text
    assert (
        client.post("/api/runs", json={"kind": "export", "revision": candidate}).status_code == 409
    )
    # Explicit read-only candidate inspection remains available to the local operator.
    assert client.get(f"/api/ontology?revision={candidate}").json()["revision"] == candidate
    assert store.latest_revision() == candidate


def test_cli_default_and_preview(tmp_path, monkeypatch):
    store, ops, first, candidate = workspace(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda: Settings(home=tmp_path, workspace="org"))
    monkeypatch.setattr(cli, "_store", lambda settings: store)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ontology"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith(f"revision {first} ")
    denied = runner.invoke(cli.app, ["ontology", "--revision", str(candidate)])
    assert denied.exit_code == 1 and "unpublished" in denied.output
    preview = runner.invoke(cli.app, ["ontology", "--revision", str(candidate), "--preview"])
    assert preview.exit_code == 0, preview.output
    assert f"revision {candidate} " in preview.output and "PREVIEW" in preview.output
    assert ops.state()["published_revision"] == first


def test_export_default_candidate_guard_and_private_preview(tmp_path, isolated_env):
    store, ops, first, candidate = workspace(tmp_path)
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    # A minimal document-only ontology avoids touching any upstream service.
    result = export_job.run_export(store, settings, format="okf")
    marker = json.loads((result.export_dir / "export.json").read_text())
    assert marker["revision"] == first
    count = len(store.list_runs())
    with pytest.raises(export_job.JobError, match="unpublished"):
        export_job.run_export(store, settings, revision=candidate, format="okf")
    with pytest.raises(export_job.JobError, match="outside"):
        export_job.run_export(store, settings, revision=candidate, preview=True, format="okf")
    with pytest.raises(export_job.JobError, match="outside"):
        export_job.run_export(
            store,
            settings,
            revision=candidate,
            preview=True,
            out=settings.exports_dir / "hidden",
            format="okf",
        )
    redirect = tmp_path / "redirect"
    redirect.symlink_to(settings.exports_dir, target_is_directory=True)
    with pytest.raises(export_job.JobError, match="outside"):
        export_job.run_export(
            store, settings, revision=candidate, preview=True, out=redirect, format="okf"
        )
    assert len(store.list_runs()) == count
    preview = export_job.run_export(
        store, settings, revision=candidate, preview=True, out=tmp_path / "previews", format="okf"
    )
    meta = json.loads((preview.export_dir / "export.json").read_text())
    assert meta["revision"] == candidate and meta["preview"] is True
    assert not (settings.exports_dir / f"r{candidate:04}").exists()
    assert ops.state()["published_revision"] == first
