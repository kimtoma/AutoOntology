# SPDX-License-Identifier: MIT
"""Safe semantic configuration and observed backend descriptors for document work."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from autoontology.config import (
    CODEX_REASONING_EFFORTS,
    codex_reasoning_effort,
    expand_model_profile,
    resolve_triplet_field,
)
from autoontology.store.operations import digest
from autoontology.unstructured import chunker, prompts, schemas
from autoontology.unstructured.extractor import EXTRACT_MAX_TOKENS, extract_mode


def code_hashes(*names):
    import importlib

    return {
        name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
        for name in names
    }


def extraction_config(settings):
    models = expand_model_profile(settings)
    return {
        "version": 1,
        "provider": settings.chat_provider,
        "model": resolve_triplet_field(models, "REASONING", "MODEL"),
        "endpoint_sha256": digest(resolve_triplet_field(models, "REASONING", "ENDPOINT")),
        "mode": extract_mode(),
        "requested_temperature": 0.0,
        "max_tokens": EXTRACT_MAX_TOKENS,
        "provider_options": {
            "ollama_no_think": settings.ollama_no_think
            if settings.chat_provider == "ollama"
            else None,
            "codex_reasoning_effort": codex_reasoning_effort()
            if settings.chat_provider == "codex"
            and codex_reasoning_effort() in CODEX_REASONING_EFFORTS
            else "unknown",
        },
        "schema_sha256": digest(schemas.ChunkExtraction.model_json_schema()),
        "structured_system_sha256": digest(prompts.SYSTEM_PROMPT),
        "json_system_sha256": digest(prompts.json_mode_system_prompt()),
        "human_template_sha256": digest(prompts.HUMAN_TEMPLATE),
        "code": code_hashes(
            "autoontology.unstructured.extractor",
            "autoontology.unstructured.schemas",
            "autoontology.unstructured.prompts",
            "autoontology.engine.processing",
        ),
        "capture_boundary": "application backend calls; internal provider/helper retries may not expose each request",
    }


def reader_config(settings, fingerprint):
    return {
        "fingerprint": fingerprint,
        "chunk_sizes": list(chunker.env_sizes()),
        "max_bytes": settings.doc_max_bytes,
        "table_max_rows": settings.doc_table_max_rows,
        "vision_model": settings.vision_model or None,
        "code": code_hashes(
            "autoontology.unstructured.readers", "autoontology.unstructured.chunker"
        ),
        "external_engine_runtime": "not_archived; reader fingerprint records observed OCR readiness",
    }


def backend_descriptor(backend):
    from autoontology.unstructured.extractor import UpstreamBackend

    if not isinstance(backend, UpstreamBackend):
        return {"status": "unverified", "reason": "injected backend; no observed provider identity"}
    llm = backend.llm
    return {
        "status": "observed_client_configuration",
        "model": getattr(llm, "model_name", None),
        "endpoint_sha256": digest(str(getattr(llm, "openai_api_base", ""))),
        "temperature": getattr(llm, "temperature", None),
        "max_tokens": getattr(llm, "max_tokens", None),
        "immutable_weights": "unverified; a CLI or hosted model name does not identify archived weights",
    }


class RecordingBackend:
    """Keep each concurrent chunk's actual requests/outputs separate, including retries."""

    def __init__(self, backend):
        self.backend = backend
        self.local = threading.local()

    def begin_capture(self):
        self.local.calls = []

    def end_capture(self):
        calls = getattr(self.local, "calls", [])
        self.local.calls = []
        return calls

    def invoke(self, method, request, call):
        entry = {"method": method, "request": request}
        self.local.calls.append(entry)
        try:
            result = call()
            entry["response"] = (
                result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            )
            return result
        except Exception as exc:
            entry["error"] = type(exc).__name__
            raise

    def structured(self, system, human, schema):
        return self.invoke(
            "structured",
            {"system": system, "human": human, "schema": schema.model_json_schema()},
            lambda: self.backend.structured(system, human, schema),
        )

    def text(self, prompt):
        return self.invoke("text", {"prompt": prompt}, lambda: self.backend.text(prompt))
