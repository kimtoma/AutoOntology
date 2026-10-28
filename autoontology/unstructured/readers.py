"""Document readers: ``path -> Document(sections)``.

Markdown, plain text and HTML are read natively (markdown-it-py heading tree, bs4).
Everything else (HWP/HWPX/HWPML, PDF, DOCX, PPTX, XLS/XLSX, images) goes to kordoc
(:mod:`autoontology.unstructured.kordoc`), whose structure-aware chunks become sections
(``breadcrumb`` -> section path, ``page`` -> page). Document text is untrusted data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from autoontology.config import REPO_ROOT
from autoontology.unstructured import kordoc

NATIVE_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown", ".txt", ".html", ".htm"})
SUPPORTED_EXTENSIONS: frozenset[str] = NATIVE_EXTENSIONS | kordoc.EXTENSIONS
PATH_SEP = " > "

MIME_TYPES: dict[str, str] = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
    ".html": "text/html", ".htm": "text/html", ".pdf": "application/pdf",
    ".hwp": "application/x-hwp", ".hwpx": "application/hwp+zip", ".hml": "application/x-hwpml",
    ".hwpml": "application/x-hwpml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp",
}


class ReadError(ValueError):
    """A document could not be read (the run records it and moves on)."""

    def __init__(self, message: str, *, code: str = "READ_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Section:
    path: str  # heading path, "제1장 총칙 > 제2조(정의)"; "" before the first heading
    page: int | None
    text: str
    char_start: int
    kind: str = "text"  # "text" | "table"


@dataclass(frozen=True)
class Document:
    title: str
    path: Path
    mime: str
    sections: list[Section] = field(default_factory=list)
    engine: str = "native"  # "native" | "kordoc"
    pages: int | None = None


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def discover_documents(target: Path, *, recursive: bool = False) -> list[Path]:
    """Supported files under *target* (a file or a directory), sorted, hidden ones skipped."""
    target = Path(target)
    if target.is_file():
        if not is_supported(target):
            raise ReadError(f"unsupported document type {target.suffix or '(none)'}: {target} "
                            f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})",
                            code="UNSUPPORTED_FORMAT")
        return [target]
    if not target.is_dir():
        raise ReadError(f"no such file or directory: {target}", code="FILE_NOT_FOUND")
    pattern = "**/*" if recursive else "*"
    files = [p for p in target.glob(pattern)
             if p.is_file() and is_supported(p)
             and not any(part.startswith(".") for part in p.relative_to(target).parts)]
    return sorted(files)


def read_document(path: Path, *, home: Path | None = None,
                  engine: kordoc.KordocEngine | None = None) -> Document:
    path = Path(path)
    ext = path.suffix.lower()
    mime = MIME_TYPES.get(ext, "application/octet-stream")
    if ext in (".md", ".markdown"):
        return read_markdown(path)
    if ext == ".txt":
        text = _read_text(path)
        sections = [Section("", None, text.strip(), 0)] if text.strip() else []
        return Document(path.stem, path, mime, sections)
    if ext in (".html", ".htm"):
        return read_html(path)
    if ext in kordoc.EXTENSIONS:
        if engine is None:
            try:
                engine = kordoc.resolve_engine(home or REPO_ROOT / "data")
            except kordoc.KordocUnavailable as exc:
                raise ReadError(f"{path.name}: {exc.message}", code=exc.code) from exc
        return read_with_kordoc(path, engine, mime)
    raise ReadError(f"unsupported document type {ext or '(none)'}: {path.name}",
                    code="UNSUPPORTED_FORMAT")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    raise ReadError(f"{path.name}: not UTF-8 or CP949 text", code="BAD_ENCODING")


# --- Markdown ---------------------------------------------------------------------------


def read_markdown(path: Path) -> Document:
    from markdown_it import MarkdownIt

    source = _read_text(path)
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    tokens = MarkdownIt("commonmark").enable("table").parse(source)
    headings: list[tuple[int, int, str]] = []  # (line, level, title)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            title = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
            headings.append((tok.map[0], int(tok.tag[1]), title))
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    bounds = [h[0] for h in headings] + [len(lines)]
    first = bounds[0]
    if "\n".join(lines[:first]).strip():
        sections.append(Section("", None, "\n".join(lines[:first]).strip(), 0))
    for index, (line, level, title) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        body = "\n".join(lines[line:bounds[index + 1]]).strip()
        if body:
            sections.append(Section(PATH_SEP.join(t for _, t in stack), None, body,
                                    offsets[line]))
    h1 = next((t for _, lvl, t in headings if lvl == 1 and t), "")
    return Document(h1 or path.stem, path, MIME_TYPES[path.suffix.lower()], sections)


# --- HTML -------------------------------------------------------------------------------

_BLOCKS = ("p", "li", "pre", "blockquote", "tr", "dt", "dd", "td", "th", "caption")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def read_html(path: Path) -> Document:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_read_text(path), "html.parser")
    for tag in soup(["script", "style", "nav", "noscript", "template", "iframe", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    root = soup.body or soup
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    offset = 0

    def flush() -> None:
        nonlocal offset
        text = "\n".join(buf).strip()
        if text:
            sections.append(Section(PATH_SEP.join(t for _, t in stack), None, text, offset))
            offset += len(text) + 1
        buf.clear()

    for el in root.find_all([*_HEADINGS, *_BLOCKS]):
        if el.name not in _HEADINGS and el.find_parent(_BLOCKS):
            continue  # nested block: its text is part of the outer block
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in _HEADINGS:
            flush()
            level = int(el.name[1])
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            buf.append(text)
        else:
            if el.name == "tr":
                text = "| " + " | ".join(c.get_text(" ", strip=True)
                                         for c in el.find_all(["td", "th"])) + " |"
            buf.append(text)
    flush()
    h1 = soup.find("h1")
    return Document(title or (h1.get_text(" ", strip=True) if h1 else "") or path.stem, path,
                    MIME_TYPES[path.suffix.lower()], sections)


# --- kordoc -----------------------------------------------------------------------------

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def read_with_kordoc(path: Path, engine: kordoc.KordocEngine, mime: str) -> Document:
    try:
        chunks = kordoc.parse_chunks(engine, path)
    except kordoc.KordocError as exc:
        raise ReadError(f"{path.name}: {exc.message}", code=exc.code) from exc
    sections: list[Section] = []
    offset = 0
    for chunk in chunks:
        if chunk.type == "heading":
            continue  # its text is the breadcrumb of the chunks that follow
        text = _IMAGE_RE.sub("", chunk.text).strip()
        if not text:
            continue
        sections.append(Section(PATH_SEP.join(c.strip() for c in chunk.breadcrumb if c.strip()),
                                chunk.page, text, offset,
                                "table" if chunk.type == "table" else "text"))
        offset += len(text) + 1
    pages = max((c.page for c in chunks if c.page), default=None)
    return Document(path.stem, path, mime, sections, engine="kordoc", pages=pages)
