# SPDX-License-Identifier: MIT
"""New workspaces require review, representative questions and explicit publication."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.engine.operations import run_publish, validate
from autoontology.store.db import Store
from autoontology.store.operations import Operations, OperationsError, read_snapshot
from autoontology.web.app import create_app
from tests.test_operations import example, snapshot


class CitedAnswer:
    async def stream(self, messages):
        yield "대표이사 승인입니다. [E1]"


@pytest.fixture
def initial(tmp_path):
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    store = Store.for_home(tmp_path)
    ops = Operations(store)
    revision = snapshot(store, example())
    return store, ops, revision, settings


def approve(ops, revision):
    for item in ops.changes():
        ops.decide(item["id"], "accept", candidate=revision, actor="owner", note="Original checked")


def embeddings(texts, kind):
    return [[1.0, 0.0, 0.0] for _ in texts]


def test_first_successful_build_remains_private_until_approved_and_published(initial):
    store, ops, revision, settings = initial
    assert read_snapshot(store) is None
    assert ops.state()["published_revision"] is None
    assert ops.state()["candidate_revision"] == revision
    with pytest.raises(OperationsError, match="approved"):
        ops.compose(revision)
    approve(ops, revision)
    ops.add_check("승인권자는 누구인가요?", "대표이사")
    result = validate(
        store, settings, revision=revision, embedder=(embeddings, "test"), llm=CitedAnswer()
    )
    assert result["ok"] and result["checks_configured"]
    assert read_snapshot(store) is None  # validation is not publication
    published = run_publish(
        store, settings, revision=revision, token=result["token"], embedder=(embeddings, "test")
    )
    assert read_snapshot(store).revision == published.revision
    assert published.revision > revision
    assert (settings.exports_dir / f"r{published.revision:04}" / "export.json").is_file()


def test_missing_questions_fail_before_models_and_cannot_publish(initial):
    store, ops, revision, settings = initial
    approve(ops, revision)

    def forbidden(*args):
        pytest.fail("empty evaluation must not call a model")

    result = validate(store, settings, revision=revision, embedder=(forbidden, "test"))
    assert not result["ok"] and not result["checks_configured"]
    assert result["blocking_reasons"]
    with pytest.raises(OperationsError, match="revalidate"):
        run_publish(
            store, settings, revision=revision, token=result["token"], embedder=(forbidden, "test")
        )
    assert read_snapshot(store) is None


def test_deleted_check_invalidates_validation_and_empty_values_are_rejected(initial):
    store, ops, revision, settings = initial
    approve(ops, revision)
    for question, expected in [("  ", "Expected"), ("Question", "  ")]:
        with pytest.raises(OperationsError, match="non-empty"):
            ops.add_check(question, expected)
    ops.add_check("승인권자는?", "대표이사")
    result = validate(
        store, settings, revision=revision, embedder=(embeddings, "test"), llm=CitedAnswer()
    )
    with store._conn() as conn:
        conn.execute("DELETE FROM ops_checks")
    with pytest.raises(OperationsError, match="revalidate"):
        run_publish(
            store, settings, revision=revision, token=result["token"], embedder=(embeddings, "test")
        )


def test_empty_web_startup_pins_no_head_and_exposes_review_only(tmp_path):
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    client = TestClient(create_app(settings))
    store = Store.for_home(tmp_path)
    revision = snapshot(store, example())
    assert client.get("/api/ontology").status_code == 404
    overview = client.get("/api/ops").json()
    assert overview["published_revision"] is None and overview["candidate_revision"] == revision
    assert overview["initial_approval_required"] is True
    assert overview["publication_policy"]["minimum_questions"] == 1
    assert client.get(f"/api/ontology?revision={revision}").status_code == 200
    assert (
        client.post("/api/runs", json={"kind": "export", "revision": revision}).status_code == 409
    )
    assert (
        client.post("/api/ops/checks", json={"question": " ", "expected": " "}).status_code == 409
    )


def test_cli_initial_review_validation_and_publication(initial, monkeypatch):
    import json

    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.engine import operations

    store, ops, revision, settings = initial
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    real_validate, real_publish = operations.validate, operations.run_publish
    monkeypatch.setattr(
        operations,
        "validate",
        lambda *args, **kw: real_validate(
            *args, **kw, embedder=(embeddings, "test"), llm=CitedAnswer()
        ),
    )
    monkeypatch.setattr(
        operations,
        "run_publish",
        lambda *args, **kw: real_publish(*args, **kw, embedder=(embeddings, "test")),
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ops", "list"])
    assert result.exit_code == 0, result.output
    item = json.loads(result.output)["items"][0]
    result = runner.invoke(
        cli.app,
        [
            "ops",
            "decide",
            item["id"],
            "--revision",
            str(revision),
            "--decision",
            "accept",
            "--note",
            "Original checked",
        ],
    )
    assert result.exit_code == 0, result.output
    blocked = runner.invoke(cli.app, ["ops", "validate", "--revision", str(revision)])
    assert blocked.exit_code == 1 and not json.loads(blocked.output)["ok"]
    assert (
        runner.invoke(cli.app, ["ops", "check", "승인권자는?", "--expected", "대표이사"]).exit_code
        == 0
    )
    result = runner.invoke(cli.app, ["ops", "validate", "--revision", str(revision)])
    assert result.exit_code == 0, result.output
    token = json.loads(result.output)["token"]
    result = runner.invoke(
        cli.app, ["ops", "publish", "--revision", str(revision), "--token", token]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["revision"] == ops.state()["published_revision"]


def test_new_cli_workspace_cannot_implicitly_publish_first_build(tmp_path):
    from autoontology import cli

    store = cli._store(replace(Settings.from_env(), home=tmp_path))
    revision = snapshot(store, example())
    assert read_snapshot(store) is None
    assert Operations(store).state()["candidate_revision"] == revision


def test_cli_checks_cannot_race_an_active_publication(initial, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.engine.build import WORKSPACE_LEASE

    store, ops, _, settings = initial
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    with store.lease([WORKSPACE_LEASE], "publish"):
        result = CliRunner().invoke(cli.app, ["ops", "check", "Question", "--expected", "Expected"])
    assert result.exit_code == 1
    assert not ops.checks()
