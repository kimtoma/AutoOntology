# SPDX-License-Identifier: MIT
import shutil
import subprocess
from pathlib import Path

import pytest

from autoontology.unstructured import kordoc

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

FAKE_CLI = r"""
const args = process.argv.slice(2);
if (args[0] === '--version') { console.log('9.9.9'); process.exit(0); }
const file = require('path').basename(args[args.length - 1] || '');
const fmt = args[args.indexOf('--format') + 1];
process.stderr.write(`[kordoc] ${file} ... OK\n`);
if (process.env.KORDOC_OFFLINE !== '1') { console.log('{"success":false,"code":"ONLINE"}'); process.exit(1); }
if (file.includes('fail')) {
  console.log(JSON.stringify({success: false, fileType: 'unknown', error: 'DRM 문서', code: 'ENCRYPTED', file}));
  process.exit(1);
}
if (file.includes('garbage')) { console.log('not json'); process.exit(2); }
if (file.includes('slow')) { setTimeout(() => {}, 10000); return; }
if (fmt === 'chunks') {
  console.log(JSON.stringify([
    {id: 'c0001', type: 'heading', breadcrumb: [], text: '# 제1장 총칙', blockRange: [0, 0], page: 1},
    {id: 'c0002', type: 'text', breadcrumb: ['제1장 총칙'], text: '계약은 승인을 받는다.', blockRange: [1, 1], page: 2},
    {id: 'c0003', type: 'table', breadcrumb: ['제1장 총칙'], text: '| a | b |\n| --- | --- |', page: 2},
    {id: 'c0004', type: 'text', breadcrumb: 'bad', text: 42},
    {args: args},
  ]));
} else {
  console.log(JSON.stringify({success: true, fileType: 'hwp', markdown: '# 제1장', blocks: [],
    metadata: {pageCount: 2}, pageCount: 2, args}));
}
"""


@pytest.fixture
def engine(tmp_path, monkeypatch):
    cli = tmp_path / "fake" / "cli.js"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLI, encoding="utf-8")
    monkeypatch.setenv("KORDOC_CLI", str(cli))
    monkeypatch.delenv("KORDOC_NODE", raising=False)
    monkeypatch.delenv("KORDOC_OFFLINE", raising=False)
    return kordoc.resolve_engine(tmp_path / "home")


def test_resolution_order(tmp_path, monkeypatch, engine):
    assert engine.origin == "KORDOC_CLI" and engine.cli.name == "cli.js"
    monkeypatch.delenv("KORDOC_CLI")
    home = tmp_path / "home"
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    with pytest.raises(kordoc.KordocUnavailable, match="install-kordoc"):
        kordoc.resolve_cli(home) or kordoc.resolve_engine(home)
    cli = kordoc.home_cli(home)
    cli.parent.mkdir(parents=True)
    cli.write_text("//", encoding="utf-8")
    assert kordoc.resolve_cli(home) == (cli, "home")
    # npm global shim on PATH -> the real dist/cli.js behind it
    shutil.rmtree(home)
    prefix = tmp_path / "npmg"
    real = prefix / "lib" / "node_modules" / "kordoc" / "dist" / "cli.js"
    real.parent.mkdir(parents=True)
    real.write_text("//", encoding="utf-8")
    real.chmod(0o755)
    (prefix / "bin").mkdir()
    (prefix / "bin" / "kordoc").symlink_to(real)
    monkeypatch.setenv("PATH", str(prefix / "bin"))
    assert kordoc.resolve_cli(home) == (real.resolve(), "PATH")
    monkeypatch.setenv("KORDOC_CLI", str(tmp_path / "missing.js"))
    with pytest.raises(kordoc.KordocUnavailable, match="does not exist"):
        kordoc.resolve_cli(home)


def test_node_too_old(tmp_path, monkeypatch):
    old = tmp_path / "node"
    old.write_text("#!/bin/sh\necho v18.19.0\n")
    old.chmod(0o755)
    monkeypatch.setenv("KORDOC_NODE", str(old))
    with pytest.raises(kordoc.KordocUnavailable) as err:
        kordoc.require_node()
    assert err.value.code == "NODE_TOO_OLD" and "18.19.0" in err.value.message


def test_parse_chunks_offline_and_skips_malformed(engine, tmp_path, caplog):
    caplog.set_level("INFO", "autoontology.kordoc")
    chunks = kordoc.parse_chunks(engine, tmp_path / "규정.hwp")
    assert [c.id for c in chunks] == ["c0001", "c0002", "c0003"]
    assert chunks[1].breadcrumb == ("제1장 총칙",) and chunks[1].page == 2
    assert chunks[2].type == "table" and chunks[0].type == "heading"
    assert "규정.hwp ... OK" in caplog.text  # stderr goes to the log


def test_parse_json(engine, tmp_path):
    data = kordoc.parse_json(engine, tmp_path / "a.pdf")
    assert data["success"] is True and data["pageCount"] == 2
    assert data["args"] == [
        "--format",
        "json",
        "--no-images",
        "--table-format",
        "gfm",
        "--silent",
        "--",
        str((tmp_path / "a.pdf").resolve()),
    ]
    assert kordoc.engine_version(engine) == "9.9.9"


def test_failure_envelope_and_garbage(engine, tmp_path):
    with pytest.raises(kordoc.KordocError) as err:
        kordoc.parse_chunks(engine, tmp_path / "fail.hwp")
    assert err.value.code == "ENCRYPTED" and "DRM 문서" in err.value.message
    with pytest.raises(kordoc.KordocError) as err:
        kordoc.parse_json(engine, tmp_path / "garbage.pdf")
    assert err.value.code == "EXIT_2"
    with pytest.raises(kordoc.KordocError) as err:
        kordoc.parse_chunks(engine, tmp_path / "slow.pdf", timeout=0.5)
    assert err.value.code == "TIMEOUT"


class _Runner:
    def __init__(self, home, returncode=0):
        self.home, self.returncode, self.calls = home, returncode, []

    def __call__(self, argv, check=False):
        self.calls.append(argv)
        if self.returncode == 0:
            cli = kordoc.home_cli(self.home)
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_text("//", encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.returncode)


def test_install_kordoc_argv_and_idempotence(tmp_path):
    home = tmp_path / "home"
    runner = _Runner(home)
    result = kordoc.install_kordoc(home, npm="/x/npm", runner=runner, echo=lambda _: None)
    assert result.action == "installed" and result.version == kordoc.DEFAULT_VERSION == "4.24.13"
    assert runner.calls == [
        [
            "/x/npm",
            "install",
            "--prefix",
            str(home / "kordoc"),
            "--no-audit",
            "--no-fund",
            "kordoc@4.24.13",
        ]
    ]
    assert (home / "kordoc" / "kordoc.version").read_text() == "4.24.13\n"
    again = kordoc.install_kordoc(home, npm="/x/npm", runner=runner, echo=lambda _: None)
    assert again.action == "already installed" and len(runner.calls) == 1
    kordoc.install_kordoc(
        home,
        version="4.24.12",
        omit_optional=True,
        npm="/x/npm",
        runner=runner,
        echo=lambda _: None,
    )
    assert runner.calls[-1][-2:] == ["kordoc@4.24.12", "--omit=optional"]
    assert kordoc.read_version_file(home) == {"version": "4.24.12", "omit_optional": True}
    kordoc.install_kordoc(
        home,
        version="4.24.12",
        omit_optional=True,
        force=True,
        npm="/x/npm",
        runner=runner,
        echo=lambda _: None,
    )
    assert len(runner.calls) == 3


def test_install_kordoc_failures(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(kordoc.KordocError, match="not a kordoc version"):
        kordoc.install_kordoc(
            home,
            version="latest; rm -rf /",
            npm="/x/npm",
            runner=_Runner(home),
            echo=lambda _: None,
        )
    with pytest.raises(kordoc.KordocError, match="npm install failed"):
        kordoc.install_kordoc(home, npm="/x/npm", runner=_Runner(home, 1), echo=lambda _: None)
    assert not (Path(home) / "kordoc" / "kordoc.version").exists()


def test_dash_filenames_are_passed_after_double_dash(engine, tmp_path):
    chunks = kordoc.parse_chunks(engine, tmp_path / "-rf.hwp")
    assert [c.id for c in chunks] == ["c0001", "c0002", "c0003"]
