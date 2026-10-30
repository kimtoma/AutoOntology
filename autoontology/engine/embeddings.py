# SPDX-License-Identifier: MIT
"""Model-specific retrieval inputs shared by playground, alignment and upstream.

Ollama's OpenAI endpoint ignores NVIDIA's ``input_type`` extension. Qwen therefore
needs its query instruction in the actual text; passages remain unprefixed. See
https://huggingface.co/Qwen/Qwen3-Embedding-0.6B#usage.
"""

from __future__ import annotations

from functools import wraps

QWEN_RETRIEVAL_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)


def prepare_inputs(texts: list[str], model: str, input_type: str) -> list[str]:
    if input_type != "query" or model.lower().partition(":")[0] != "qwen3-embedding":
        return texts
    prefix = f"Instruct: {QWEN_RETRIEVAL_INSTRUCTION}\nQuery:"
    return [
        prefix + text.strip()
        if isinstance(text, str) and text.strip() and not text.strip().startswith(prefix)
        else text
        for text in texts
    ]


def wrap_embed_texts(original):
    if getattr(original, "_autoontology_embedding_inputs", False):
        return original

    @wraps(original)
    def embed(texts, params, input_type, **kwargs):
        return original(
            prepare_inputs(texts, params.model_name, input_type), params, input_type, **kwargs
        )

    embed._autoontology_embedding_inputs = True
    return embed


def install() -> None:
    """Install before upstream callers import ``embed_texts``; safe to call twice."""
    from auto_ontology.utils import embedding

    embedding.embed_texts = wrap_embed_texts(embedding.embed_texts)
