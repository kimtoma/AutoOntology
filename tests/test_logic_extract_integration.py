# SPDX-License-Identifier: MIT
"""The ``logic`` run against the configured chat model (real LLM calls, a few cents with a
paid API). The quickstart 청구수납규정 concepts and object types are seeded as in
``test_logic_extract.py``; the sources stay faked. Run with
``AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_logic_extract_integration.py``."""

from __future__ import annotations

import pytest

from autoontology.config import Settings
from autoontology.logic import extract
from autoontology.store.db import Store
from tests import logic_fixtures as fx
from tests.test_logic_extract import seed_documents

pytestmark = pytest.mark.integration


def test_real_model_proposes_grounded_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(tmp_path / "home"))
    fx.install_backend(monkeypatch)
    settings = Settings.from_env()
    view = fx.seed(Store.for_home(settings.home))
    seed_documents(view)
    result = extract.run_logic(view, settings)
    assert result.status == "succeeded", result.error
    stored = view.logic_candidates().list()
    assert stored, result.counts
    for c in stored:
        assert c.kind in ("function", "action", "workflow") and c.name
        assert isinstance(c.errors, list)
        assert all(e["verified"] for e in c.evidence)
    # at least one candidate is grounded in a verified quote of the document
    assert any(c.evidence_verified for c in stored)
