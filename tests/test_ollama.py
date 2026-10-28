import json
import subprocess

import httpx
import pytest

from autoontology.engine import ollama
from autoontology.engine.ollama import NEW1, NEW2, OLD1, OLD2, TemplatePatchError

BASE_TEMPLATE = "{{- range $i, $_ := .Messages }}\n" + OLD1 + "\nmiddle\n" + OLD2 + "{{ end }}\n"


def test_patch_replaces_both_think_switches():
    patched = ollama.patch_no_think_template(BASE_TEMPLATE)
    assert OLD1 not in patched and OLD2 not in patched
    assert NEW1 in patched and NEW2 in patched
    assert "$.IsThinkSet" not in patched
    assert '" "}}/think' not in patched


@pytest.mark.parametrize("missing", [OLD1, OLD2])
def test_patch_fails_loudly_when_a_pattern_is_absent(missing):
    with pytest.raises(TemplatePatchError, match="template changed"):
        ollama.patch_no_think_template(BASE_TEMPLATE.replace(missing, ""))


def test_patch_rejects_duplicate_patterns():
    with pytest.raises(TemplatePatchError):
        ollama.patch_no_think_template(BASE_TEMPLATE + OLD1)


def test_modelfile_has_base_ctx_and_template():
    text = ollama.build_modelfile("qwen3:8b", BASE_TEMPLATE, num_ctx=16384)
    lines = text.splitlines()
    assert lines[0] == "FROM qwen3:8b"
    assert lines[1] == "PARAMETER num_ctx 16384"
    assert lines[2].startswith('TEMPLATE """')
    assert text.rstrip().endswith('"""')
    assert NEW1 in text and OLD1 not in text


def test_modelfile_rejects_triple_quotes():
    with pytest.raises(TemplatePatchError):
        ollama.build_modelfile("b", BASE_TEMPLATE + '"""', num_ctx=1)


def test_has_model_accepts_latest_suffix():
    names = {"bge-m3:latest", "openai/qwen3:8b"}
    assert ollama.has_model(names, "bge-m3")
    assert ollama.has_model(names, "openai/qwen3:8b")
    assert not ollama.has_model(names, "qwen3:8b")


class _Runner:
    def __init__(self, template=BASE_TEMPLATE):
        self.calls = []
        self.template = template
        self.modelfile = None

    def __call__(self, argv, **kw):
        self.calls.append(argv[:3])
        if argv[1] == "show":
            return subprocess.CompletedProcess(argv, 0, stdout=self.template, stderr="")
        if argv[1] == "create":
            with open(argv[argv.index("-f") + 1]) as fh:
                self.modelfile = fh.read()
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def _transport(names, completion_tokens=2):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in names]})
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "openai/qwen3:8b"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"completion_tokens": completion_tokens},
            },
        )

    return httpx.MockTransport(handler)


def _setup(names, runner, *, force=False, tokens=2):
    client = httpx.Client(transport=_transport(names, tokens))
    return ollama.setup_ollama(
        endpoint="http://localhost:11434/v1",
        base="qwen3:8b",
        alias="openai/qwen3:8b",
        embed="bge-m3",
        num_ctx=16384,
        force=force,
        run=runner,
        client=client,
        echo=lambda _m: None,
    )


def test_setup_pulls_missing_models_and_creates_alias():
    runner = _Runner()
    result = _setup(set(), runner)
    assert ["ollama", "pull", "qwen3:8b"] in runner.calls
    assert ["ollama", "pull", "bge-m3"] in runner.calls
    assert ["ollama", "create", "openai/qwen3:8b"] in runner.calls
    assert "PARAMETER num_ctx 16384" in runner.modelfile and NEW2 in runner.modelfile
    assert result.created and result.completion_tokens == 2


def test_setup_is_idempotent_but_still_verifies():
    runner = _Runner()
    result = _setup({"qwen3:8b", "bge-m3:latest", "openai/qwen3:8b"}, runner)
    assert runner.calls == []
    assert not result.created and result.answer == "pong"


def test_setup_force_recreates():
    runner = _Runner()
    _setup({"qwen3:8b", "bge-m3:latest", "openai/qwen3:8b"}, runner, force=True)
    assert [c[1] for c in runner.calls] == ["show", "create"]


def test_setup_fails_when_thinking_is_still_on():
    with pytest.raises(ollama.OllamaSetupError, match="completion_tokens 180"):
        _setup({"qwen3:8b", "bge-m3:latest", "openai/qwen3:8b"}, _Runner(), tokens=180)


def test_setup_surfaces_template_drift():
    with pytest.raises(TemplatePatchError):
        _setup({"qwen3:8b", "bge-m3:latest"}, _Runner(template="{{ .Prompt }}"))
