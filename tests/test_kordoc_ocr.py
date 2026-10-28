# SPDX-License-Identifier: MIT
import json
import shutil
import subprocess

import pytest

from autoontology.unstructured import kordoc, readers

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

#: check-ocr-models: ready when $FAKE_OCR_DIR/ready exists; the download (no
#: --status-only) must run without KORDOC_OFFLINE and creates it unless FAKE_OCR_FAIL.
#: chunks: echoes its argv as the text of one chunk.
FAKE_CLI = r"""
const fs = require('fs'), path = require('path');
const args = process.argv.slice(2);
const dir = process.env.FAKE_OCR_DIR;
const marker = path.join(dir, 'ready');
if (args[0] === 'check-ocr-models') {
  fs.appendFileSync(path.join(dir, 'calls'), JSON.stringify({args, offline: process.env.KORDOC_OFFLINE || ''}) + '\n');
  if (!args.includes('--status-only')) {
    if (process.env.KORDOC_OFFLINE === '1') { console.error('offline'); process.exit(3); }
    if (process.env.FAKE_OCR_FAIL) { console.error('network down'); process.exit(1); }
    fs.writeFileSync(marker, 'ok');
    console.log('downloaded');
    process.exit(0);
  }
  const ready = fs.existsSync(marker);
  console.log(JSON.stringify({modelsDir: '/home/someone/.cache/kordoc/models/ppocr', allReady: ready, models: [
    {name: 'PP-OCRv5 mobile det', filename: 'det.onnx', exists: ready, verified: ready},
    {name: 'PP-OCRv5 korean rec', filename: 'rec_korean.onnx', exists: ready, verified: ready},
  ]}));
  process.exit(0);
}
if (args[0] === '--version') { console.log('9.9.9'); process.exit(0); }
console.log(JSON.stringify([{id: 'c1', type: 'text', breadcrumb: [], text: args.slice(0, -2).join(' '), page: 1}]));
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    cli = tmp_path / "fake" / "cli.js"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLI, encoding="utf-8")
    state = tmp_path / "ocr"
    state.mkdir()
    monkeypatch.setenv("KORDOC_CLI", str(cli))
    monkeypatch.setenv("FAKE_OCR_DIR", str(state))
    monkeypatch.delenv("KORDOC_NODE", raising=False)
    return {"home": tmp_path / "home", "state": state, "engine": kordoc.resolve_engine(tmp_path)}


def _calls(state):
    path = state / "calls"
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


def test_ocr_flag_only_for_pdf(fake, tmp_path):
    engine = fake["engine"]
    for name, expected in [("a.pdf", True), ("b.docx", False), ("c.png", False), ("d.hwp", False)]:
        (chunk,) = kordoc.parse_chunks(engine, tmp_path / name)
        assert ("--ocr" in chunk.text.split()) is expected, name
        assert chunk.text.startswith("--format chunks --table-format gfm --silent")


def test_status_not_ready_then_download(fake):
    home, state = fake["home"], fake["state"]
    status = kordoc.ocr_models_status(home)
    assert status == {
        "ready": False,
        "known": True,
        "detail": "OCR models missing or unverified: det.onnx, rec_korean.onnx",
    }
    assert "/home/someone" not in json.dumps(status)
    lines: list[str] = []
    ready = kordoc.download_ocr_models(home, echo=lines.append)
    assert ready == {
        "ready": True,
        "known": True,
        "detail": "2/2 OCR models verified (PP-OCRv5 korean)",
    }
    calls = _calls(state)
    assert [c["args"] for c in calls] == [
        ["check-ocr-models", "--status-only"],
        ["check-ocr-models", "--status-only"],
        ["check-ocr-models"],
        ["check-ocr-models", "--status-only"],
    ]
    # the status checks stay offline; only the download itself may use the network
    assert [c["offline"] for c in calls] == ["1", "1", "", "1"]
    assert "~18 MB" in lines[0]
    # already ready: no download call
    kordoc.download_ocr_models(home, echo=lines.append)
    assert [c["args"] for c in _calls(state)][-1] == ["check-ocr-models", "--status-only"]
    assert len(_calls(state)) == 5


def test_download_failure(fake, monkeypatch):
    monkeypatch.setenv("FAKE_OCR_FAIL", "1")
    monkeypatch.setenv("KORDOC_OFFLINE", "1")  # set by the user: still lifted for the download
    with pytest.raises(kordoc.KordocError) as err:
        kordoc.download_ocr_models(fake["home"], echo=lambda _: None)
    assert err.value.code == "OCR_MODELS_MISSING" and "network" in err.value.message


def test_status_without_engine(tmp_path, monkeypatch):
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    with pytest.raises(kordoc.KordocUnavailable):
        kordoc.ocr_models_status(tmp_path)


def test_install_kordoc_downloads_models_unless_skipped(fake, monkeypatch):
    home = fake["home"]
    cli = kordoc.home_cli(home)
    cli.parent.mkdir(parents=True)
    cli.write_text("//", encoding="utf-8")
    (home / "kordoc" / "kordoc.version").write_text(f"{kordoc.DEFAULT_VERSION}\n")
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw))
        return subprocess.run(argv, **kw)

    skipped = kordoc.install_kordoc(home, runner=runner, echo=lambda _: None, ocr_models=False)
    assert skipped.action == "already installed" and skipped.ocr is None and calls == []
    result = kordoc.install_kordoc(home, runner=runner, echo=lambda _: None)
    assert result.ocr == {
        "ready": True,
        "known": True,
        "detail": "2/2 OCR models verified (PP-OCRv5 korean)",
    }
    ((argv, kw),) = calls
    assert argv[-1] == "check-ocr-models" and "KORDOC_OFFLINE" not in kw["env"]
    assert kw["stdin"] is subprocess.DEVNULL  # never waits on the terminal
    omitted = kordoc.install_kordoc(home, runner=runner, echo=lambda _: None, omit_optional=True)
    assert omitted.ocr is None


def test_read_document_with_ocr(fake, tmp_path):
    doc = readers.read_document(tmp_path / "scan.pdf", engine=fake["engine"])
    assert "--ocr" in doc.sections[0].text


def test_cli_install_kordoc_downloads_or_skips_the_models(fake, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli

    home = fake["home"]
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(home))
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(home / "none.env"))
    monkeypatch.delenv("KORDOC_CLI")  # the CLI checks the installed (home) engine
    cli_js = kordoc.home_cli(home)
    cli_js.parent.mkdir(parents=True)
    cli_js.write_text(FAKE_CLI, encoding="utf-8")
    (home / "kordoc" / "kordoc.version").write_text(f"{kordoc.DEFAULT_VERSION}\n")
    monkeypatch.setattr(kordoc, "engine_version", lambda _e: kordoc.DEFAULT_VERSION)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["tools", "install-kordoc", "--skip-ocr-models"])
    assert res.exit_code == 0, res.output
    assert "ocr models: skipped" in res.output and not (fake["state"] / "ready").exists()
    res = runner.invoke(cli.app, ["tools", "install-kordoc"])
    assert res.exit_code == 0, res.output
    assert "ocr models: 2/2 OCR models verified" in res.output
    assert (fake["state"] / "ready").exists()
    (fake["state"] / "ready").unlink()
    monkeypatch.setenv("FAKE_OCR_FAIL", "1")
    res = runner.invoke(cli.app, ["tools", "install-kordoc"])
    assert res.exit_code != 0 and "--skip-ocr-models" in res.output


def test_doctor_ocr_models_row(fake, monkeypatch, tmp_path):
    from autoontology import doctor
    from autoontology.config import Settings

    settings = Settings(home=fake["home"])
    check = doctor.check_ocr_models(settings)
    assert check.status == "WARN"
    assert "images and image-only PDFs are skipped" in check.detail
    assert "scanned pages of other PDFs yield no text" in check.detail
    assert check.remedy == "run `uv run autoontology tools install-kordoc`"
    (fake["state"] / "ready").write_text("ok")
    assert doctor.check_ocr_models(settings).status == "OK"
    monkeypatch.delenv("KORDOC_CLI")
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    assert doctor.check_ocr_models(settings).status == "SKIP"


def test_status_timeout_is_unknown_not_missing(fake, monkeypatch):
    def timeout(*_a, **_kw):
        raise kordoc.KordocError("TIMEOUT", "check-ocr-models took longer than 30s")

    monkeypatch.setattr(kordoc, "run_cli", timeout)
    status = kordoc.ocr_models_status(fake["home"], engine=fake["engine"])
    assert status["ready"] is False and status["known"] is False
