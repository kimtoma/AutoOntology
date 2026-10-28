# SPDX-License-Identifier: MIT
"""Integration: the real kordoc OCR (PP-OCRv5 korean, ``autoontology tools install-kordoc``)
on ``tests/fixtures/images/scan_page1.png`` -- page 1 of the HWP fixture rendered to PNG
with ``kordoc render biz_plan.hwp --format png --pages 1`` -- must recover the Korean
words the HWP itself holds."""

from __future__ import annotations

import re

import pytest

from autoontology.config import REPO_ROOT, Settings

pytestmark = pytest.mark.integration

FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCAN = FIXTURES / "images" / "scan_page1.png"
HWP = FIXTURES / "hwp" / "biz_plan.hwp"
HANGUL_WORD = re.compile(r"[가-힣]{2,}")


def test_real_ocr_reads_the_scanned_page(monkeypatch):
    from autoontology.unstructured import kordoc, readers

    monkeypatch.delenv("KORDOC_CLI", raising=False)
    home = Settings.from_env().home
    engine = kordoc.resolve_engine(home)
    status = kordoc.ocr_models_status(home, engine=engine)
    assert status["ready"], f"{status['detail']}: run `{kordoc.INSTALL_COMMAND}` first"
    doc = readers.read_document(SCAN, engine=engine)
    assert doc.engine == "kordoc" and doc.mime == "image/png"
    ocr_words = set(HANGUL_WORD.findall("\n".join(s.text for s in doc.sections)))
    hwp = readers.read_document(HWP, engine=engine)
    hwp_words = set(HANGUL_WORD.findall("\n".join(s.text for s in hwp.sections)))
    assert len(ocr_words & hwp_words) >= 3, (sorted(ocr_words), sorted(hwp_words)[:50])
