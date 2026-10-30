# SPDX-License-Identifier: MIT
"""Bind retrieval vectors to observable provider content, not a mutable model tag.

Ollama reports the manifest digest through GET /api/tags. Discovery is read-only:
it never pulls, replaces or copies a model on the shared local server. Checks
surround each embedding call; this is drift detection, not an atomic server lock
or an archive of model weights and runtime dependencies.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from autoontology.engine.ollama import root_url
from autoontology.store.operations import OperationsError


class ModelArtifactError(OperationsError):
    def __init__(self, message: str, code="embedding_identity_unavailable", status=503):
        super().__init__(message)
        self.code, self.status = code, status


def _get(url, **kwargs):
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        return client.get(url, **kwargs)


def input_adapter_sha256():
    from autoontology.engine import embeddings

    return hashlib.sha256(Path(embeddings.__file__).read_bytes()).hexdigest()


def embedding_identity(settings, embedder=None, *, endpoint=None, model=None) -> dict:
    if embedder is not None:
        embed, _ = embedder
        if isinstance(embed, GuardedEmbedder):
            return embed.check()
        return {
            "version": 1,
            "provider": "injected",
            "status": "unverified",
            "reason": "caller supplied an embedder without a provider content identity",
        }
    identity = {
        "version": 1,
        "provider": settings.embed_provider,
        "input_adapter_sha256": input_adapter_sha256(),
    }
    if settings.embed_provider != "ollama":
        return {
            **identity,
            "status": "unverified",
            "reason": "the provider does not expose a verified immutable model identity",
        }
    # Resolve the effective endpoint/model, including supported profile overrides.
    if endpoint is None or model is None:
        from autoontology.playground.service import embed_triplet

        endpoint, _, model = embed_triplet(settings)
    try:
        response = _get(f"{root_url(endpoint)}/api/tags", timeout=5.0)
        response.raise_for_status()
        rows = response.json()["models"]
        if not isinstance(rows, list):
            raise ValueError("invalid model list")
        names = {model, f"{model}:latest"} if ":" not in model.rsplit("/", 1)[-1] else {model}
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") in names]
        digests = {
            value.removeprefix("sha256:").lower()
            for row in matches
            if isinstance(value := row.get("digest"), str)
        }
        if len(digests) != 1 or len(matches) != len(
            [r for r in matches if isinstance(r.get("digest"), str)]
        ):
            raise ValueError("missing or ambiguous model digest")
        sha = digests.pop()
        if not re.fullmatch("[0-9a-f]{64}", sha):
            raise ValueError("invalid model digest")
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise ModelArtifactError(
            "Cannot verify the embedding model identity. Check Ollama availability and the selected installed model, then retry."
        ) from None
    return {
        **identity,
        "status": "verified",
        "algorithm": "ollama-manifest-sha256",
        "sha256": sha,
    }


class GuardedEmbedder:
    def __init__(self, embed: Callable, artifact: dict, current: Callable[[], dict]):
        self.embed, self.artifact, self.current = embed, dict(artifact), current

    def check(self):
        if self.current() != self.artifact:
            raise ModelArtifactError(
                "Embedding model artifact changed. Prepare and validate a new publication before using its vectors.",
                "embedding_artifact_changed",
                409,
            )
        return dict(self.artifact)

    def __call__(self, texts, input_type):
        self.check()
        result = self.embed(texts, input_type)
        self.check()
        return result


def reusable_artifact(expected, current):
    """Unknown historical identity never authorizes passage-vector reuse."""
    return bool(expected and expected.get("status") == "verified" and expected == current)
