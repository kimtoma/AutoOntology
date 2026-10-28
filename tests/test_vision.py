# SPDX-License-Identifier: MIT
import base64
import json
from pathlib import Path

import httpx
import pytest

from autoontology.unstructured import vision
from autoontology.unstructured.readers import Document, Section


def _doc(path: Path) -> Document:
    return Document("scan", path, "image/png", [Section("", None, "OCR 텍스트", 0)], "kordoc")


def test_caption_request_and_prepended_section(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG fake")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"message": {"role": "assistant", "content": " 조직도 "}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    doc = vision.add_caption(
        _doc(img), endpoint="http://localhost:11434/v1", model="gemma4:e4b", client=client
    )
    assert seen["url"] == "http://localhost:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "gemma4:e4b" and body["stream"] is False
    (msg,) = body["messages"]
    assert msg["role"] == "user" and msg["content"] == vision.CAPTION_PROMPT
    assert msg["images"] == [base64.b64encode(b"\x89PNG fake").decode()]
    assert seen["timeout"]["read"] == 120
    assert [s.kind for s in doc.sections] == ["caption", "text"]
    assert doc.sections[0].path == "Image description" and doc.sections[0].text == "조직도"
    assert doc.sections[1].char_start == len("조직도") + 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"error": "model not found"}),
        httpx.Response(200, json={"message": {"content": ""}}),
        httpx.Response(200, text="not json"),
    ],
)
def test_caption_failures_raise_vision_error(tmp_path, response):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(vision.VisionError):
        vision.caption_image(img, endpoint="http://h:1", model="m", client=client)


def test_connect_error_is_a_vision_error(tmp_path):
    img = tmp_path / "a.webp"
    img.write_bytes(b"x")

    def boom(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(vision.VisionError, match="ConnectError"):
        vision.caption_image(img, endpoint="http://h:1", model="m", client=client)


def test_doctor_vision_model_row(monkeypatch, tmp_path):
    from autoontology import doctor
    from autoontology.config import Settings
    from autoontology.engine import ollama

    assert doctor.check_vision_model(Settings(home=tmp_path)).status == "SKIP"
    settings = Settings(home=tmp_path, vision_model="gemma4:e4b")
    monkeypatch.setattr(ollama, "list_models", lambda endpoint, client=None: {"qwen3:8b"})
    check = doctor.check_vision_model(settings)
    assert check.status == "FAIL" and check.remedy == "uv run autoontology setup"
    monkeypatch.setattr(ollama, "list_models", lambda endpoint, client=None: {"gemma4:e4b"})
    assert doctor.check_vision_model(settings).status == "OK"

    def down(endpoint, client=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama, "list_models", down)
    assert doctor.check_vision_model(settings).status == "FAIL"


def test_settings_read_the_new_variables(monkeypatch, tmp_path):
    from autoontology.config import Settings

    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(tmp_path / "none.env"))
    defaults = Settings.from_env()
    assert defaults.vision_model == "" and defaults.doc_table_max_rows == 200
    monkeypatch.setenv("AUTOONTOLOGY_VISION_MODEL", " gemma4:e4b ")
    monkeypatch.setenv("AUTOONTOLOGY_DOC_TABLE_MAX_ROWS", "50")
    s = Settings.from_env()
    assert s.vision_model == "gemma4:e4b" and s.doc_table_max_rows == 50
    assert defaults.doc_max_bytes == 50 * 1024 * 1024
    monkeypatch.setenv("AUTOONTOLOGY_DOC_MAX_BYTES", "1000")
    assert Settings.from_env().doc_max_bytes == 1000
