# SPDX-License-Identifier: MIT
import struct
from pathlib import Path

import pytest

from autoontology.unstructured import doc_word97, readers

FIXTURES = Path(__file__).parent / "fixtures" / "doc"


def test_korean_headings_paragraphs_and_table():
    path = FIXTURES / "manual_ko.doc"
    assert readers.discover_documents(path) == [path]
    doc = readers.read_document(path)
    assert doc.engine == "native" and doc.mime == "application/msword"
    assert doc.title == "거래처 관리 매뉴얼"
    assert [s.path for s in doc.sections] == ["거래처 관리 매뉴얼", "평가 기준", "거래 중지"]
    criteria = doc.sections[1].text.split("\n")
    assert criteria[0] == "평가 기준"
    assert "항목 | 가중치 | 담당" in criteria
    assert "품질 | 50 | 품질팀" in criteria and "납기 | 30 | 구매팀" in criteria
    assert "S001" in doc.sections[0].text
    assert doc.sections[2].text.endswith("거래를 중지한다.")
    assert all(not any(ord(c) < 32 and c != "\n" for c in s.text) for s in doc.sections)


def test_almost_empty_is_one_section():
    doc = readers.read_document(FIXTURES / "almost_empty.doc")
    assert doc.title == "almost_empty"
    assert [(s.path, s.text) for s in doc.sections] == [("", "메모")]


def test_field_codes_keep_only_their_result():
    (section,) = readers.read_document(FIXTURES / "field_code.doc").sections
    assert section.text == "자세한 기준은 사내 정책 포털에서 확인한다.\n문서 번호 1 끝."
    assert "HYPERLINK" not in section.text


def test_normalize():
    raw = "a\x13 REF x \x13 nested \x14in\x15 \x14res\x15b\rc\x0bd\x07e\x07\x07f\x01\x08g"
    assert doc_word97.normalize(raw) == "aresb\nc\nd | e\nfg"


def _synthetic(pieces: list[tuple[str, bool]], ccp_text: int) -> tuple[bytes, bytes]:
    """A minimal FIB + CLX: *pieces* are (text, compressed) placed after the FIB."""
    csw, cslw, cb = 14, 22, 93
    fib_len = 32 + 2 + csw * 2 + 2 + cslw * 4 + 2 + cb * 8
    word = bytearray(fib_len)
    struct.pack_into("<HH", word, 0, 0xA5EC, 0xC1)
    struct.pack_into("<H", word, 10, 0x0200)  # fWhichTblStm -> 1Table
    struct.pack_into("<H", word, 32, csw)
    lw = 32 + 2 + csw * 2
    struct.pack_into("<H", word, lw, cslw)
    struct.pack_into("<i", word, lw + 2 + 3 * 4, ccp_text)
    blob = lw + 2 + cslw * 4
    struct.pack_into("<H", word, blob, cb)
    cps, pcds = [0], b""
    for text, compressed in pieces:
        fc = len(word)
        if compressed:
            word += text.encode("cp1252")
            fc_field = (fc * 2) | 0x40000000
        else:
            word += text.encode("utf-16-le")
            fc_field = fc
        cps.append(cps[-1] + len(text))
        pcds += struct.pack("<HIH", 0, fc_field, 0)
    plc = struct.pack(f"<{len(cps)}I", *cps) + pcds
    clx = b"\x01" + struct.pack("<h", 2) + b"\x00\x00" + b"\x02" + struct.pack("<I", len(plc)) + plc
    table = b"\x00" * 16 + clx
    struct.pack_into("<II", word, blob + 2 + 33 * 8, 16, len(clx))
    return bytes(word), table


def test_piece_table_mixes_compressed_and_unicode_pieces_in_cp_order():
    word, table = _synthetic([("Caf\xe9 ", True), ("거래처\r", False), ("footnote", True)], 9)
    assert doc_word97._main_text(word, table) == "Caf\xe9 거래처\r"


class _FakeOle:
    def __init__(self, streams):
        self.streams = streams

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exists(self, name):
        return name in self.streams

    def openstream(self, name):
        import io

        return io.BytesIO(self.streams[name])


def test_encrypted_and_corrupt(tmp_path, monkeypatch):
    import olefile

    path = tmp_path / "x.doc"
    path.write_bytes(b"x")
    word, table = _synthetic([("hello\r", True)], 6)
    monkeypatch.setattr(olefile, "isOleFile", lambda _p: True)
    enc = bytearray(word)
    struct.pack_into("<H", enc, 10, 0x0300)
    monkeypatch.setattr(olefile, "OleFileIO", lambda _p: _FakeOle({"WordDocument": bytes(enc)}))
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path)
    assert err.value.code == "DOC_ENCRYPTED"
    broken = table[:20]  # CLX cut short
    monkeypatch.setattr(
        olefile, "OleFileIO", lambda _p: _FakeOle({"WordDocument": word, "1Table": broken})
    )
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path)
    assert err.value.code == "DOC_PARSE_FAILED" and "docx" in err.value.remedy
    monkeypatch.setattr(
        olefile, "OleFileIO", lambda _p: _FakeOle({"WordDocument": word, "1Table": table})
    )
    assert readers.read_document(path).sections[0].text == "hello"


@pytest.mark.parametrize("cut", [0, 512, 4096])
def test_truncated_and_garbage_files_fail_cleanly(tmp_path, cut):
    data = (FIXTURES / "manual_ko.doc").read_bytes()
    path = tmp_path / "bad.doc"
    path.write_bytes(data[:cut] if cut else b"\xd0\xcf\x11\xe0 not really OLE" * 20)
    with pytest.raises(readers.ReadError) as err:
        readers.read_document(path)
    assert err.value.code == "DOC_PARSE_FAILED"
    assert err.value.remedy == "Save the file as .docx and add it again"
