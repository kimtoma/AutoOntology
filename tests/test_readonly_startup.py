# SPDX-License-Identifier: MIT
"""Metadata and native command discovery must not initialize ingestion dependencies."""

import os
import subprocess
import sys
from pathlib import Path


def test_command_discovery_and_metadata_avoid_ingestion_runtime():
    code = """
import sys
from autoontology import cli
from autoontology.structured.semantic import StructuredSnapshot, qualified
from autoontology.structured.pipeline import FilesLocator
assert qualified({"schema":"public","table":"invoices","column":"amount"}) == "public.invoices.amount"
assert StructuredSnapshot("source", "id", "catalog").terms == []
assert FilesLocator("/unused").path == "/unused"
assert not {"duckdb", "httpx", "autoontology.unstructured.schemas"}.intersection(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).absolute().parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).absolute().parents[1])},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
