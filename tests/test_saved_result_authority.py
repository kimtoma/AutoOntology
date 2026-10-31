# SPDX-License-Identifier: MIT
"""A valid login must not expose another principal's saved questions or results."""

import json

import pytest

from autoontology.playground.store import PlaygroundStore
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity
from autoontology.store.traces import UsageTraces
from tests.test_playground import _sse
from tests.test_playground import client as playground_client  # noqa: F401


@pytest.fixture
def managed(playground_client):  # noqa: F811
    client, settings, app, llm, embed = playground_client
    service = Identity(app.state.api.store)
    owner_token = service.bootstrap(
        "Private results", "owner", "Owner", "result owner test password"
    )
    owner = service.authenticate(owner_token.secret.reveal())
    tokens = {}
    for username in ("alice", "bob"):
        service.create_human(
            owner, username, username.title(), "result reader test password", ["viewer"]
        )
        tokens[username] = service.login(username, "result reader test password")
    return client, settings, app, llm, service, owner, owner_token, tokens


def headers(token):
    return {"Authorization": "Bearer " + token.secret.reveal()}


def ask(client, token):
    response = client.post(
        "/api/playground/chat",
        json={"question": "Private contract question"},
        headers=headers(token),
    )
    assert response.status_code == 200
    events = dict(_sse(response.text))
    assert "done" in events
    return events["done"]


def test_other_reader_cannot_list_read_continue_or_delete_a_saved_question(managed):
    client, _, _, llm, _, _, _, tokens = managed
    result = ask(client, tokens["alice"])
    before = len(llm.messages)
    for path in ("/api/playground/traces", "/api/playground/conversations"):
        response = client.get(path, headers=headers(tokens["bob"]))
        assert response.status_code == 200
        assert (
            result["trace_id"] not in response.text
            and result["conversation_id"] not in response.text
        )
    for path in (
        f"/api/playground/traces/{result['trace_id']}",
        f"/api/playground/conversations/{result['conversation_id']}",
    ):
        response = client.get(path, headers=headers(tokens["bob"]))
        assert response.status_code == 404
        response = client.delete(path, headers=headers(tokens["bob"]))
        assert response.status_code == 404
    response = client.post(
        "/api/playground/chat",
        json={"question": "Continue", "conversation_id": result["conversation_id"]},
        headers=headers(tokens["bob"]),
    )
    assert response.status_code == 404 and len(llm.messages) == before
    response = client.get(
        f"/api/playground/traces/{result['trace_id']}", headers=headers(tokens["alice"])
    )
    assert response.status_code == 200
    assert (
        client.delete(
            f"/api/playground/traces/{result['trace_id']}", headers=headers(tokens["alice"])
        ).status_code
        == 200
    )


def test_admin_inspection_is_explicit_and_does_not_grant_another_persons_conversation(managed):
    client, _, _, _, _, _, owner, tokens = managed
    result = ask(client, tokens["alice"])
    response = client.get("/api/playground/traces", headers=headers(owner))
    assert result["trace_id"] not in response.text
    response = client.get("/api/playground/traces?all_users=true", headers=headers(owner))
    assert result["trace_id"] in response.text
    response = client.get("/api/playground/traces?all_users=true", headers=headers(tokens["alice"]))
    assert response.status_code == 403
    response = client.get(f"/api/playground/traces/{result['trace_id']}", headers=headers(owner))
    assert response.status_code == 200
    response = client.post(
        "/api/playground/chat",
        json={"question": "Admin follow-up", "conversation_id": result["conversation_id"]},
        headers=headers(owner),
    )
    assert response.status_code == 404


def test_common_store_methods_do_not_bypass_http_ownership(managed):
    client, _, app, _, service, _, _, tokens = managed
    result = ask(client, tokens["alice"])
    store = app.state.api.store
    with acting_as(store, service.authenticate(tokens["bob"].secret.reveal())):
        for call in (
            lambda: UsageTraces(store).get(result["trace_id"]),
            lambda: UsageTraces(store).delete(result["trace_id"]),
            lambda: PlaygroundStore(store).messages(result["conversation_id"]),
            lambda: PlaygroundStore(store).add_message(result["conversation_id"], "user", "forged"),
            lambda: PlaygroundStore(store).delete_conversation(result["conversation_id"]),
        ):
            with pytest.raises(AccessError):
                call()


def test_retention_is_per_owner_and_cannot_prune_someone_elses_history(managed, monkeypatch):
    _, _, app, _, service, _, _, tokens = managed
    import autoontology.playground.store as module

    monkeypatch.setattr(module, "MAX_CONVERSATIONS", 1)
    store = app.state.api.store
    pg = PlaygroundStore(store)
    with acting_as(store, service.authenticate(tokens["alice"].secret.reveal())):
        alice = pg.create_conversation("workspace", "Alice private")
        pg.add_message(alice, "user", "Private text")
    with acting_as(store, service.authenticate(tokens["bob"].secret.reveal())):
        pg.create_conversation("workspace", "Bob old")
        pg.create_conversation("workspace", "Bob new")
        assert len(pg.list_conversations("workspace")) == 1
    with acting_as(store, service.authenticate(tokens["alice"].secret.reveal())):
        assert pg.get_conversation(alice)["title"] == "Alice private"


def test_revoked_stream_stops_before_more_text_or_a_successful_receipt(managed):
    client, _, app, llm, service, owner, _, tokens = managed

    async def revoked(messages):
        yield "first allowed text"
        service.revoke_credential(owner, tokens["alice"].credential_id)
        yield "secret after revocation"

    llm.stream = revoked
    response = client.post(
        "/api/playground/chat", json={"question": "Private"}, headers=headers(tokens["alice"])
    )
    events = dict(_sse(response.text))
    assert "secret after revocation" not in response.text and "done" not in events
    assert events["error"]["status"] == 401
    with acting_as(app.state.api.store, owner):
        trace = UsageTraces(app.state.api.store).get(events["status"]["trace_id"])
        assert trace["status"] == "failed" and trace["data"]["outcome"]["partial"]
        assert "secret after revocation" not in json.dumps(trace)


def test_revocation_cancels_a_provider_that_has_not_returned_another_token(managed):
    import time

    import anyio

    client, _, app, llm, service, owner, _, tokens = managed
    closed = []

    async def waiting(messages):
        service.revoke_credential(owner, tokens["alice"].credential_id)
        try:
            await anyio.sleep(3)
            yield "late private text"
        finally:
            closed.append(True)

    llm.stream = waiting
    start = time.monotonic()
    response = client.post(
        "/api/playground/chat", json={"question": "Wait"}, headers=headers(tokens["alice"])
    )
    assert time.monotonic() - start < 2
    assert closed and "late private text" not in response.text
    events = dict(_sse(response.text))
    assert events["error"]["status"] == 401 and "done" not in events
    with acting_as(app.state.api.store, owner):
        trace = UsageTraces(app.state.api.store).get(events["status"]["trace_id"])
        assert (
            trace["status"] == "failed"
            and trace["data"]["outcome"]["error"] == "credential_invalid"
        )


def test_sql_result_file_is_owned_and_revocation_cancels_the_read_executor(managed):
    import anyio

    from tests.test_sql_service import attach

    client, settings, app, llm, service, owner, _, tokens = managed
    sql, planner, executor = attach((client, settings, app, llm, None))
    response = client.post(
        "/api/playground/sql",
        json={"question": "Count customers"},
        headers=headers(tokens["alice"]),
    )
    result = dict(_sse(response.text))["done"]
    response = client.get(
        f"/api/playground/traces/{result['trace_id']}/result.csv", headers=headers(tokens["bob"])
    )
    assert response.status_code == 404
    response = client.get(
        f"/api/playground/traces/{result['trace_id']}/result.csv", headers=headers(tokens["alice"])
    )
    assert response.status_code == 200 and "40" in response.text
    closed = []

    async def waiting(*args):
        service.revoke_credential(owner, tokens["alice"].credential_id)
        try:
            await anyio.sleep(3)
            return {"columns": ["secret"], "rows": [[12345]], "truncated": False}
        except anyio.get_cancelled_exc_class():
            closed.append("cancelled")
            raise
        finally:
            closed.append("closed")

    executor.run = waiting
    response = client.post(
        "/api/playground/sql", json={"question": "Read"}, headers=headers(tokens["alice"])
    )
    events = dict(_sse(response.text))
    assert (
        closed == ["cancelled", "closed"] and "12345" not in response.text and "done" not in events
    )
    assert events["error"]["status"] == 401
    with acting_as(app.state.api.store, owner):
        trace = sql.traces.get(events["meta"]["trace_id"])
        assert trace["status"] == "failed"


def test_a_different_producer_instance_cannot_rewrite_a_running_request(managed):
    _, _, app, _, service, _, _, tokens = managed
    pg = app.state.playground
    store = app.state.api.store
    with acting_as(store, service.authenticate(tokens["alice"].secret.reveal())):
        scope = pg.scope()
        producer = UsageTraces(store)
        trace = producer.begin(
            kind="retrieval",
            workspace=scope.key,
            revision=scope.revision,
            model=scope.model,
            request={"question": "test"},
        )
        other = UsageTraces(store)
        for mutate in (
            lambda: other.context(trace, {"forged": True}),
            lambda: other.finish(trace, "failed", {"forged": True}),
        ):
            with pytest.raises(AccessError) as error:
                mutate()
            assert error.value.code == "trace_producer_required"
        producer.finish(trace, "succeeded", {"hit_count": 0})


def test_v12_migration_preserves_receipt_hashes_and_does_not_invent_conversation_owners(
    tmp_path, monkeypatch
):
    import os
    import uuid

    from autoontology.store import db
    from autoontology.store.operations import digest

    path = tmp_path / "v12.db"
    trace_ids = []
    with monkeypatch.context() as previous:
        previous.setattr(db, "MIGRATIONS", [m for m in db.MIGRATIONS if m[0] <= 12])
        old = db.Store(path)
        identity = Identity(old)
        issued = identity.bootstrap("Before v13", "owner", "Owner", "migration owner test password")
        actor = identity.authenticate(issued.secret.reveal())
        for verified in (True, False):
            trace_id = uuid.uuid4().hex
            trace_ids.append(trace_id)
            author = (
                {
                    "id": actor.principal_id,
                    "verified": True,
                    "workspace_id": identity.status()["workspace_id"],
                }
                if verified
                else {"id": "local-unverified", "verified": False}
            )
            record = {
                "id": trace_id,
                "workspace": "workspace",
                "kind": "chat",
                "revision": 1,
                "publication_id": None,
                "status": "succeeded",
                "conversation_id": None,
                "owner_pid": os.getpid(),
                "started_at": "2026-10-31T00:00:00Z",
                "finished_at": "2026-10-31T00:00:01Z",
                "data": {"actor": author, "outcome": {"answer": "old answer"}},
            }
            with old._conn() as conn:
                conn.execute(
                    "INSERT INTO usage_traces VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *[
                            record[k]
                            for k in (
                                "id",
                                "workspace",
                                "kind",
                                "revision",
                                "publication_id",
                                "status",
                                "conversation_id",
                                "owner_pid",
                                "started_at",
                                "finished_at",
                            )
                        ],
                        json.dumps(record["data"]),
                        digest(record),
                    ),
                )
        conversation = uuid.uuid4().hex
        with old._conn() as conn:
            conn.execute(
                "INSERT INTO playground_conversations VALUES(?,?,?,?,?)",
                (conversation, "workspace", "Legacy question", "then", "then"),
            )
            before = [
                tuple(row)
                for row in conn.execute("SELECT id,data_json,sha256 FROM usage_traces ORDER BY id")
            ]
    restored = db.Store(path)
    with restored._conn() as conn:
        assert [
            tuple(row)
            for row in conn.execute("SELECT id,data_json,sha256 FROM usage_traces ORDER BY id")
        ] == before
    with acting_as(restored, actor):
        traces = UsageTraces(restored)
        assert traces.get(trace_ids[0])["data"]["outcome"]["answer"] == "old answer"
        assert {t["id"] for t in traces.listing(workspace="workspace")} == {trace_ids[0]}
        assert len(traces.listing(workspace="workspace", all_users=True)) == 2
        assert PlaygroundStore(restored).list_conversations("workspace") == []
        legacy = PlaygroundStore(restored).list_conversations("workspace", all_users=True)
        assert legacy[0]["id"] == conversation and legacy[0]["principal_id"] is None


@pytest.mark.parametrize("watch", [False, True])
def test_native_revocation_keeps_the_request_id_without_reading_with_a_revoked_credential(
    managed, monkeypatch, watch
):
    import httpx
    from typer.testing import CliRunner

    from autoontology.cli import app as cli_app
    from autoontology.security.credential_file import save_credential

    client, settings, _, llm, service, owner, _, tokens = managed
    folder = settings.home / "security"
    folder.mkdir(mode=0o700, exist_ok=True)
    save_credential(
        folder / "cli-session.json",
        tokens["alice"],
        workspace_id=service.status()["workspace_id"],
        origin=settings.service_url,
    )
    reads = []
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: reads.append(args))
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda method, url, **kwargs: client.stream(
            method, url, json=kwargs.get("json"), headers=kwargs.get("headers")
        ),
    )

    async def revoked(messages):
        yield "Allowed"
        service.revoke_credential(owner, tokens["alice"].credential_id)
        yield "Denied"

    llm.stream = revoked
    result = CliRunner().invoke(cli_app, ["ops", "ask", "test", *(["--watch"] if watch else [])])
    assert result.exit_code == 1 and not reads
    data = (
        json.loads(result.stdout.splitlines()[-1])["data"] if watch else json.loads(result.stdout)
    )
    assert (
        data["status"] == 401
        and data["error"] == "credential_invalid"
        and len(data["trace_id"]) == 32
    )
