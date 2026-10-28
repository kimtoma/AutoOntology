# SPDX-License-Identifier: MIT
"""Every input format AutoOntology reads, in one place.

Two kinds of sources read files: a *files* source loads tables (structured path,
:mod:`autoontology.structured.loaders`), a *documents* source reads text
(:mod:`autoontology.unstructured.readers`). Some extensions belong to both (``.csv`` is a
table in a files source and a GFM table section in a documents source; ``.xls``/``.xlsx``
are sheets of tables or kordoc documents): the source decides the kind, not the
extension. The readers and loaders import their suffix sets from here, and
:func:`describe` is what ``/api/status`` shows the UI.
"""

from __future__ import annotations

from typing import Any

#: Structured (files source) suffixes by loader.
CSV_SUFFIXES: frozenset[str] = frozenset({".csv", ".tsv"})
PARQUET_SUFFIXES: frozenset[str] = frozenset({".parquet", ".pq"})
JSON_SUFFIXES: frozenset[str] = frozenset({".json", ".jsonl", ".ndjson"})
XLSX_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xlsm"})
XLS_SUFFIXES: frozenset[str] = frozenset({".xls"})
SQLITE_SUFFIXES: frozenset[str] = frozenset({".sqlite", ".sqlite3", ".db"})
STRUCTURED_SUFFIXES: frozenset[str] = (
    CSV_SUFFIXES | PARQUET_SUFFIXES | JSON_SUFFIXES | XLSX_SUFFIXES | XLS_SUFFIXES | SQLITE_SUFFIXES
)

#: Documents read in-process (no Node needed).
DOCUMENT_NATIVE_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".markdown", ".txt", ".html", ".htm", ".csv", ".tsv", ".doc"}
)
#: Documents parsed by the kordoc engine (Node >= 20, `tools install-kordoc`).
DOCUMENT_KORDOC_SUFFIXES: frozenset[str] = frozenset(
    {".hwp", ".hwpx", ".hml", ".hwpml", ".pdf", ".docx", ".pptx", ".xls", ".xlsx"}
)
#: Images: kordoc's built-in OCR (PP-OCRv5 korean models, `tools install-kordoc`).
IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})
DOCUMENT_SUFFIXES: frozenset[str] = (
    DOCUMENT_NATIVE_SUFFIXES | DOCUMENT_KORDOC_SUFFIXES | IMAGE_SUFFIXES
)

_STRUCTURED_NOTES = {
    **dict.fromkeys(CSV_SUFFIXES, "one table per file; first row is the header"),
    **dict.fromkeys(PARQUET_SUFFIXES, "one table per file"),
    **dict.fromkeys(JSON_SUFFIXES, "one table per file (array of objects or JSON lines)"),
    **dict.fromkeys(XLSX_SUFFIXES, "one table per sheet; first row is the header"),
    **dict.fromkeys(XLS_SUFFIXES, "Excel 97-2003; one table per sheet; first row is the header"),
    **dict.fromkeys(SQLITE_SUFFIXES, "one table per SQLite table"),
}
_DOCUMENT_NOTES = {
    ".md": "heading tree",
    ".markdown": "heading tree",
    ".txt": "plain text (UTF-8 or CP949)",
    ".html": "heading tree; scripts and navigation dropped",
    ".htm": "heading tree; scripts and navigation dropped",
    ".csv": "one table section (first row is the header, capped rows)",
    ".tsv": "one table section (first row is the header, capped rows)",
    ".doc": "Word 97-2003 text and tables (no images); encrypted files are refused",
    ".hwp": "Hancom HWP 5.x",
    ".hwpx": "Hancom HWPX",
    ".hml": "Hancom HWPML",
    ".hwpml": "Hancom HWPML",
    ".pdf": "text layer; scanned pages are OCR'd when the OCR models are installed",
    ".docx": "Word",
    ".pptx": "PowerPoint",
    ".xls": "Excel 97-2003 sheets as tables",
    ".xlsx": "Excel sheets as tables",
    **dict.fromkeys(IMAGE_SUFFIXES, "OCR (needs the OCR models); optional vision caption"),
}


def document_engine(suffix: str) -> str:
    """``native`` | ``kordoc`` | ``kordoc-ocr`` for a document suffix (lowercase, dotted)."""
    if suffix in IMAGE_SUFFIXES:
        return "kordoc-ocr"
    if suffix in DOCUMENT_NATIVE_SUFFIXES:
        return "native"
    if suffix in DOCUMENT_KORDOC_SUFFIXES:
        return "kordoc"
    raise KeyError(suffix)


def describe() -> dict[str, Any]:
    """``{"structured": {ext: entry}, "documents": {ext: entry}}`` where an entry is
    ``{"kind": "structured"|"document", "engine": "native"|"kordoc"|"kordoc-ocr",
    "note": str}``. An extension can appear under both kinds (e.g. ``.csv``)."""
    return {
        "structured": {
            ext: {"kind": "structured", "engine": "native", "note": _STRUCTURED_NOTES[ext]}
            for ext in sorted(STRUCTURED_SUFFIXES)
        },
        "documents": {
            ext: {"kind": "document", "engine": document_engine(ext), "note": _DOCUMENT_NOTES[ext]}
            for ext in sorted(DOCUMENT_SUFFIXES)
        },
    }
