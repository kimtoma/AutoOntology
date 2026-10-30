# SPDX-License-Identifier: MIT
"""Model names do not authorize reuse of vectors from different model content."""

import json
from dataclasses import replace

import httpx
import pytest

from autoontology.config import Settings
from autoontology.engine import model_artifacts as artifacts
from autoontology.engine.operations import profile, run_publish, validate
from autoontology.playground.service import Playground, PlaygroundError
from autoontology.store.operations import Operations
from autoontology.store.publications import Publications
from tests import test_publication_manifest as publication


def resolver(monkeypatch, digest="a" * 64):
    state = {"digest": digest, "calls": 0}

    def get(url, **kwargs):
        state["calls"] += 1
        assert url == "http://localhost:11434/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "bge-m3:latest", "digest": state["digest"]}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(artifacts, "_get", get)
    return state


def test_ollama_identity_resolves_latest_digest_without_credentials(monkeypatch):
    resolver(monkeypatch)
    identity = artifacts.embedding_identity(Settings.from_env())
    assert identity["status"] == "verified" and identity["sha256"] == "a" * 64
    assert identity["provider"] == "ollama" and identity["input_adapter_sha256"]
    assert not any(key in json.dumps(identity) for key in ("endpoint", "api_key", "Bearer"))


@pytest.mark.parametrize("bad", ["", "sha256:bad", None, "a" * 63, "a" * 65, 10])
def test_missing_or_malformed_digest_never_becomes_a_verified_identity(monkeypatch, bad):
    resolver(monkeypatch, bad)
    with pytest.raises(artifacts.ModelArtifactError, match="identity"):
        artifacts.embedding_identity(Settings.from_env())


def test_unavailable_identity_has_safe_actionable_error(monkeypatch):
    def broken(*args, **kw):
        raise httpx.ConnectError("secret provider body https://user:secret@host")

    monkeypatch.setattr(artifacts, "_get", broken)
    with pytest.raises(artifacts.ModelArtifactError) as caught:
        artifacts.embedding_identity(Settings.from_env())
    assert caught.value.code == "embedding_identity_unavailable"
    assert "secret" not in str(caught.value)


def test_non_ollama_and_injected_models_are_explicitly_unverified(monkeypatch):
    monkeypatch.setattr(artifacts, "_get", lambda *a, **k: pytest.fail("no provider discovery"))
    assert (
        artifacts.embedding_identity(replace(Settings.from_env(), embed_provider="openai"))[
            "status"
        ]
        == "unverified"
    )
    assert (
        profile(Settings.from_env(), (publication.embed, "test"))["embedding_artifact"]["status"]
        == "unverified"
    )


def test_guard_detects_same_tag_change_before_and_after_embedding(monkeypatch):
    state = resolver(monkeypatch)
    settings = Settings.from_env()
    identity = artifacts.embedding_identity(settings)
    calls = []

    def embed(texts, kind):
        calls.append(kind)
        if kind == "query":
            state["digest"] = "b" * 64
        return publication.embed(texts, kind)

    guarded = artifacts.GuardedEmbedder(
        embed, identity, lambda: artifacts.embedding_identity(settings)
    )
    assert guarded(["policy"], "passage") == [[1, 0, 0]]
    with pytest.raises(artifacts.ModelArtifactError, match="changed"):
        guarded(["question"], "query")
    with pytest.raises(artifacts.ModelArtifactError, match="changed"):
        guarded(["policy"], "passage")
    assert calls == ["passage", "query"]


def verified_embedder(monkeypatch):
    state = resolver(monkeypatch)
    identity = artifacts.embedding_identity(Settings.from_env())
    guarded = artifacts.GuardedEmbedder(
        publication.embed, identity, lambda: artifacts.embedding_identity(Settings.from_env())
    )
    return state, (guarded, "test")


def test_same_tag_mutation_invalidates_validation_and_does_not_publish(tmp_path, monkeypatch):
    prepared = publication.prepared.__wrapped__(tmp_path)
    store, settings, candidate, _, _ = prepared
    state, embedder = verified_embedder(monkeypatch)
    checked = validate(
        store, settings, revision=candidate, embedder=embedder, llm=publication.Answer()
    )
    state["digest"] = "b" * 64
    with pytest.raises(artifacts.ModelArtifactError):
        run_publish(store, settings, revision=candidate, token=checked["token"], embedder=embedder)
    assert Operations(store).state()["published_revision"] is None


def test_publication_records_identity_and_blocks_changed_artifact(tmp_path, monkeypatch):
    prepared = publication.prepared.__wrapped__(tmp_path)
    store, settings, candidate, _, _ = prepared
    state, embedder = verified_embedder(monkeypatch)
    checked = validate(
        store, settings, revision=candidate, embedder=embedder, llm=publication.Answer()
    )
    result = run_publish(
        store, settings, revision=candidate, token=checked["token"], embedder=embedder
    )
    manifest = Publications(store).get(result.revision)["manifest"]
    assert manifest["profile"]["embedding_artifact"]["sha256"] == "a" * 64
    model = Operations(store).model(result.revision)
    service = Playground(
        store,
        model_for=lambda: (result.revision, model),
        llm_factory=lambda: publication.Answer(),
        embedder_factory=lambda: embedder,
    )
    assert service.index(service.scope()).reused > 0
    state["digest"] = "b" * 64
    fresh = artifacts.GuardedEmbedder(
        publication.embed,
        artifacts.embedding_identity(Settings.from_env()),
        lambda: artifacts.embedding_identity(Settings.from_env()),
    )
    service.embedder_factory = lambda: (fresh, "test")
    with pytest.raises(PlaygroundError, match="artifact changed"):
        service.index(service.scope())


def test_artifact_change_during_validation_cannot_create_a_ready_packet(tmp_path, monkeypatch):
    prepared = publication.prepared.__wrapped__(tmp_path)
    store, settings, candidate, _, _ = prepared
    state, embedder = verified_embedder(monkeypatch)

    class ChangingAnswer:
        async def stream(self, messages):
            state["digest"] = "b" * 64
            yield "대표이사 승인입니다. [E1]"

    with pytest.raises(artifacts.ModelArtifactError):
        validate(store, settings, revision=candidate, embedder=embedder, llm=ChangingAnswer())
    assert Operations(store).state()["published_revision"] is None


def test_unrecorded_legacy_vectors_are_preserved_but_not_reused(tmp_path, monkeypatch):
    from autoontology.playground.store import PlaygroundStore

    prepared = publication.prepared.__wrapped__(tmp_path)
    store, _, candidate, _, _ = prepared
    state, embedder = verified_embedder(monkeypatch)
    model = Operations(store).model(candidate)
    service = Playground(
        store,
        model_for=lambda: (candidate, model),
        llm_factory=lambda: publication.Answer(),
        embedder_factory=lambda: embedder,
    )
    first = service.index(service.scope())
    assert first.embedded > 0
    scopes = []
    with store._conn() as conn:
        scopes = [r[0] for r in conn.execute("SELECT DISTINCT scope FROM playground_index")]
    state["digest"] = "b" * 64

    def second_embed(texts, kind):
        return [[0, 1, 0] for _ in texts]

    guarded = artifacts.GuardedEmbedder(
        second_embed,
        artifacts.embedding_identity(Settings.from_env()),
        lambda: artifacts.embedding_identity(Settings.from_env()),
    )
    service.embedder_factory = lambda: (guarded, "test")
    second = service.index(service.scope())
    assert second.embedded > 0 and second.reused == 0
    assert second._matrix[0].tolist() == [0, 1, 0]
    assert all(PlaygroundStore(store).index_rows(scope) for scope in scopes)


def test_guarded_query_change_has_stable_conflict_code(tmp_path, monkeypatch):
    prepared = publication.prepared.__wrapped__(tmp_path)
    store, _, candidate, _, _ = prepared
    state, _ = verified_embedder(monkeypatch)

    def embed(texts, kind):
        if kind == "query":
            state["digest"] = "b" * 64
        return publication.embed(texts, kind)

    guarded = artifacts.GuardedEmbedder(
        embed,
        artifacts.embedding_identity(Settings.from_env()),
        lambda: artifacts.embedding_identity(Settings.from_env()),
    )
    service = Playground(
        store,
        model_for=lambda: (candidate, Operations(store).model(candidate)),
        llm_factory=lambda: publication.Answer(),
        embedder_factory=lambda: (guarded, "test"),
    )
    with pytest.raises(PlaygroundError) as error:
        service.retrieve(service.scope(), "승인권자는?", 12)
    assert error.value.status == 409 and error.value.code == "embedding_artifact_changed"


def test_cli_model_identity_uses_the_same_core_and_safe_codes(monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli

    state = resolver(monkeypatch)
    result = CliRunner().invoke(cli.app, ["ops", "model-identity"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["sha256"] == state["digest"]
    state["digest"] = None
    result = CliRunner().invoke(cli.app, ["ops", "model-identity"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "embedding_identity_unavailable"


def test_cache_refreshes_on_same_tag_digest_change(monkeypatch):
    import auto_ontology.utils.embedding as emb

    from autoontology.playground.service import EmbedderCache

    identity = {"digest": "a" * 64}
    monkeypatch.setattr(
        artifacts,
        "embedding_identity",
        lambda *a, **k: {"status": "verified", "sha256": identity["digest"]},
    )
    built = []
    monkeypatch.setattr("autoontology.engine.jobs.prepare_upstream_env", lambda s: None)
    monkeypatch.setattr(emb, "EmbedParams", lambda **kw: built.append(kw) or kw)
    settings = Settings.from_env()
    cache = EmbedderCache(lambda: settings, settings)
    first = cache()
    assert cache() is first
    identity["digest"] = "b" * 64
    assert cache() is not first and len(built) == 2
