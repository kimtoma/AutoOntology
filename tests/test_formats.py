# SPDX-License-Identifier: MIT
from autoontology import formats
from autoontology.structured import loaders
from autoontology.unstructured import kordoc, readers


def test_readers_and_loaders_use_the_formats_module():
    assert loaders.SUPPORTED_SUFFIXES == formats.STRUCTURED_SUFFIXES
    assert ".xls" in loaders.SUPPORTED_SUFFIXES
    assert readers.NATIVE_EXTENSIONS == formats.DOCUMENT_NATIVE_SUFFIXES
    assert readers.SUPPORTED_EXTENSIONS == formats.DOCUMENT_SUFFIXES
    assert kordoc.EXTENSIONS == formats.DOCUMENT_KORDOC_SUFFIXES | formats.IMAGE_SUFFIXES
    assert {".csv", ".tsv", ".doc"} <= readers.NATIVE_EXTENSIONS
    assert not readers.NATIVE_EXTENSIONS & kordoc.EXTENSIONS
    for ext in readers.SUPPORTED_EXTENSIONS:
        assert ext in readers.MIME_TYPES, ext


def test_describe():
    d = formats.describe()
    assert d["structured"][".csv"] == {
        "kind": "structured",
        "engine": "native",
        "note": "one table per file; first row is the header",
    }
    assert d["documents"][".csv"]["kind"] == "document"
    assert d["documents"][".csv"]["engine"] == "native"
    assert d["documents"][".doc"]["engine"] == "native"
    assert d["documents"][".hwp"]["engine"] == "kordoc"
    assert d["documents"][".png"]["engine"] == "kordoc-ocr"
    assert set(d["structured"]) == formats.STRUCTURED_SUFFIXES
    assert set(d["documents"]) == formats.DOCUMENT_SUFFIXES
    for group in d.values():
        for entry in group.values():
            assert set(entry) == {"kind", "engine", "note"} and entry["note"]
