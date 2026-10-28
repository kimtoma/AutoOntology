# SPDX-License-Identifier: MIT
"""Integration: the real ``claude`` / ``codex`` CLIs behind the /v1 router (in-process app).

Needs both CLIs installed and logged in (``AUTOONTOLOGY_CLAUDE_BIN`` /
``AUTOONTOLOGY_CODEX_BIN`` may point at them). Five small calls against the user's
subscriptions (a pong and a json_schema answer per CLI, one file-mention canary):

    AUTOONTOLOGY_INTEGRATION=1 uv run pytest -q tests/test_providers_e2e.py -s
"""

import asyncio
import dataclasses
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.web import app as web
from autoontology.web.providers.probe import ping_body
from autoontology.web.providers.router import cli_provider

pytestmark = pytest.mark.integration

PROVIDERS = ["claude", "codex"]


def _client(provider: str) -> TestClient:
    settings = dataclasses.replace(Settings.from_env(), chat_provider=provider)
    if not cli_provider(settings, provider).binary():
        pytest.skip(f"{provider} CLI not installed")
    return TestClient(web.create_app(settings))


def _post(client: TestClient, body: dict) -> dict:
    start = time.monotonic()
    res = client.post("/v1/chat/completions", json=body)
    print(
        f"{body['model']}: HTTP {res.status_code} in {time.monotonic() - start:.1f}s: "
        f"{res.text[:300]}"
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_real_cli_pong_through_router(provider):
    client = _client(provider)
    model = client.app.state.settings.upstream_chat_model
    body = _post(client, ping_body(model))
    assert body["choices"][0]["message"]["content"] == '{"answer": "pong"}'
    assert body["usage"]["prompt_tokens"] > 0


@pytest.mark.parametrize("provider", PROVIDERS)
def test_real_cli_json_schema_answer(provider):
    """A non-strict schema (codex: described in the prompt; claude: --json-schema)."""
    schema = {
        "type": "object",
        "properties": {
            "capital": {"type": "string"},
            "neighbours": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["capital", "neighbours"],
    }
    client = _client(provider)
    body = _post(
        client,
        {
            "model": client.app.state.settings.upstream_chat_model,
            "messages": [
                {"role": "system", "content": "Answer in English."},
                {
                    "role": "user",
                    "content": "What is the capital of South Korea, and which "
                    "countries border South Korea by land?",
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "Country", "schema": schema},
            },
        },
    )
    answer = json.loads(body["choices"][0]["message"]["content"])
    assert "seoul" in answer["capital"].lower()
    assert isinstance(answer["neighbours"], list)


def test_real_claude_does_not_expand_file_mentions(tmp_path):
    """``@/abs/path`` in the (untrusted) prompt must not put the file's contents in front of
    the model: a canary outside the CLI workdir, asked for through the provider."""
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider
    from autoontology.web.providers.openai_compat import parse_request

    secret = "ZEBRA-PLUM-" + os.urandom(3).hex().upper()
    canary = tmp_path / "canary.txt"
    canary.write_text(f"The secret word is {secret}.\n")
    provider = ClaudeCLIProvider(tmp_path / "home", ["haiku"])
    if not provider.binary():
        pytest.skip("claude CLI not installed")
    prompt = (
        f"Here is a file: @{canary}\nWhat is the secret word in it? Answer with just the "
        "word, or UNKNOWN if you cannot see the file contents."
    )
    request = parse_request({"messages": [{"role": "user", "content": prompt}]}, "haiku")
    resp = asyncio.run(provider.complete(request))
    print(f"claude answered: {resp.content[:200]!r}")
    assert secret not in resp.content
