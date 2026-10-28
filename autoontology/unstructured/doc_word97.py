# SPDX-License-Identifier: MIT
"""Legacy Word 97-2003 ``.doc`` reader (pure Python, ``olefile``).

The binary format ([MS-DOC]) keeps the text in the ``WordDocument`` stream, addressed by
the *piece table* stored in the ``0Table``/``1Table`` stream:

1. FIB (File Information Block) at offset 0 of ``WordDocument``: ``wIdent`` 0xA5EC,
   flags (``fEncrypted`` 0x0100, ``fWhichTblStm`` 0x0200 selects ``1Table``), then the
   variable-length ``FibRgW``/``FibRgLw`` (``ccpText`` = characters of the main document)
   and ``FibRgFcLcb`` (``fcClx``/``lcbClx`` = where the CLX lives in the table stream).
2. CLX = optional ``Prc`` records (0x01) then the ``Pcdt`` (0x02): a PlcPcd of n+1
   character positions and n piece descriptors. Each piece's ``fc`` has bit 30 set when
   its text is "compressed" (one cp1252 byte per character at ``fc/2``), else UTF-16LE.
3. The pieces in CP order, cut at ``ccpText``, are the main text (footnotes, headers and
   comments that follow it are ignored).

Then the special characters are normalised: paragraph/line marks to newlines, table cell
marks to `` | `` (a row's double mark to a newline), field codes reduced to their result.
The document text is untrusted data.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from autoontology.unstructured.readers import (
    DOC_MAX_CHARS,
    MIME_TYPES,
    Document,
    ReadError,
    Section,
    check_size,
)

W_IDENT = 0xA5EC
F_ENCRYPTED = 0x0100
F_WHICH_TBL_STM = 0x0200
#: index of the fcClx/lcbClx pair in FibRgFcLcb97
CLX_PAIR_INDEX = 33
#: index of ccpText in FibRgLw97
CCP_TEXT_INDEX = 3
REMEDY = "Save the file as .docx and add it again"
HEADING_MAX_CHARS = 40
#: Hard limits of the parser (hostile input must fail fast, never loop or blow up):
#: pieces in the piece table, characters of the main text (readers.DOC_MAX_CHARS), a Prc's cbGrpprl ([MS-DOC]
#: 2.9.209: > 0 and <= 0x3FA2) and the field nesting tracked exactly.
MAX_PIECES = 10_000
PRC_MAX_CB = 0x3FA2
MAX_FIELD_DEPTH = 256

_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]+")


def _fail(path: Path, why: str) -> ReadError:
    return ReadError(
        f"{path.name}: not a readable Word 97-2003 document ({why})",
        code="DOC_PARSE_FAILED",
        remedy=REMEDY,
    )


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def extract_text(path: Path, *, max_bytes: int | None = None) -> str:
    """The raw main-document text (Word special characters still in place)."""
    import olefile

    check_size(path, max_bytes)
    try:
        if not olefile.isOleFile(str(path)):
            raise _fail(path, "not an OLE compound file")
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("WordDocument"):
                raise _fail(path, "no WordDocument stream")
            word = ole.openstream("WordDocument").read()
            if len(word) < 34 or _u16(word, 0) != W_IDENT:
                raise _fail(path, "bad FIB signature")
            flags = _u16(word, 10)
            if flags & F_ENCRYPTED:
                raise ReadError(
                    f"{path.name}: the document is password-protected",
                    code="DOC_ENCRYPTED",
                    remedy="Remove the password in Word, save as .docx and add it again",
                )
            table_name = "1Table" if flags & F_WHICH_TBL_STM else "0Table"
            if not ole.exists(table_name):
                raise _fail(path, f"no {table_name} stream")
            table = ole.openstream(table_name).read()
    except ReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - olefile raises many types on garbage
        raise _fail(path, type(exc).__name__) from exc
    try:
        return _main_text(word, table)
    except (struct.error, IndexError, ValueError) as exc:
        raise _fail(path, f"corrupt structure: {exc}") from exc


def _main_text(word: bytes, table: bytes) -> str:
    """The main text from the piece table. Every length and offset is checked against
    its stream before use, the character positions must increase from 0, pieces may not
    overlap, and ``ccpText`` cannot exceed the stream (a character takes at least one
    byte) nor :data:`DOC_MAX_CHARS`: the output is at most one character per byte of
    ``WordDocument``, and the work is linear in the stream sizes."""
    pos = 32
    csw = _u16(word, pos)
    pos += 2 + csw * 2
    cslw = _u16(word, pos)
    lw = pos + 2
    if cslw <= CCP_TEXT_INDEX:
        raise ValueError("FibRgLw too short")
    ccp_text = struct.unpack_from("<i", word, lw + CCP_TEXT_INDEX * 4)[0]
    pos = lw + cslw * 4
    cb_fclcb = _u16(word, pos)
    blob = pos + 2
    if cb_fclcb <= CLX_PAIR_INDEX:
        raise ValueError("FibRgFcLcb too short")
    fc_clx = _u32(word, blob + CLX_PAIR_INDEX * 8)
    lcb_clx = _u32(word, blob + CLX_PAIR_INDEX * 8 + 4)
    if ccp_text < 0 or ccp_text > len(word):
        raise ValueError(f"ccpText {ccp_text} does not fit the WordDocument stream")
    if ccp_text > DOC_MAX_CHARS:
        raise ValueError(f"the text has more than {DOC_MAX_CHARS:,} characters")
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise ValueError("CLX outside the table stream")
    clx = table[fc_clx : fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:  # Prc: skip (i strictly increases)
        cb = _u16(clx, i + 1)
        if not 0 < cb <= PRC_MAX_CB:
            raise ValueError(f"bad Prc size {cb}")
        i += 3 + cb
    if i >= len(clx) or clx[i] != 0x02:
        raise ValueError("no Pcdt in CLX")
    lcb = _u32(clx, i + 1)
    if lcb > len(clx) - (i + 5):
        raise ValueError("PlcPcd outside the CLX")
    plc = clx[i + 5 : i + 5 + lcb]
    if lcb < 16 or (lcb - 4) % 12:
        raise ValueError("bad PlcPcd size")
    n = (lcb - 4) // 12
    if n > MAX_PIECES:
        raise ValueError(f"{n} pieces in the piece table (at most {MAX_PIECES:,})")
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    if cps[0] != 0 or any(b < a for a, b in zip(cps, cps[1:], strict=False)):
        raise ValueError("piece character positions do not increase from 0")
    # validate every piece (bounds, overlap) before decoding anything
    pieces: list[tuple[int, int, bool]] = []  # (byte offset, char count, compressed)
    for k in range(n):
        start, end = cps[k], min(cps[k + 1], ccp_text)
        if start >= ccp_text:
            break
        if end <= start:
            continue
        fc_raw = _u32(plc, 4 * (n + 1) + k * 8 + 2)
        compressed = bool(fc_raw & 0x40000000)
        offset = (fc_raw & 0x3FFFFFFF) // 2 if compressed else fc_raw & 0x3FFFFFFF
        count = end - start
        if offset + count * (1 if compressed else 2) > len(word):
            raise ValueError("piece outside the WordDocument stream")
        pieces.append((offset, count, compressed))
    spans = sorted((off, off + cnt * (1 if comp else 2)) for off, cnt, comp in pieces)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        if next_start < prev_end:
            raise ValueError("pieces overlap")
    parts: list[str] = []
    for offset, count, compressed in pieces:
        if compressed:
            parts.append(word[offset : offset + count].decode("cp1252", errors="replace"))
        else:
            chunk = word[offset : offset + 2 * count]
            parts.append(chunk.decode("utf-16-le", errors="replace"))
    return "".join(parts)


def _strip_fields(text: str) -> str:
    """Keep only the result of each field (between 0x14 and 0x15); nested fields too.

    One pass: *stack* holds, per open field, whether it is still in its code part, and
    *in_code* counts those, so a character is kept when no open field is in code. Fields
    nested deeper than :data:`MAX_FIELD_DEPTH` (only in hostile or corrupt files) are only
    counted, assuming each 0x14 ends the code of the innermost of them."""
    out: list[str] = []
    stack = bytearray()  # 1 = still in its code part
    in_code = 0
    deep = deep_code = 0  # fields beyond MAX_FIELD_DEPTH; how many of them in code
    for ch in text:
        if ch == "\x13":
            if len(stack) < MAX_FIELD_DEPTH:
                stack.append(1)
                in_code += 1
            else:
                deep += 1
                deep_code += 1
        elif ch == "\x14":
            if deep:
                deep_code = max(0, deep_code - 1)
            elif stack and stack[-1]:
                stack[-1] = 0
                in_code -= 1
        elif ch == "\x15":
            if deep:
                deep -= 1
                deep_code = min(deep_code, deep)
            elif stack:
                in_code -= stack.pop()
        elif not in_code and not deep_code:
            out.append(ch)
    return "".join(out)


def normalize(text: str) -> str:
    text = _strip_fields(text)
    text = text.replace("\x07\x07", "\n").replace("\x07", " | ")
    text = text.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    text = _CONTROL.sub("", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _is_heading(line: str, following: str | None) -> bool:
    return (
        following is not None
        and 0 < len(line) <= HEADING_MAX_CHARS
        and not line.endswith((".", "。", ":", ",", "|"))
        and " | " not in line
        and len(following) > len(line)
    )


def split_sections(text: str) -> tuple[str, list[Section]]:
    """``(first heading or "", sections)``: a short line without a trailing period that a
    longer paragraph follows starts a section; otherwise the text is one section."""
    paragraphs = [p for p in text.split("\n") if p.strip()]
    groups: list[tuple[str, list[str]]] = [("", [])]
    for idx, para in enumerate(paragraphs):
        nxt = paragraphs[idx + 1] if idx + 1 < len(paragraphs) else None
        if _is_heading(para, nxt):
            groups.append((para, [para]))
        else:
            groups[-1][1].append(para)
    sections: list[Section] = []
    offset = 0
    for heading, body in groups:
        if not body:
            continue
        chunk = "\n".join(body)
        sections.append(Section(heading, None, chunk, offset))
        offset += len(chunk) + 1
    first = groups[1][0] if len(groups) > 1 and not groups[0][1] else ""
    return first, sections


def read_doc(path: Path, *, max_bytes: int | None = None) -> Document:
    path = Path(path)
    text = normalize(extract_text(path, max_bytes=max_bytes))
    title, sections = split_sections(text)
    return Document(title or path.stem, path, MIME_TYPES[".doc"], sections)
