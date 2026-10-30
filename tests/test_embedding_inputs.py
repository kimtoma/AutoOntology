# SPDX-License-Identifier: MIT
"""Query instructions reach every upstream embedding path without vendor edits."""

from types import SimpleNamespace

import pytest

from autoontology.engine.embeddings import prepare_inputs, wrap_embed_texts


@pytest.mark.parametrize("model", ["qwen3-embedding:0.6b", "qwen3-embedding:4b", "qwen3-embedding"])
def test_qwen_queries_get_one_instruction_but_passages_stay_unchanged(model):
    texts = ["  계약 승인자는 누구인가요?  ", "", None]
    result = prepare_inputs(texts, model, "query")
    assert result[0].startswith("Instruct: ")
    assert result[0].endswith("\nQuery:계약 승인자는 누구인가요?")
    assert result[1:] == ["", None]
    assert prepare_inputs(result, model, "query") == result
    assert prepare_inputs(texts, model, "passage") == texts


def test_other_models_keep_existing_inputs():
    texts = ["질문", "", None]
    assert prepare_inputs(texts, "bge-m3", "query") == texts


def test_wrapper_preserves_timeouts_and_does_not_mutate_callers():
    calls = []

    def original(texts, params, input_type, **kwargs):
        calls.append((texts, params, input_type, kwargs))
        return [[1.0]]

    wrapped = wrap_embed_texts(original)
    assert wrap_embed_texts(wrapped) is wrapped
    params = SimpleNamespace(model_name="qwen3-embedding:4b")
    texts = ["승인자는?"]
    assert wrapped(texts, params, "query", timeout=7, retries=0) == [[1.0]]
    assert texts == ["승인자는?"]
    assert calls[0][0][0].endswith("\nQuery:승인자는?")
    assert calls[0][1:] == (params, "query", {"timeout": 7, "retries": 0})
