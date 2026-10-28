# SPDX-License-Identifier: MIT
"""The UI tests classify files against a copy of ``/api/status.formats``: keep it current."""

import json
from pathlib import Path

from autoontology import formats

FIXTURE = Path(__file__).resolve().parents[1] / "ui" / "src" / "test" / "fixtures" / "formats.json"


def test_ui_formats_fixture_matches_the_api():
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == formats.describe(), (
        "regenerate ui/src/test/fixtures/formats.json from autoontology.formats.describe()"
    )
