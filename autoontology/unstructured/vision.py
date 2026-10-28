# SPDX-License-Identifier: MIT
"""Optional image captions from an Ollama vision model (``AUTOONTOLOGY_VISION_MODEL``).

When set, every image document gets an "Image description" section (``kind="caption"``)
before its OCR text: ``POST <ollama>/api/chat`` with the image as base64. OCR finds the
words; the caption adds what kind of document or diagram it is and how its parts relate.
The model's answer is untrusted document data, like the OCR text.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import httpx

from autoontology.unstructured.readers import Document, Section

CAPTION_TITLE = "Image description"
CAPTION_TIMEOUT_S = 120.0
CAPTION_PROMPT = (
    "Describe this image for an enterprise knowledge base. Say what kind of document or "
    "diagram it is, then list the entities it shows (organisations, people, products, "
    "codes), the relations between them, and every number with its meaning. Be factual "
    "and do not guess beyond what is visible. Answer in the language of the text in the "
    "image."
)


class VisionError(RuntimeError):
    pass


def _root(endpoint: str) -> str:
    return endpoint.rstrip("/").removesuffix("/v1")


def caption_image(
    path: Path,
    *,
    endpoint: str,
    model: str,
    client: httpx.Client | None = None,
    timeout: float = CAPTION_TIMEOUT_S,
) -> str:
    """The vision model's description of the image at *path* (``VisionError`` on failure)."""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": CAPTION_PROMPT,
                "images": [base64.b64encode(Path(path).read_bytes()).decode("ascii")],
            }
        ],
        "stream": False,
    }
    url = f"{_root(endpoint)}/api/chat"
    try:
        resp = (client or httpx).post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise VisionError(f"{model} at {url}: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise VisionError(f"{model} at {url}: HTTP {resp.status_code} {resp.text[:200]}")
    try:
        content = resp.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise VisionError(f"{model}: unexpected response {resp.text[:200]!r}") from exc
    text = str(content or "").strip()
    if not text:
        raise VisionError(f"{model}: empty description")
    return text


def add_caption(
    doc: Document,
    *,
    endpoint: str,
    model: str,
    client: httpx.Client | None = None,
    timeout: float = CAPTION_TIMEOUT_S,
) -> Document:
    """*doc* with the caption section first (the OCR sections follow, offsets shifted)."""
    text = caption_image(doc.path, endpoint=endpoint, model=model, client=client, timeout=timeout)
    shift = len(text) + 1
    sections = [Section(CAPTION_TITLE, None, text, 0, "caption")] + [
        replace(s, char_start=s.char_start + shift) for s in doc.sections
    ]
    return replace(doc, sections=sections)
