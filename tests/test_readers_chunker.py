# SPDX-License-Identifier: MIT
import re
import shutil

import pytest

from autoontology.unstructured import chunker, kordoc, readers
from autoontology.unstructured.readers import Document, Section

MD = """\
머리말 문단.

# 계약관리규정

## 제1장 총칙

### 제1조(목적)
이 규정은 계약 관리에 관한 사항을 정한다.

### 제2조(정의)
1. "계약"이란 고객과 체결한 약정을 말한다.
2. "계약금액"이란 계약서에 적힌 금액을 말한다.

## 제2장 승인

| 금액 | 승인권자 |
| --- | --- |
| 1억 미만 | 팀장 |
"""


def test_markdown_heading_tree(tmp_path):
    path = tmp_path / "규정.md"
    path.write_text(MD, encoding="utf-8")
    doc = readers.read_document(path)
    assert doc.title == "계약관리규정" and doc.engine == "native" and doc.mime == "text/markdown"
    paths = [s.path for s in doc.sections]
    assert paths == [
        "",
        "계약관리규정",
        "계약관리규정 > 제1장 총칙",
        "계약관리규정 > 제1장 총칙 > 제1조(목적)",
        "계약관리규정 > 제1장 총칙 > 제2조(정의)",
        "계약관리규정 > 제2장 승인",
    ]
    defs = doc.sections[4]
    assert defs.text.startswith("### 제2조(정의)") and "계약금액" in defs.text
    assert MD[defs.char_start :].startswith("### 제2조(정의)")
    assert "| 1억 미만 | 팀장 |" in doc.sections[5].text


def test_txt_and_html(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("첫 줄\n\n둘째 문단", encoding="utf-8")
    doc = readers.read_document(txt)
    assert [(s.path, s.text) for s in doc.sections] == [("", "첫 줄\n\n둘째 문단")]
    html = tmp_path / "b.html"
    html.write_text(
        """<html><head><title>청구 안내</title><style>p{}</style></head><body>
      <nav><a>메뉴</a></nav><h1>청구</h1><p>청구서는 매월 <b>발행</b>한다.</p>
      <ul><li><p>연체 시 독촉</p></li></ul><script>alert(1)</script>
      <h2>수납</h2><table><tr><th>항목</th><th>기한</th></tr><tr><td>수납</td><td>30일</td>
      </tr></table></body></html>""",
        encoding="utf-8",
    )
    doc = readers.read_document(html)
    assert doc.title == "청구 안내"
    assert [s.path for s in doc.sections] == ["청구", "청구 > 수납"]
    assert doc.sections[0].text == "청구\n청구서는 매월 발행 한다.\n연체 시 독촉"
    assert "| 항목 | 기한 |" in doc.sections[1].text and "메뉴" not in doc.sections[0].text
    assert "alert" not in doc.sections[0].text


def test_discover_documents(tmp_path):
    (tmp_path / "a.md").write_text("# a")
    (tmp_path / "b.hwp").write_bytes(b"x")
    (tmp_path / "c.csv").write_text("x")
    (tmp_path / "e.zip").write_text("x")
    (tmp_path / ".hidden.md").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.pdf").write_bytes(b"x")
    assert [p.name for p in readers.discover_documents(tmp_path)] == ["a.md", "b.hwp", "c.csv"]
    assert [p.name for p in readers.discover_documents(tmp_path, recursive=True)] == [
        "a.md",
        "b.hwp",
        "c.csv",
        "d.pdf",
    ]
    with pytest.raises(readers.ReadError, match="unsupported"):
        readers.discover_documents(tmp_path / "e.zip")


def test_kordoc_formats_need_the_engine(tmp_path, monkeypatch):
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    f = tmp_path / "a.hwp"
    f.write_bytes(b"x")
    with pytest.raises(readers.ReadError, match="install-kordoc") as err:
        readers.read_document(f, home=tmp_path / "home")
    assert err.value.code == "ENGINE_MISSING"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_kordoc_chunks_become_sections(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text(
        """console.log(JSON.stringify([
      {id:'c1', type:'heading', breadcrumb:[], text:'# 사업계획', page:1},
      {id:'c2', type:'text', breadcrumb:['사업계획'], text:'![image](i.jpg)', page:1},
      {id:'c3', type:'text', breadcrumb:['사업계획', ' 범위 '], text:'사업 범위는 다음과 같다. ![x](y.png)', page:2},
      {id:'c4', type:'table', breadcrumb:['사업계획'], text:'| 항목 | 값 |\\n| --- | --- |', page:3}]))
    """,
        encoding="utf-8",
    )
    monkeypatch.setenv("KORDOC_CLI", str(cli))
    f = tmp_path / "plan.hwp"
    f.write_bytes(b"x")
    doc = readers.read_document(f, home=tmp_path / "home")
    assert doc.engine == "kordoc" and doc.pages == 3 and doc.title == "plan"
    assert [(s.path, s.page, s.kind, s.text) for s in doc.sections] == [
        ("사업계획 > 범위", 2, "text", "사업 범위는 다음과 같다."),
        ("사업계획", 3, "table", "| 항목 | 값 |\n| --- | --- |"),
    ]
    assert isinstance(kordoc.resolve_engine(tmp_path / "home"), kordoc.KordocEngine)


def _doc(*sections):
    return Document("t", readers.Path("t.md"), "text/markdown", list(sections))


def test_chunker_packs_small_sections_under_common_path():
    doc = _doc(
        Section("규정 > 제1조", None, "### 제1조\n목적을 정한다.", 0),
        Section("규정 > 제2조", None, "정의를 둔다.", 20),
        Section("규정 > 제3조", 4, "적용 범위.", 40),
    )
    chunks = chunker.chunk(doc, max_tokens=200, pack_tokens=200)
    assert len(chunks) == 1
    [c] = chunks
    assert c.section_path == "규정" and c.page == 4 and (c.index, c.total) == (0, 1)
    assert c.text == "### 제1조\n목적을 정한다.\n\n### 제2조\n정의를 둔다.\n\n### 제3조\n적용 범위."
    assert len(chunker.chunk(doc, max_tokens=200, pack_tokens=0)) == 3


def test_chunker_splits_oversize_on_sentences_with_overlap():
    sentences = [f"제{i}항 고객은 계약 조건을 준수하여야 한다." for i in range(60)]
    doc = _doc(Section("규정", 1, " ".join(sentences), 0))
    chunks = chunker.chunk(doc, max_tokens=120, overlap_tokens=30, pack_tokens=120)
    assert len(chunks) > 3 and all(c.tokens <= 120 for c in chunks)
    assert all(c.section_path == "규정" and c.page == 1 for c in chunks)
    for c in chunks:  # never split inside a sentence
        assert c.text.rstrip().endswith("한다.")
    first_last = chunks[0].text.split("\n\n")[-1].split(" 제")[-1]
    assert first_last.split("항")[0] in chunks[1].text.split("\n\n")[0]  # overlap
    joined = " ".join(c.text for c in chunks)
    assert all(s in joined for s in sentences)


def test_chunker_splits_tables_repeating_header():
    rows = "\n".join(f"| 항목{i} | 값{i} |" for i in range(80))
    doc = _doc(Section("표", None, "| 항목 | 값 |\n| --- | --- |\n" + rows, 0, "table"))
    chunks = chunker.chunk(doc, max_tokens=100)
    assert len(chunks) > 2 and all(c.kind == "table" for c in chunks)
    assert all(c.text.startswith("| 항목 | 값 |\n| --- | --- |\n| 항목") for c in chunks)
    assert sum(len(re.findall(r"값\d+", c.text)) for c in chunks) == 80


def test_hard_split_keeps_korean_text_intact():
    """Review #16: fixed token slices decoded on their own split Hangul syllables."""
    from autoontology.unstructured import chunker

    text = "한글문서추출검증" * 1000
    pieces = chunker._hard_split(text, 1200)
    assert "".join(pieces) == text
    assert not any("�" in p for p in pieces)
    assert all(chunker.count_tokens(p) <= 1200 for p in pieces) and len(pieces) > 1
    mixed = "계약 contract 金額 🚀 " * 500
    assert "".join(chunker._hard_split(mixed, 97)) == mixed


# --- CSV/TSV documents ------------------------------------------------------------------


def test_csv_document_is_one_gfm_table(tmp_path):
    path = tmp_path / "담당자연락망.csv"
    path.write_text(
        "이름,부서,내선\n김하나,영업|1팀,1201\n이두리,재무팀,1302\n", encoding="utf-8-sig"
    )
    assert readers.discover_documents(path) == [path]
    doc = readers.read_document(path)
    assert doc.title == "담당자연락망" and doc.mime == "text/csv" and doc.engine == "native"
    assert len(doc.sections) == 1
    (section,) = doc.sections
    assert section.kind == "table" and section.path == ""
    assert section.text.split("\n") == [
        "| 이름 | 부서 | 내선 |",
        "| --- | --- | --- |",
        "| 김하나 | 영업\\|1팀 | 1201 |",
        "| 이두리 | 재무팀 | 1302 |",
    ]


def test_tsv_cp949_and_row_cap(tmp_path, monkeypatch):
    path = tmp_path / "단가.tsv"
    rows = ["코드\t품명\t단가"] + [f"P{i:03d}\t품목, {i}\t{i * 100}" for i in range(1, 8)]
    path.write_bytes(("\r\n".join(rows) + "\r\n").encode("cp949"))
    doc = readers.read_document(path, max_table_rows=5)
    lines = doc.sections[0].text.split("\n")
    assert lines[0] == "| 코드 | 품명 | 단가 |"
    assert lines[2] == "| P001 | 품목, 1 | 100 |"
    assert len(lines) == 2 + 5 + 1
    assert lines[-1] == "… 2 more rows (7 total)"
    monkeypatch.setenv("AUTOONTOLOGY_DOC_TABLE_MAX_ROWS", "3")
    assert readers.read_document(path).sections[0].text.endswith("… 4 more rows (7 total)")


def test_csv_ragged_rows_and_empty(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b\n1\n2,3,4\n", encoding="utf-8")
    lines = readers.read_document(path).sections[0].text.split("\n")
    # rows are padded or cut to the header width (well-formed GFM)
    assert lines == ["| a | b |", "| --- | --- |", "| 1 |  |", "| 2 | 3 |"]
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert readers.read_document(empty).sections == []


def test_large_csv_is_streamed_with_bounded_memory(tmp_path):
    import tracemalloc

    path = tmp_path / "big.csv"
    row = "가나다라마바사아자차카타파하,0123456789,abcdefghijklmnopqrstuvwxyz\n"
    count = 20 * 1024 * 1024 // len(row.encode("utf-8")) + 1
    with path.open("w", encoding="utf-8") as fh:
        fh.write("이름,번호,메모\n")
        for _ in range(count):
            fh.write(row)
    assert path.stat().st_size > 20 * 1024 * 1024
    tracemalloc.start()
    try:
        doc = readers.read_document(path, max_table_rows=50)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    lines = doc.sections[0].text.split("\n")
    assert len(lines) == 2 + 50 + 1
    assert lines[-1] == f"… {count - 50} more rows ({count} total)"
    assert peak < 100 * 1024 * 1024, peak


def test_csv_falls_back_to_cp949_past_the_kept_rows(tmp_path):
    path = tmp_path / "late.csv"
    rows = ["code,name"] + [f"A{i},ascii" for i in range(300)] + ["B1,품목"]
    path.write_bytes(("\n".join(rows) + "\n").encode("cp949"))
    lines = readers.read_document(path, max_table_rows=2).sections[0].text.split("\n")
    assert lines[2] == "| A0 | ascii |" and lines[-1] == "… 299 more rows (301 total)"


@pytest.mark.parametrize(
    "name, data",
    [
        ("a.csv", b"a,b\n" + b"1,2\n" * 400),
        ("a.tsv", b"a\tb\n" + b"1\t2\n" * 400),
        ("a.txt", b"x" * 2000),
        ("a.md", b"# t\n" + b"x" * 2000),
        ("a.html", b"<p>" + b"x" * 2000 + b"</p>"),
        ("a.doc", b"\xd0\xcf\x11\xe0" + b"\0" * 2000),
    ],
)
def test_documents_over_the_byte_cap_fail(tmp_path, monkeypatch, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    monkeypatch.setenv("AUTOONTOLOGY_DOC_MAX_BYTES", "1000")
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path)
    assert err.value.code == "FILE_TOO_LARGE" and "AUTOONTOLOGY_DOC_MAX_BYTES" in str(err.value)
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path, max_bytes=1000)
    assert err.value.code == "FILE_TOO_LARGE"
    monkeypatch.delenv("AUTOONTOLOGY_DOC_MAX_BYTES")
    assert readers.doc_max_bytes() == readers.DEFAULT_DOC_MAX_BYTES == 50 * 1024 * 1024


@pytest.mark.parametrize("var", ["AUTOONTOLOGY_DOC_TABLE_MAX_ROWS", "AUTOONTOLOGY_DOC_MAX_BYTES"])
def test_bad_env_values_are_read_errors(tmp_path, monkeypatch, var):
    path = tmp_path / "a.csv"
    path.write_text("a\n1\n", encoding="utf-8")
    monkeypatch.setenv(var, "lots")
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path)
    assert err.value.code == "BAD_CONFIG" and var in str(err.value)


def test_wide_csv_header_is_capped(tmp_path):
    import time
    import tracemalloc

    path = tmp_path / "wide.csv"
    cols = 20_000_000  # a 40 MB header, within the 50 MB byte cap
    path.write_text("h," * cols + "end\n" + "1,2,3\n", encoding="utf-8")
    started = time.perf_counter()
    tracemalloc.start()
    try:
        doc = readers.read_document(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - started < 5
    assert peak < 100 * 1024 * 1024, peak
    (section,) = doc.sections
    header, sep, row = section.text.split("\n")
    width = readers.DOC_TABLE_MAX_COLS
    assert header.startswith("| h | h |")
    assert header.endswith(f"| … {cols + 1 - width} more columns |")
    assert sep.count("---") == width + 1
    assert row.startswith("| 1 | 2 | 3 |") and row.count("|") == width + 2
    assert len(section.text) < 10_000


def test_csv_section_text_is_capped(tmp_path, monkeypatch):
    path = tmp_path / "long.csv"
    path.write_text("a,b\n" + "".join(f"{'가' * 50},{i}\n" for i in range(300)), encoding="utf-8")
    monkeypatch.setattr(readers, "DOC_MAX_CHARS", 2_000)
    text = readers.read_document(path).sections[0].text
    assert len(text) <= 2_000
    assert text.endswith("… table truncated at 2,000 characters")
