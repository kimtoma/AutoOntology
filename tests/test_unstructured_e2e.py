"""Integration: the real kordoc engine (``<home>/kordoc``, from ``autoontology tools
install-kordoc``) on the HWP fixture and the generated PDF/DOCX, and the real
``autoontology extract hanbit-docs samples/unstructured`` against the local Ollama.

The extract test needs ``autoontology up`` (chat goes through the service's /v1). On a store
that already holds this source it is a no-op re-run, which also proves idempotence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoontology.config import REPO_ROOT, Settings

pytestmark = pytest.mark.integration

SAMPLES = REPO_ROOT / "samples" / "unstructured"
HWP = REPO_ROOT / "tests" / "fixtures" / "hwp" / "biz_plan.hwp"
SOURCE = "hanbit-docs"


@pytest.fixture
def engine(monkeypatch):
    from autoontology.unstructured import kordoc

    monkeypatch.delenv("KORDOC_CLI", raising=False)  # the real <home>/kordoc install
    engine = kordoc.resolve_engine(Settings.from_env().home)
    assert engine.origin == "home", "run `uv run autoontology tools install-kordoc` first"
    assert kordoc.engine_version(engine) == kordoc.DEFAULT_VERSION
    return engine


@pytest.mark.parametrize(
    "path, expected",
    [
        (HWP, ["사업목적", "보안관리"]),
        (SAMPLES / "계약관리규정.pdf", ["계약금액", "승인권자", "고객ID"]),
        (SAMPLES / "청구수납규정.docx", ["청구서", "수납상태", "독촉장"]),
    ],
)
def test_real_kordoc_reads_fixture(engine, path, expected):
    from autoontology.unstructured import chunker, kordoc, readers

    data = kordoc.parse_json(engine, path)
    assert data["success"] is True and data["markdown"].strip()
    doc = readers.read_document(path, engine=engine)
    text = "\n".join(s.text for s in doc.sections)
    assert doc.engine == "kordoc" and all(word in text for word in expected)
    chunks = chunker.chunk(doc)
    assert chunks and all(c.tokens <= chunker.DEFAULT_MAX_TOKENS for c in chunks)


def _service_up(settings: Settings) -> bool:
    from autoontology.engine.jobs import service_alive

    return service_alive(settings)


def test_extract_hanbit_docs_against_ollama(monkeypatch):
    from autoontology.engine import extract as extract_job
    from autoontology.store.db import Store
    from autoontology.store.documents import DocumentStore

    settings = Settings.from_env()
    if not _service_up(settings):
        pytest.skip("services are down (uv run autoontology up)")
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.chdir(REPO_ROOT)
    store = Store.for_home(settings.home)
    res = extract_job.run_extract(
        SOURCE, Path("samples/unstructured"), settings=settings, store=store
    )
    assert res.status == "succeeded", res
    src = store.get_source(SOURCE)
    docs = DocumentStore(store)
    concepts = docs.list_concepts(src.id)
    relations = docs.list_relations(src.id)
    assert len(concepts) >= 10, [c.name for c in concepts]
    assert len(relations) >= 5
    assert all(c.evidence_count >= 1 for c in concepts)  # non-empty quotes only
    per_doc = docs.concept_documents(src.id)
    hwp_path = str((SAMPLES / "사업수행계획서.hwp").resolve())
    assert any(hwp_path in paths for paths in per_doc.values()), "HWP contributed no concept"
    names = {c.name for c in concepts}
    assert {"고객", "계약"} <= names or {"고객", "청구서"} <= names, sorted(names)
    report = res.export_dir / "report.md"
    assert report.is_file() and "Document extraction report" in report.read_text(encoding="utf-8")
