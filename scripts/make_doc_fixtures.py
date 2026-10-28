# SPDX-License-Identifier: MIT
"""Regenerate the legacy Word ``.doc`` test fixtures in ``tests/fixtures/doc/``.

macOS only: ``/usr/bin/textutil -convert doc`` writes Word 97-2003 binaries from HTML.
textutil never writes field codes, so ``field_code.doc`` is patched afterwards: the
placeholder characters U+27E6/U+27E7/U+27E1 in its UTF-16 text are swapped, in place and
with the same byte length, for Word's field begin/separator/end marks 0x13/0x14/0x15.
Run: ``uv run python scripts/make_doc_fixtures.py``.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "doc"

FIXTURES = {
    "manual_ko.doc": """
<h1>거래처 관리 매뉴얼</h1>
<p>이 매뉴얼은 거래처 등록과 평가 절차를 정한다. 모든 거래처는 코드 S001 형식으로 관리한다.</p>
<h2>평가 기준</h2>
<p>거래처 평가는 매 분기 품질, 납기, 가격 세 항목으로 실시한다.</p>
<table border="1">
<tr><td>항목</td><td>가중치</td><td>담당</td></tr>
<tr><td>품질</td><td>50</td><td>품질팀</td></tr>
<tr><td>납기</td><td>30</td><td>구매팀</td></tr>
</table>
<h2>거래 중지</h2>
<p>평가 점수가 60점 미만인 거래처는 구매팀장 승인으로 거래를 중지한다.</p>
""",
    "almost_empty.doc": "<p>메모</p>",
    "field_code.doc": """
<p>자세한 기준은 ⟦ HYPERLINK "https://example.com/policy" ⟧사내 정책 포털⟡에서 확인한다.</p>
<p>문서 번호 ⟦ PAGE ⟧1⟡ 끝.</p>
""",
}


FIELD_MARKS = {"\u27e6": "\x13", "\u27e7": "\x14", "\u27e1": "\x15"}


def _patch_fields(path: Path) -> None:
    import olefile

    with olefile.OleFileIO(str(path), write_mode=True) as ole:
        data = ole.openstream("WordDocument").read()
        patched = data
        for mark, code in FIELD_MARKS.items():
            patched = patched.replace(mark.encode("utf-16-le"), code.encode("utf-16-le"))
        if patched == data:
            raise SystemExit(f"{path}: no field placeholders found (text not UTF-16?)")
        ole.write_stream("WordDocument", patched)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in FIXTURES.items():
            html = Path(tmp) / (Path(name).stem + ".html")
            html.write_text(
                f'<html><head><meta charset="utf-8"></head><body>{body}</body></html>',
                encoding="utf-8",
            )
            target = OUT / name
            subprocess.run(
                ["/usr/bin/textutil", "-convert", "doc", str(html), "-output", str(target)],
                check=True,
            )
            if name == "field_code.doc":
                _patch_fields(target)
            print(f"{target.relative_to(OUT.parent.parent.parent)}: {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
