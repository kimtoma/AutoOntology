# SPDX-License-Identifier: MIT
"""Section-aware chunking: ``Document -> [Chunk]``.

1. A section within ``max_tokens`` is one piece. A larger one is split on paragraph
   boundaries, then sentence boundaries (never inside a sentence when avoidable), with
   about ``overlap_tokens`` of trailing sentences repeated at the start of the next piece.
   Oversize tables split on rows and repeat their header rows.
2. Consecutive whole small sections of one document are packed together up to
   ``pack_tokens`` (fewer LLM calls; kordoc and Markdown both emit many tiny sections).
   A packed chunk's ``section_path`` is the common prefix of its sections' paths; a
   section whose heading is not already in its text gets its leaf heading as a line.

Token counts use tiktoken ``cl100k_base`` (an approximation for Korean, which is fine).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

from autoontology.unstructured.readers import PATH_SEP, Document, Section

DEFAULT_MAX_TOKENS = 1200
DEFAULT_OVERLAP_TOKENS = 100
#: Packing target. A ~500-token chunk of dense definitions took qwen3:8b 116 s to answer
#: (9 concepts, 14 relations); smaller chunks stay well inside LLM_INVOKE_TIMEOUT_S.
DEFAULT_PACK_TOKENS = 400

_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


@dataclass(frozen=True)
class Chunk:
    index: int
    total: int
    section_path: str
    page: int | None
    kind: str
    text: str
    tokens: int


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def env_sizes() -> tuple[int, int, int]:
    """``(max, overlap, pack)`` from ``AUTOONTOLOGY_CHUNK_TOKENS`` /
    ``AUTOONTOLOGY_CHUNK_OVERLAP_TOKENS`` / ``AUTOONTOLOGY_CHUNK_PACK_TOKENS``."""

    def read(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            value = int(raw) if raw else default
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
        return value

    return (
        read("AUTOONTOLOGY_CHUNK_TOKENS", DEFAULT_MAX_TOKENS),
        read("AUTOONTOLOGY_CHUNK_OVERLAP_TOKENS", DEFAULT_OVERLAP_TOKENS),
        read("AUTOONTOLOGY_CHUNK_PACK_TOKENS", DEFAULT_PACK_TOKENS),
    )


@dataclass
class _Piece:
    path: str
    page: int | None
    kind: str
    text: str
    tokens: int
    whole: bool  # an entire section (may be packed with neighbours)


def _split_units(text: str) -> list[str]:
    """Paragraphs; a paragraph too large for any chunk is split into sentences later."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip()]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Last resort for a single sentence larger than a chunk: cut it into pieces of at most
    *max_tokens* tokens, always at a character boundary. Decoding fixed token slices on
    their own would split multi-byte characters (one Hangul syllable can span two
    cl100k tokens) into U+FFFD; here the pieces concatenate back to *text* exactly."""
    enc = _encoding()
    max_tokens = max(1, max_tokens)
    pieces: list[str] = []
    rest = text
    while rest:
        ids = enc.encode(rest, disallowed_special=())
        if len(ids) <= max_tokens:
            pieces.append(rest)
            break
        # character index where token number max_tokens starts: a character boundary
        # (offsets of the whole text: a truncated token list may end inside a character)
        _, offsets = enc.decode_with_offsets(ids)
        cut = max(1, min(offsets[max_tokens], len(rest)))
        # re-encoding the prefix can merge differently: shrink until it fits
        while cut > 1 and count_tokens(rest[:cut]) > max_tokens:
            cut -= 1
        pieces.append(rest[:cut])
        rest = rest[cut:]
    return pieces


def _split_text(text: str, max_tokens: int, overlap: int) -> list[str]:
    units: list[str] = []
    for paragraph in _split_units(text):
        if count_tokens(paragraph) <= max_tokens:
            units.append(paragraph)
            continue
        for sentence in _sentences(paragraph):
            units.extend(
                [sentence]
                if count_tokens(sentence) <= max_tokens
                else _hard_split(sentence, max_tokens)
            )
    pieces: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join([*current, unit])
        if current and count_tokens(candidate) > max_tokens:
            pieces.append("\n\n".join(current))
            tail: list[str] = []
            for prev in reversed(_sentences(current[-1])):  # overlap: trailing sentences
                if count_tokens(" ".join([prev, *tail])) > overlap:
                    break
                tail.insert(0, prev)
            current = [" ".join(tail)] if tail else []
            if current and count_tokens("\n\n".join([*current, unit])) > max_tokens:
                current = []
        current.append(unit)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def _split_table(text: str, max_tokens: int) -> list[str]:
    lines = [line for line in text.split("\n") if line.strip()]
    header = (
        lines[:2]
        if len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ")
        else lines[:1]
    )
    pieces: list[str] = []
    current = list(header)
    for line in lines[len(header) :]:
        if len(current) > len(header) and count_tokens("\n".join([*current, line])) > max_tokens:
            pieces.append("\n".join(current))
            current = list(header)
        current.append(line)
    if len(current) > len(header) or not pieces:
        pieces.append("\n".join(current))
    return pieces


def _pieces(section: Section, max_tokens: int, overlap: int) -> list[_Piece]:
    tokens = count_tokens(section.text)
    if tokens <= max_tokens:
        return [_Piece(section.path, section.page, section.kind, section.text, tokens, True)]
    parts = (
        _split_table(section.text, max_tokens)
        if section.kind == "table"
        else _split_text(section.text, max_tokens, overlap)
    )
    return [
        _Piece(section.path, section.page, section.kind, p, count_tokens(p), False) for p in parts
    ]


def _common_path(paths: list[str]) -> str:
    split = [p.split(PATH_SEP) if p else [] for p in paths]
    common: list[str] = []
    for parts in zip(*split):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return PATH_SEP.join(common)


def _with_heading(piece: _Piece) -> str:
    leaf = piece.path.split(PATH_SEP)[-1] if piece.path else ""
    if not leaf or piece.text.lstrip().startswith("#") or piece.text.startswith(leaf):
        return piece.text
    return f"### {leaf}\n{piece.text}"


def chunk(
    document: Document,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    pack_tokens: int = DEFAULT_PACK_TOKENS,
) -> list[Chunk]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be > 0")
    pack_tokens = min(pack_tokens, max_tokens)
    pieces = [
        p
        for s in document.sections
        if s.text.strip()
        for p in _pieces(s, max_tokens, min(overlap_tokens, max_tokens // 2))
    ]
    groups: list[list[_Piece]] = []
    for piece in pieces:
        last = groups[-1] if groups else None
        if (
            last
            and piece.whole
            and all(p.whole for p in last)
            and count_tokens("\n\n".join(_with_heading(p) for p in [*last, piece])) <= pack_tokens
        ):
            last.append(piece)
        else:
            groups.append([piece])
    chunks: list[Chunk] = []
    for group in groups:
        if len(group) == 1:
            text, path = group[0].text, group[0].path
        else:
            path = _common_path([p.path for p in group])
            text = "\n\n".join(_with_heading(p) if p.path != path else p.text for p in group)
        kinds = {p.kind for p in group}
        chunks.append(
            Chunk(
                index=len(chunks),
                total=0,
                section_path=path,
                page=next((p.page for p in group if p.page), None),
                kind=kinds.pop() if len(kinds) == 1 else "mixed",
                text=text,
                tokens=count_tokens(text),
            )
        )
    total = len(chunks)
    return [Chunk(c.index, total, c.section_path, c.page, c.kind, c.text, c.tokens) for c in chunks]
