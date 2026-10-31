# SPDX-License-Identifier: MIT
"""Processing records survive source changes and never invent historical settings."""

import json
from dataclasses import replace

import pytest

from autoontology.engine import build, extract
from autoontology.store.documents import DocumentStore
from autoontology.store.processing import Processing, ProcessingError
from tests import test_extract_job as fixtures

env = fixtures.env


def test_read_extract_and_consolidation_record_real_inputs_and_outputs(env):
    settings, store, folder = env
    assert (
        fixtures._run(settings, store, folder, fixtures._backends(settings)).status == "succeeded"
    )
    source = store.get_source("hanbit_docs")
    records = Processing(store).listing(source.name)
    assert {r["record"]["stage"] for r in records} == {"read", "extract", "consolidate"}
    row = next(r for r in records if r["record"]["stage"] == "extract")
    assert row["record"]["input"]["text"]
    assert row["record"]["configuration"]["schema_sha256"]
    assert row["record"]["messages"]["human"]
    assert row["record"]["output"]["concepts"]
    assert row["record"]["backend"]["status"] == "unverified"
    for receipt in records:
        if receipt["record"]["stage"] == "extract":
            for invocation in receipt["record"]["invocations"]:
                assert receipt["record"]["input"]["text"] in invocation["request"]["human"]
    assert Processing(store).get(row["id"]) == row


def test_same_configuration_reuses_receipts_without_model_work(env):
    settings, store, folder = env
    first = fixtures._run(settings, store, folder, fixtures._backends(settings))
    before = Processing(store).listing("hanbit_docs")
    second = fixtures._run(
        settings, store, folder, fixtures._backends(settings, llm=fixtures.Boom())
    )
    assert first.status == second.status == "succeeded"
    assert Processing(store).listing("hanbit_docs") == before


def test_model_selection_change_reextracts_and_keeps_old_results(env):
    settings, store, folder = env
    first = fixtures._backends(settings)
    fixtures._run(settings, store, folder, first)
    old = Processing(store).listing("hanbit_docs")
    changed = replace(settings, codex_model="next-model", chat_provider="codex")
    backend = fixtures._backends(changed)
    assert fixtures._run(changed, store, folder, backend).status == "succeeded"
    assert backend.llm.calls > 0
    after = Processing(store).listing("hanbit_docs")
    assert len(after) > len(old)
    assert all(Processing(store).get(r["id"]) == r for r in old)


def test_prompt_change_reextracts_unchanged_document_bytes(env, monkeypatch):
    from autoontology.unstructured import prompts

    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + "\nUse updated rules.")
    backends = fixtures._backends(settings)
    fixtures._run(settings, store, folder, backends)
    assert backends.llm.calls > 0


def test_historical_records_outlive_source_and_chunk_deletion(env):
    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    before = Processing(store).listing("hanbit_docs")
    store.delete_source(store.get_source("hanbit_docs").id)
    assert Processing(store).listing("hanbit_docs") == before


def test_receipt_tampering_is_detected(env):
    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    item = Processing(store).listing("hanbit_docs")[0]
    with store._conn() as conn:
        conn.execute("UPDATE processing_receipts SET record_json='{}' WHERE id=?", (item["id"],))
    with pytest.raises(ProcessingError, match="integrity"):
        Processing(store).get(item["id"])


def test_failed_reextraction_preserves_old_receipts_and_leaves_pending_chunks(env):
    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    before = Processing(store).listing("hanbit_docs")
    changed = replace(settings, codex_model="changed", chat_provider="codex")

    class Failed:
        def structured(self, *args):
            raise ConnectionError("provider unavailable")

        text = structured

    from autoontology.engine.jobs import JobError

    with pytest.raises(JobError):
        fixtures._run(changed, store, folder, fixtures._backends(changed, llm=Failed()))
    assert all(Processing(store).get(r["id"]) == r for r in before)
    assert any(
        c.extraction is None
        for c in DocumentStore(store).chunks_for_source(store.get_source("hanbit_docs").id)
    )


def test_chunk_settings_change_causes_real_reread_and_rechunk(env, monkeypatch):
    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    source = store.get_source("hanbit_docs")
    old = {c.id for c in DocumentStore(store).chunks_for_source(source.id)}
    monkeypatch.setenv("AUTOONTOLOGY_CHUNK_PACK_TOKENS", "180")
    monkeypatch.setenv("AUTOONTOLOGY_CHUNK_TOKENS", "240")
    monkeypatch.setenv("AUTOONTOLOGY_CHUNK_OVERLAP_TOKENS", "40")
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    assert {c.id for c in DocumentStore(store).chunks_for_source(source.id)} != old


def test_snapshot_source_bindings_keep_exact_processing_receipts(env):
    from autoontology.ontology.model import MergedOntology

    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    source = store.get_source("hanbit_docs")
    model = MergedOntology("org", "https://example.test/#", documents_sources=[source.name])
    bound = build.snapshot_sources(store, [source], model)[0]["processing"]
    assert bound["status"] == "recorded" and bound["receipts"]
    before = json.loads(json.dumps(bound))
    changed = replace(settings, chat_provider="codex", codex_model="next")
    fixtures._run(changed, store, folder, fixtures._backends(changed))
    assert bound == before
    assert all(Processing(store).get(r) for r in bound["receipts"])


def test_configuration_never_copies_provider_secrets(env):
    settings, _, _ = env
    settings = replace(
        settings,
        chat_provider="openai",
        openai_api_key="test-secret-credential",
        openai_base_url="https://user:secret-host@provider.test/v1",
    )
    encoded = json.dumps(extract.extraction_config(settings))
    assert "secret" not in encoded and "provider.test" not in encoded


def test_processing_cli_and_api_inspect_identical_records(env, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.web.app import create_app

    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    records = Processing(store).listing("hanbit_docs")
    client = TestClient(create_app(settings))
    assert client.get("/api/ops/processing", params={"source": "hanbit_docs"}).json() == records
    assert client.get("/api/ops/processing/" + records[0]["id"]).json() == records[0]
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(cli, "_store", lambda _: store)
    result = CliRunner().invoke(cli.app, ["ops", "processing-record", records[0]["id"]])
    assert result.exit_code == 0 and json.loads(result.output) == records[0]
    listed = CliRunner().invoke(cli.app, ["ops", "processing", "hanbit_docs"])
    assert listed.exit_code == 0 and json.loads(listed.output) == records


def test_configuration_change_is_checked_on_resume_too(env):
    settings, store, folder = env
    fixtures._run(settings, store, folder, fixtures._backends(settings))
    changed = replace(settings, chat_provider="codex", codex_model="changed")
    backends = fixtures._backends(changed)
    assert fixtures._run(changed, store, folder, backends, resume=True).status == "succeeded"
    assert backends.llm.calls > 0


def test_source_binding_change_invalidates_publication_validation(tmp_path):
    from autoontology.engine.operations import run_publish
    from autoontology.store.operations import OperationsError
    from tests import test_publication_manifest as pub

    store, settings, candidate, checked, _ = pub.prepared.__wrapped__(tmp_path)
    sources = store.get_snapshot(candidate).sources
    sources[0]["processing"] = {
        "version": 1,
        "status": "unknown",
        "receipts": [],
        "unknown": [{"stage": "extract"}],
    }
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET sources_json=? WHERE revision=?",
            (json.dumps(sources), candidate),
        )
    with pytest.raises(OperationsError, match="validation changed"):
        run_publish(
            store,
            settings,
            revision=candidate,
            token=checked["token"],
            embedder=(pub.embed, "test"),
        )


def test_publication_pins_processing_and_verification_detects_missing_history(tmp_path):
    from autoontology.engine.operations import run_publish, validate
    from autoontology.store.publications import PublicationError, Publications
    from tests import test_publication_manifest as pub

    store, settings, candidate, _, _ = pub.prepared.__wrapped__(tmp_path)
    source = store.get_source("rules")
    processing = Processing(store)
    parent = processing.record(
        source,
        "read",
        "read",
        input={"sha256": "historical-fixture"},
        configuration={"status": "unknown"},
        output={},
        dependencies=[],
    )
    child = processing.record(
        source,
        "extract",
        "extract",
        input={},
        configuration={"status": "unknown"},
        output={},
        dependencies=[parent],
    )
    sources = store.get_snapshot(candidate).sources
    sources[0]["processing"] = {
        "version": 1,
        "status": "unknown",
        "receipts": [child],
        "unknown": [{"stage": "legacy"}],
    }
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET sources_json=? WHERE revision=?",
            (json.dumps(sources), candidate),
        )
    checked = validate(
        store, settings, revision=candidate, embedder=(pub.embed, "test"), llm=pub.Answer()
    )
    revision = run_publish(
        store, settings, revision=candidate, token=checked["token"], embedder=(pub.embed, "test")
    ).revision
    manifest = Publications(store).get(revision)["manifest"]
    assert {r["id"] for r in manifest["processing_receipts"]} == {parent, child}
    assert Publications(store).verify(revision, settings.exports_dir)["ok"]
    with store._conn() as conn:
        conn.execute("DELETE FROM processing_receipts WHERE id=?", (parent,))
    with pytest.raises(PublicationError, match="processing receipt"):
        Publications(store).verify(revision, settings.exports_dir)


def test_historical_reader_settings_are_not_inferred_from_matching_live_bytes(tmp_path):
    from autoontology.store.publications import Publications
    from tests import test_publication_manifest as pub

    prepared = pub.prepared.__wrapped__(tmp_path)
    store, _, _, _, _ = prepared
    model_document = next(iter(store.latest_snapshot().model["documents"].values()))
    DocumentStore(store).replace_document(
        source_id=store.get_source("rules").id,
        path="/current.md",
        title="Current reader",
        mime="text/plain",
        engine="current-reader",
        sha256=model_document["sha256"],
        size=1,
        pages=None,
        chunks=[],
        reader_fingerprint="today-only",
    )
    revision = pub.publish(prepared).revision
    assert Publications(store).get(revision)["manifest"]["readers"][0]["fingerprints"] == []


def test_later_publication_inherits_frozen_parent_inputs(tmp_path):
    from autoontology.engine.operations import run_publish, validate
    from autoontology.store.operations import Operations
    from autoontology.store.publications import Publications
    from tests import test_publication_manifest as pub

    store, settings, candidate, _, _ = pub.prepared.__wrapped__(tmp_path)
    processing = Processing(store)
    source = store.get_source("rules")
    first = processing.record(
        source,
        "extract",
        "first",
        input={},
        configuration={"model": "first"},
        output={},
        dependencies=[],
    )
    sources = store.get_snapshot(candidate).sources
    sources[0]["processing"] = {
        "version": 1,
        "status": "unknown",
        "receipts": [first],
        "unknown": [],
    }
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET sources_json=? WHERE revision=?",
            (json.dumps(sources), candidate),
        )
    checked = validate(
        store, settings, revision=candidate, embedder=(pub.embed, "test"), llm=pub.Answer()
    )
    parent = run_publish(
        store, settings, revision=candidate, token=checked["token"], embedder=(pub.embed, "test")
    ).revision
    second = processing.record(
        source,
        "extract",
        "second",
        input={},
        configuration={"model": "second"},
        output={},
        dependencies=[],
    )
    sources[0]["processing"]["receipts"] = [second]
    model = Operations(store).model(parent)
    model.classes["concept-rule"].definitions = ["Updated rule"]
    run = store.create_run(kind="build", source_id=None)
    candidate = store.save_snapshot(run.id, model, sources=sources)
    store.update_run(run.id, status="succeeded")
    for item in Operations(store).changes():
        Operations(store).decide(item["id"], "accept", candidate=candidate)
    checked = validate(
        store, settings, revision=candidate, embedder=(pub.embed, "test"), llm=pub.Answer()
    )
    result = run_publish(
        store, settings, revision=candidate, token=checked["token"], embedder=(pub.embed, "test")
    )
    manifest = Publications(store).get(result.revision)["manifest"]
    assert manifest["previous_publication_id"] == Publications(store).get(parent)["id"]
    assert {r["id"] for r in manifest["processing_receipts"]} == {first, second}


def test_missing_validation_input_has_an_actionable_error(tmp_path):
    from autoontology.store.db import Store
    from autoontology.store.operations import Operations, OperationsError

    with pytest.raises(OperationsError, match="snapshot is missing"):
        Operations(Store.for_home(tmp_path)).signature(999, {})
