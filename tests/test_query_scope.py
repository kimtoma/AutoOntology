# SPDX-License-Identifier: MIT
"""Query preparation, saved results and conversation scope share current authority."""

import json

import pytest

from autoontology.playground.service import Playground
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.security.query_scope import capture_scope, require_scope
from autoontology.security.views import knowledge_view
from autoontology.store.traces import UsageTraces
from tests import test_domain_views as domain_cases

scoped = domain_cases.scoped


@pytest.fixture
def anyio_backend():
    return "asyncio"


class LocalAnswer:
    provider, model = "controlled-local", "scoped-answer"

    def __init__(self):
        self.messages = []

    async def stream(self, messages):
        self.messages.append(messages)
        assert "PRIVATE" not in json.dumps(messages)
        yield "계약 승인 근거입니다. [E1]"


def playground(store, revision, llm=None):
    llm = llm or LocalAnswer()
    return Playground(
        store,
        model_for=lambda: (revision, knowledge_model(store, revision)),
        llm_factory=lambda: llm,
        embedder_factory=lambda: (
            lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts],
            "controlled",
        ),
    )


def knowledge_model(store, revision):
    from autoontology.ontology.model import MergedOntology

    return MergedOntology.from_dict(store.get_snapshot(revision).model)


def test_scope_binding_is_a_resource_subset_not_just_an_ownership_claim(scoped):
    store, identity, owner, viewer, _, hr, sources, revision = scoped
    with acting_as(store, viewer):
        selected = knowledge_view(store, store.get_snapshot(revision), action="query.run")
        binding = capture_scope(store, revision, selected.model)
        require_scope(store, binding, "results.read")
        assert "PRIVATE" not in json.dumps(binding)
    identity.assign_source(owner, sources[1].id, hr["id"])
    with acting_as(store, viewer), pytest.raises(AccessError):
        require_scope(store, binding, "results.read")


def test_more_permission_keeps_an_older_narrow_result_readable(scoped):
    store, identity, owner, viewer, finance, hr, _, revision = scoped
    with acting_as(store, viewer):
        selected = knowledge_view(store, store.get_snapshot(revision), action="query.run")
        binding = capture_scope(store, revision, selected.model)
    identity.set_roles(owner, viewer.principal_id, ["viewer"], domains=[finance["id"], hr["id"]])
    with acting_as(store, viewer):
        require_scope(store, binding, "results.read")


@pytest.mark.anyio
async def test_scoped_chat_never_sends_private_context_and_scope_survives_trace_deletion(scoped):
    store, identity, owner, viewer, _, hr, sources, revision = scoped
    answer = LocalAnswer()
    service = playground(store, revision, answer)
    with acting_as(store, viewer):
        selected = service.scope()
        assert "term-mixed" not in selected.model.classes
        events = [event async for event in service.ask(selected, "계약 승인 근거")]
        done = next(event["data"] for event in events if event["event"] == "done")
        trace_id, cid = done["trace_id"], done["conversation_id"]
        receipt = service.traces.get(trace_id)
        assert receipt["data"]["binding"]["knowledge"]["status"] == "recorded"
        assert "PRIVATE" not in json.dumps(receipt)
        assert service.traces.delete(trace_id)
        assert len(service.pg.messages(cid)) == 2
    identity.assign_source(owner, sources[1].id, hr["id"])
    with acting_as(store, viewer):
        assert not service.pg.list_conversations(selected.key)
        with pytest.raises(AccessError):
            service.pg.messages(cid)
        assert service.pg.delete_conversation(cid)


@pytest.mark.anyio
async def test_policy_drift_stops_a_query_even_while_its_general_query_grant_remains(scoped):
    store, identity, owner, viewer, _, hr, sources, revision = scoped
    service = playground(store, revision)
    with acting_as(store, viewer):
        selected = service.scope()
        trace_id = None
        with pytest.raises(AccessError):
            async for event in service.ask(selected, "계약 승인 근거"):
                trace_id = event["data"].get("trace_id", trace_id)
                if event["event"] == "delta":
                    identity.assign_source(owner, sources[1].id, hr["id"])
        assert trace_id
        with pytest.raises(AccessError):
            UsageTraces(store).get(trace_id)
    with acting_as(store, owner):
        assert UsageTraces(store).get(trace_id)["status"] == "failed"


def test_full_and_scoped_index_caches_do_not_share_private_elements(scoped):
    store, _, owner, viewer, _, _, _, revision = scoped
    service = playground(store, revision)
    with acting_as(store, owner):
        full = service.index(service.scope())
        assert any("PRIVATE" in e.text for e in full.elements)
    with acting_as(store, viewer):
        limited = service.index(service.scope())
        assert all("PRIVATE" not in e.text for e in limited.elements)
        assert len(limited.elements) < len(full.elements)


def test_unknown_legacy_records_and_diagnostics_cannot_become_scoped_saved_results(scoped):
    store, identity, owner, viewer, finance, hr, _, revision = scoped
    from autoontology.ontology.model import OClass

    full = knowledge_model(store, revision)
    full.classes["unknown"] = OClass("unknown", "PRIVATE_UNKNOWN", "document")
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET model_json=? WHERE revision=?",
            (json.dumps(full.to_dict()), revision),
        )
    with acting_as(store, owner):
        binding = capture_scope(store, revision, full)
    identity.set_roles(owner, viewer.principal_id, ["viewer"], domains=[finance["id"], hr["id"]])
    with acting_as(store, viewer), pytest.raises(AccessError, match="outside current"):
        require_scope(store, binding, "results.read")


@pytest.mark.anyio
async def test_message_tampering_is_not_a_valid_result_even_after_trace_deletion(scoped):
    store, _, _, viewer, _, _, _, revision = scoped
    service = playground(store, revision)
    with acting_as(store, viewer):
        events = [e async for e in service.ask(service.scope(), "계약")]
        done = next(e["data"] for e in events if e["event"] == "done")
        service.traces.delete(done["trace_id"])
        with store._conn() as conn:
            conn.execute(
                "UPDATE playground_messages SET content='Changed' WHERE conversation_id=? AND role='assistant'",
                (done["conversation_id"],),
            )
        with pytest.raises(AccessError) as error:
            service.pg.messages(done["conversation_id"])
        assert error.value.code == "result_scope_invalid"


def test_http_retrieval_chat_history_csv_and_sparql_share_current_scope(
    scoped, isolated_env, monkeypatch
):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from autoontology.config import Settings
    from autoontology.web.app import create_app
    from tests.test_playground import _sse

    store, identity, owner, viewer, _, hr, sources, revision = scoped
    settings = replace(
        Settings.from_env(),
        workspace="org",
        home=store.path.parent,
        exports_dir=store.path.parent / "exports",
    )
    app = create_app(settings)
    answer = LocalAnswer()
    app.state.playground = playground(store, revision, answer)
    issued = identity.login("finance", "long finance test password")
    headers = {"Authorization": "Bearer " + issued.secret.reveal()}
    with TestClient(app) as client:
        for path in (
            "/api/playground/index",
            "/api/playground/traces",
            "/api/playground/conversations",
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert "PRIVATE" not in response.text
        response = client.post(
            "/api/playground/retrieve", headers=headers, json={"question": "계약"}
        )
        assert response.status_code == 200 and "PRIVATE" not in response.text, response.text
        response = client.post(
            "/api/playground/chat", headers=headers, json={"question": "계약 승인"}
        )
        events = _sse(response.text)
        assert "error" not in dict(events), events
        done = dict(events)["done"]
        trace_id, cid = done["trace_id"], done["conversation_id"]
        assert (
            client.get(f"/api/playground/conversations/{cid}", headers=headers).status_code == 200
        )
        with acting_as(store, viewer):
            traces = UsageTraces(store)
            sql_id = traces.begin(
                kind="sql",
                workspace="org",
                revision=revision,
                model=app.state.playground.scope().model,
                request={"question": "controlled saved rows"},
            )
            traces.finish(sql_id, "succeeded", {"columns": ["approved"], "rows": [["계약"]]})
        download = client.get(f"/api/playground/traces/{sql_id}/result.csv", headers=headers)
        assert download.status_code == 200 and "계약" in download.content.decode("utf-8-sig")
        query = 'SELECT ?s ?p ?o WHERE { ?s ?p ?o FILTER(CONTAINS(STR(?o), "PRIVATE")) }'
        response = client.post("/api/playground/sparql", headers=headers, json={"query": query})
        assert response.status_code == 200 and not response.json()["rows"], response.text
        identity.assign_source(owner, sources[1].id, hr["id"])
        for path in (
            f"/api/playground/traces/{trace_id}",
            f"/api/playground/traces/{sql_id}/result.csv",
            f"/api/playground/conversations/{cid}",
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 404 and "계약 승인" not in response.text, response.text
        assert not client.get("/api/playground/conversations", headers=headers).json()[
            "conversations"
        ]
        assert (
            client.delete(f"/api/playground/traces/{trace_id}", headers=headers).status_code == 200
        )
        assert (
            client.delete(f"/api/playground/conversations/{cid}", headers=headers).status_code
            == 200
        )


@pytest.mark.anyio
async def test_waiting_provider_is_cancelled_after_domain_move_without_waiting_for_text(scoped):
    from contextlib import ExitStack

    import anyio

    from autoontology.web.playground_api import _guarded

    store, identity, owner, viewer, _, hr, sources, revision = scoped
    stopped = []

    async def waiting():
        try:
            await anyio.sleep(30)
            yield {"event": "done", "data": {}}
        finally:
            stopped.append(True)

    async def move():
        await anyio.sleep(0.15)
        identity.assign_source(owner, sources[1].id, hr["id"])

    with acting_as(store, viewer):
        model = playground(store, revision).scope().model
        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(move)
                events = [
                    e async for e in _guarded(ExitStack(), waiting(), store=store, model=model)
                ]
        assert stopped and events[-1]["event"] == "error"
        assert json.loads(events[-1]["data"])["status"] == 409
