# SPDX-License-Identifier: MIT
"""CLI message catalogue: language choice, key parity, fallback and Korean output."""

import os
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autoontology import doctor, i18n, onboarding
from autoontology.cli import app
from autoontology.doctor import Check
from autoontology.i18n_catalog import en, ko
from autoontology.onboarding import ProviderInfo

runner = CliRunner()
HANGUL = re.compile(r"[가-힣]")


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


def test_catalogues_have_the_same_keys_and_placeholders():
    assert set(en.MESSAGES) == set(ko.MESSAGES)
    for key, text in en.MESSAGES.items():
        assert text.strip() and ko.MESSAGES[key].strip(), key
        assert _fields(text) == _fields(ko.MESSAGES[key]), key


def test_korean_texts_are_translated():
    # identical to English only for words that are names in every language
    same = {k for k in en.MESSAGES if en.MESSAGES[k] == ko.MESSAGES[k]}
    assert same <= {
        "init.step.postgres",
        "init.sum.documents_kordoc",
        "init.env_set",
        "init.warn",
        "demo.bad_set",
    }
    for key in en.MESSAGES:
        if key.startswith(("help.", "init.pg", "init.login", "remedy.node")):
            assert HANGUL.search(ko.MESSAGES[key]), key


def test_backticked_commands_survive_translation():
    for key, text in en.MESSAGES.items():
        for command in re.findall(r"`[^`]+`", text):
            assert command in ko.MESSAGES[key], (key, command)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "en"),
        ({"LANG": "ko_KR.UTF-8"}, "ko"),
        ({"LANG": "en_US.UTF-8", "LC_ALL": "ko_KR.UTF-8"}, "ko"),
        ({"LC_MESSAGES": "C", "LANG": "ko_KR.UTF-8"}, "en"),
        ({"LANG": "ko_KR.UTF-8", "AUTOONTOLOGY_LANG": "en"}, "en"),
        ({"AUTOONTOLOGY_LANG": "KO"}, "ko"),
        ({"AUTOONTOLOGY_LANG": " "}, "en"),
    ],
)
def test_current_lang_order(monkeypatch, env, expected):
    for name in ("AUTOONTOLOGY_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert i18n.current_lang() == expected


def test_lang_from_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("AUTOONTOLOGY_LANG=ko\n", encoding="utf-8")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.delenv("AUTOONTOLOGY_LANG", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.current_lang() == "ko"


def test_msg_formats_and_falls_back_to_english(monkeypatch):
    assert i18n.msg("init.chat_ok", "ko", provider="claude", model="m") == "  ok    채팅: claude m"
    monkeypatch.delitem(ko.MESSAGES, "init.chat_ok")
    assert i18n.msg("init.chat_ok", "ko", provider="claude", model="m") == "  ok    chat: claude m"
    assert i18n.msg("no.such.key", "ko") == "no.such.key"


def test_display_width_pads_hangul_as_double_width():
    assert i18n.width("상태") == 4 and i18n.width("OK") == 2
    assert i18n.ljust("점검", 6) == "점검  "


def test_remedy_translation_keeps_commands():
    assert i18n.remedy("uv run autoontology setup", "ko") == "uv run autoontology setup"
    assert i18n.remedy("start Postgres (`docker compose up -d`); see the postgres row", "ko") == (
        "Postgres 시작 (`docker compose up -d`); postgres 행 참고"
    )
    assert i18n.remedy("start Ollama (`ollama serve`)", "en") == "start Ollama (`ollama serve`)"


def test_every_doctor_remedy_has_a_korean_text_or_is_a_command(isolated_env, monkeypatch):
    """Rows from a broken machine (as in test_doctor's remedy test): each remedy part is
    either translated or a bare command."""
    import httpx

    from autoontology.config import Settings

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    monkeypatch.setattr(
        doctor,
        "check_postgres",
        lambda s: [Check("postgres", "FAIL", "x", "start Postgres, e.g. `docker compose up -d`")],
    )
    monkeypatch.setattr(doctor, "check_ports", lambda s: [])
    checks = doctor.run_checks(Settings.from_env(), skip_models=True)
    for c in checks:
        for part in i18n.remedy(c.remedy, "ko").split("; ") if c.remedy else []:
            assert HANGUL.search(part) or part.startswith(("uv run ", "`")), (c.name, part)


def test_doctor_prints_korean_headers(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_LANG", "ko")
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda settings, skip_models=False: [
            Check(
                ".env",
                "WARN",
                "missing",
                "uv run autoontology init (writes .env; or cp .env.example .env)",
            ),
            Check("ollama", "FAIL", "down", "start Ollama (`ollama serve`)"),
        ],
    )
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 1
    lines = res.output.splitlines()
    assert lines[0].split() == ["점검", "상태", "상세", "조치"]
    assert "Ollama 시작 (`ollama serve`)" in res.output and "FAIL" in res.output
    # the STATUS column lines up although the header is double-width
    col = i18n.width(lines[0][: lines[0].index("상태")])
    assert [line[col : col + 4] for line in lines[1:]] == ["WARN", "FAIL"]


def test_doctor_stays_english_by_default(isolated_env, monkeypatch):
    monkeypatch.setattr(doctor, "run_checks", lambda s, skip_models=False: [Check("a", "OK")])
    res = runner.invoke(app, ["doctor"])
    assert res.output.split()[:4] == ["CHECK", "STATUS", "DETAIL", "REMEDY"]


def test_init_yes_prints_korean(isolated_env, monkeypatch):
    from autoontology import cli
    from autoontology.engine import pg
    from tests.test_cli_onboarding import _det

    monkeypatch.setenv("AUTOONTOLOGY_LANG", "ko")
    monkeypatch.setattr(pg, "setup_database", lambda s: "database autoontology exists")
    monkeypatch.setattr(
        onboarding, "detect_environment", lambda s, env_path=None: _det(env_path, claude=True)
    )
    monkeypatch.setattr(cli, "_init_setup", lambda docs: None)
    env = Path(isolated_env) / "conf" / ".env"
    res = runner.invoke(app, ["init", "--yes", "--env", str(env)])
    assert res.exit_code == 0, res.output
    assert res.output.splitlines()[0] == "English: AUTOONTOLOGY_LANG=en"
    for text in ("[2/7] 채팅 제공자", "제공자", "로그인", "[7/7] 요약", "항목", "값"):
        assert text in res.output, text
    assert "다음: uv run autoontology demo --set hanbit" in res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in env.read_text(encoding="utf-8")


def test_init_english_hint_points_to_korean(isolated_env, monkeypatch):
    from autoontology import cli
    from autoontology.engine import pg
    from tests.test_cli_onboarding import _det

    monkeypatch.setattr(pg, "setup_database", lambda s: "ok")
    monkeypatch.setattr(onboarding, "detect_environment", lambda s, env_path=None: _det(env_path))
    monkeypatch.setattr(cli, "_init_setup", lambda docs: None)
    res = runner.invoke(app, ["init", "--yes", "--env", str(Path(isolated_env) / ".env")])
    assert res.exit_code == 0, res.output
    assert res.output.splitlines()[0] == "한국어: AUTOONTOLOGY_LANG=ko"
    assert "[2/7] Chat provider" in res.output


def test_provider_status_in_korean(isolated_env, monkeypatch):
    from autoontology.web.providers import probe

    monkeypatch.setenv("AUTOONTOLOGY_LANG", "ko")
    monkeypatch.setattr(
        onboarding,
        "provider_info",
        lambda s, n: ProviderInfo(
            n, n != "codex", n == "ollama", "", "" if n == "ollama" else f"fix {n}"
        ),
    )
    monkeypatch.setattr(
        probe,
        "last_test",
        lambda s, n: (
            {"ok": True, "seconds": 2.9, "at": "2026-10-29T01:02:03"} if n == "claude" else None
        ),
    )
    res = runner.invoke(app, ["provider", "status"])
    assert res.exit_code == 0, res.output
    header = res.output.splitlines()[0]
    assert header.split() == ["제공자", "사용", "가능", "로그인", "모델", "최근", "테스트", "조치"]
    assert "성공 2.9초 (2026-10-29T01:02)" in res.output and "fix codex" in res.output
    assert "* = 현재 채팅 제공자 (ollama" in res.output
    assert "* ollama  예" in res.output  # yes/no in Korean, columns padded


def test_source_rm_notices_in_korean():
    assert i18n.notice("removed source quick", "ko") == "소스 quick 제거됨"
    assert i18n.notice("dropped database autoontology_src_quick", "ko") == (
        "데이터베이스 autoontology_src_quick 삭제됨"
    )
    assert i18n.notice("something else", "ko") == "something else"
    assert i18n.notice("removed source quick", "en") == "removed source quick"


def test_help_texts_follow_the_language(tmp_path):
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("AUTOONTOLOGY_")},
        "AUTOONTOLOGY_LANG": "ko",
        "AUTOONTOLOGY_ENV_FILE": str(tmp_path / "absent.env"),
        "COLUMNS": "200",
    }
    out = subprocess.run(
        [sys.executable, "-m", "autoontology", "init", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "묻지 않고 감지된 기본값으로 진행" in out and "--provider" in out


#: The ``--help`` screens with Korean help texts, and the catalogue keys each one shows.
HELP_SCREENS = {
    ("init",): (
        "help.init.provider",
        "help.init.chat_model",
        "help.init.docs",
        "help.init.vision_model",
        "help.init.yes",
        "help.init.env",
    ),
    ("demo",): ("help.demo.set", "help.demo.no_open", "help.demo.resume", "help.demo.replace"),
    ("provider",): ("help.provider_app",),
    ("provider", "test"): ("help.provider.test.provider",),
    ("provider", "use"): ("help.provider.use.chat_model",),
    ("doctor",): ("help.doctor", "help.doctor.skip_models"),
}


@pytest.mark.parametrize("args", list(HELP_SCREENS), ids=" ".join)
def test_korean_help_is_intact_at_80_columns(tmp_path, args):
    """Rich cuts wide-character lines it cannot wrap ("비 …"): every Korean help text must
    survive an 80-column terminal whole."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("AUTOONTOLOGY_")},
        "AUTOONTOLOGY_LANG": "ko",
        "AUTOONTOLOGY_ENV_FILE": str(tmp_path / "absent.env"),
        "COLUMNS": "80",
    }
    out = subprocess.run(
        [sys.executable, "-m", "autoontology", *args, "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "…" not in out, out
    flat = re.sub(r"[\s│╭╮╰╯─]", "", out)
    for key in HELP_SCREENS[args]:
        assert re.sub(r"\s", "", ko.MESSAGES[key]) in flat, (key, out)


def test_provider_status_remedies_and_times_in_korean():
    assert i18n.remedy("set AUTOONTOLOGY_OPENAI_BASE_URL in .env", "ko") == (
        ".env에 AUTOONTOLOGY_OPENAI_BASE_URL을 설정하세요"
    )
    text = i18n.remedy(
        "install the claude CLI and log in "
        "(run `claude auth login` (or `claude` then /login) as this user)",
        "ko",
    )
    assert text.startswith("claude CLI를 설치하고 로그인하세요") and "`claude auth login`" in text
    assert i18n.msg("provider.seconds", "ko", seconds=3.07) == "3.07초"
