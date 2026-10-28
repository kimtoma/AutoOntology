# SPDX-License-Identifier: MIT
"""onboarding: detection, provider recommendation, the .env editor and the sample sets."""

import os
import stat
from pathlib import Path

import pytest

from autoontology import onboarding
from autoontology.config import Settings
from autoontology.onboarding import (
    INIT_BLOCK,
    Detection,
    EnvFile,
    ProviderInfo,
    describe_change,
    recommend_provider,
)
from autoontology.store.db import Store


def detection(**providers) -> Detection:
    infos = {
        name: ProviderInfo(name, *providers.get(name, (False, False)))
        for name in ("ollama", "openai", "claude", "codex")
    }
    return Detection(
        postgres_ok=True,
        postgres_detail="",
        docker=True,
        providers=infos,
        ollama_models=True,
        node_ok=True,
        node_detail="",
        kordoc=False,
        ocr_ready=False,
        env_exists=False,
        env_path=Path("/nonexistent/.env"),
    )


# --- recommendation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("providers", "expected"),
    [
        ({"claude": (True, True), "codex": (True, True), "ollama": (True, True)}, "claude"),
        ({"claude": (True, False), "codex": (True, True), "ollama": (True, True)}, "codex"),
        ({"claude": (True, False), "codex": (False, False), "ollama": (True, True)}, "ollama"),
        ({}, "ollama"),
    ],
)
def test_recommend_provider_order(providers, expected):
    assert recommend_provider(detection(**providers)) == expected


def test_detect_environment_uses_the_checks(isolated_env, monkeypatch):
    from autoontology import doctor
    from autoontology.doctor import Check

    monkeypatch.setattr(doctor, "check_postgres", lambda s: [Check("postgres", "FAIL", "down")])
    monkeypatch.setattr(doctor, "check_node", lambda: Check("node", "OK", "v22"))
    monkeypatch.setattr(doctor, "check_kordoc", lambda s: Check("kordoc", "WARN", "missing"))
    monkeypatch.setattr(doctor, "check_ocr_models", lambda s: Check("ocr models", "SKIP", "-"))

    def info(settings, name):
        return ProviderInfo(name, name in ("claude", "ollama"), name == "claude", "n", "")

    monkeypatch.setattr(onboarding, "provider_info", info)
    det = onboarding.detect_environment(Settings.from_env(), Path(isolated_env) / "x.env")
    assert not det.postgres_ok and det.postgres_detail == "down"
    assert det.node_ok and not det.kordoc and not det.ocr_ready and not det.env_exists
    assert det.ollama_reachable and det.providers["claude"].ready
    assert recommend_provider(det) == "claude"


def test_provider_info_never_raises(isolated_env, monkeypatch):
    from autoontology.web.providers import router

    def boom(self, *a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(router.Registry, "status", boom)
    info = onboarding.provider_info(Settings.from_env(), "claude")
    assert not info.ready and "claude auth login" in info.remedy


def test_provider_info_for_a_missing_cli(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_BIN", str(Path(isolated_env) / "no-claude"))
    monkeypatch.setenv("PATH", str(isolated_env))
    info = onboarding.provider_info(Settings.from_env(), "claude")
    assert not info.found and not info.logged_in and info.remedy


# --- EnvFile ----------------------------------------------------------------------------

TEMPLATE = """# AutoOntology configuration.
# --- Model providers ---
# Chat provider: ollama | openai | claude | codex.
AUTOONTOLOGY_CHAT_PROVIDER=ollama

# Claude model.
# AUTOONTOLOGY_CLAUDE_MODEL=claude-sonnet-5-5
POSTGRES_USER=me
"""


def test_set_replaces_active_and_commented_lines_in_place():
    env = EnvFile(TEMPLATE, existed=True)
    env.set("AUTOONTOLOGY_CHAT_PROVIDER", "claude")
    env.set("AUTOONTOLOGY_CLAUDE_MODEL", "claude-opus-5-5")
    out = env.render()
    assert out == TEMPLATE.replace(
        "AUTOONTOLOGY_CHAT_PROVIDER=ollama", "AUTOONTOLOGY_CHAT_PROVIDER=claude"
    ).replace(
        "# AUTOONTOLOGY_CLAUDE_MODEL=claude-sonnet-5-5", "AUTOONTOLOGY_CLAUDE_MODEL=claude-opus-5-5"
    )
    assert INIT_BLOCK not in out
    assert env.changes() == [
        ("AUTOONTOLOGY_CHAT_PROVIDER", "ollama", "claude"),
        ("AUTOONTOLOGY_CLAUDE_MODEL", None, "claude-opus-5-5"),
    ]


def test_set_appends_new_keys_under_one_block():
    env = EnvFile("A=1", existed=True)  # no trailing newline
    env.set("AUTOONTOLOGY_VISION_MODEL", "gemma4:e4b")
    env.set("AUTOONTOLOGY_EMBED_PROVIDER", "ollama")
    assert env.render() == (
        f"A=1\n\n{INIT_BLOCK}\nAUTOONTOLOGY_VISION_MODEL=gemma4:e4b\n"
        "AUTOONTOLOGY_EMBED_PROVIDER=ollama\n"
    )
    # the block is reused when lines follow it
    env.lines += ["\n", "# user note\n", "B=2\n"]
    env.set("NEW_KEY", "x")
    text = env.render()
    assert text.count(INIT_BLOCK) == 1
    assert text.index("NEW_KEY=x") < text.index("# user note")


def test_set_is_idempotent_and_quotes_odd_values():
    env = EnvFile(TEMPLATE, existed=True)
    for _ in range(3):
        env.set("AUTOONTOLOGY_CHAT_PROVIDER", "codex")
        env.set("X_URL", "http://h:1/v1")
        env.set("X_TEXT", 'a b "c" #d')
    once = env.render()
    env.set("AUTOONTOLOGY_CHAT_PROVIDER", "codex")
    assert env.render() == once and once.count("X_URL=") == 1
    assert env.get("X_TEXT") == 'a b "c" #d' and env.get("X_URL") == "http://h:1/v1"
    with pytest.raises(ValueError):
        env.set("BAD KEY", "1")
    with pytest.raises(ValueError):
        env.set("K", "a\nb")


def test_unset_and_crlf_preserved():
    text = "# c\r\nA=1\r\nexport B=2\r\n"
    env = EnvFile(text, existed=True)
    env.set("B", "3")
    assert env.render() == "# c\r\nA=1\r\nB=3\r\n"
    env.unset("A")
    assert env.render() == "# c\r\nB=3\r\n"
    assert ("A", "1", None) in env.changes()


def test_round_trip_keeps_every_byte(tmp_path):
    path = tmp_path / ".env"
    path.write_text(TEMPLATE, encoding="utf-8")
    env = EnvFile.load(path)
    assert env.render() == TEMPLATE
    assert env.write(path) is None  # unchanged: nothing written, no backup
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_write_is_atomic_with_backup_and_keeps_mode(tmp_path):
    path = tmp_path / ".env"
    path.write_text(TEMPLATE, encoding="utf-8")
    os.chmod(path, 0o640)
    env = EnvFile.load(path)
    env.set("AUTOONTOLOGY_CHAT_PROVIDER", "claude")
    backup = env.write(path)
    assert backup is not None and backup.name.startswith(".env.bak-")
    assert backup.read_text(encoding="utf-8") == TEMPLATE
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]
    # a second write of the same content changes nothing
    assert env.changes() == [] and env.write(path) is None
    # another change on the same second gets its own backup
    env.set("AUTOONTOLOGY_CHAT_PROVIDER", "codex")
    second = env.write(path)
    assert second is not None and second != backup and backup.exists()


def test_new_file_from_template_is_0600(tmp_path):
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")
    path = tmp_path / "sub" / ".env"
    env = EnvFile.load(path, template=template)
    env.set("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")
    assert ("POSTGRES_USER", None, "me") in env.changes()  # a new file: every key is new
    assert env.write() is None  # a new file has no backup
    assert path.read_text(encoding="utf-8") == TEMPLATE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".env.bak-*"))


def test_describe_change_hides_secrets():
    assert describe_change("AUTOONTOLOGY_OPENAI_API_KEY", None, "sk-x") == (
        "AUTOONTOLOGY_OPENAI_API_KEY: (unset) -> (hidden)"
    )
    assert describe_change("A", "1", "2") == "A: 1 -> 2"


# --- sample sets ------------------------------------------------------------------------


def _samples(root: Path) -> Path:
    qs = root / "samples" / "quickstart"
    (qs / "structured").mkdir(parents=True)
    (qs / "structured" / "customers.csv").write_text("id\n1\n", encoding="utf-8")
    (qs / "documents").mkdir()
    (qs / "documents" / "규정.md").write_text("# 규정\n본문\n", encoding="utf-8")
    (qs / "documents" / "LICENSE").write_text("x", encoding="utf-8")
    hb = root / "samples" / "hanbit"
    for part in ("structured", "documents", "images"):
        (hb / part).mkdir(parents=True)
    (hb / "structured" / "a.csv").write_text("id\n1\n", encoding="utf-8")
    (hb / "documents" / "b.txt").write_text("텍스트", encoding="utf-8")
    (hb / "images" / "c.png").write_bytes(b"\x89PNG\r\n")
    (root / "samples" / "other").mkdir()  # not in the allowlist
    return root / "samples"


def _settings(isolated_env, monkeypatch) -> Settings:
    monkeypatch.setenv("AUTOONTOLOGY_SAMPLES_DIR", str(_samples(Path(isolated_env))))
    return Settings.from_env()


def test_sample_sets_are_listed_from_the_allowlist(isolated_env, monkeypatch):
    settings = _settings(isolated_env, monkeypatch)
    store = Store.for_home(settings.home)
    sets = onboarding.list_sample_sets(settings, store)
    assert [s["set"] for s in sets] == ["quickstart", "hanbit"]
    quick = sets[0]
    assert [(s["name"], s["kind"], s["files"]) for s in quick["sources"]] == [
        ("quick", "structured", 1),
        ("quick-docs", "documents", 1),
    ]
    assert quick["registered"] is False and quick["sources"][0]["bytes"] == 5
    assert [s["name"] for s in sets[1]["sources"]] == ["hanbit", "hanbit-docs", "hanbit-images"]
    with pytest.raises(onboarding.SampleSetError, match="unknown sample set"):
        onboarding.sample_sources(settings, "other")


def test_register_sample_set_is_idempotent_and_replaces_moved_paths(isolated_env, monkeypatch):
    settings = _settings(isolated_env, monkeypatch)
    store = Store.for_home(settings.home)
    removed = []

    def remove(settings, store, src, *, drop_db, notify):
        removed.append((src.name, drop_db))
        store.delete_source(src.id)

    said: list[str] = []
    first = onboarding.register_sample_set(settings, store, "quickstart", notify=said.append)
    assert [(s.name, a) for s, a in first] == [("quick", "created"), ("quick-docs", "created")]
    again = onboarding.register_sample_set(settings, store, "quickstart", remove=remove)
    assert [a for _s, a in again] == ["kept", "kept"] and removed == []
    assert onboarding.list_sample_sets(settings, store)[0]["registered"] is True
    # the folder moved (e.g. an old checkout layout): replaced, database dropped
    moved = Path(isolated_env) / "moved"
    (Path(settings.samples_dir) / "quickstart").rename(moved)
    (Path(settings.samples_dir) / "quickstart").mkdir()
    (moved / "structured").rename(Path(settings.samples_dir) / "quickstart" / "structured")
    (moved / "documents").rename(Path(settings.samples_dir) / "quickstart" / "documents")
    store.set_locator(store.get_source("quick").id, '{"path": "/old/place", "recursive": false}')
    said.clear()
    third = onboarding.register_sample_set(
        settings, store, "quickstart", notify=said.append, remove=remove
    )
    assert [a for _s, a in third] == ["replaced", "kept"]
    assert removed == [("quick", True)]
    assert any("replacing it with" in line for line in said)


def test_missing_set_folder_says_what_to_do(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_SAMPLES_DIR", str(Path(isolated_env) / "none"))
    settings = Settings.from_env()
    with pytest.raises(onboarding.SampleSetError, match="git pull"):
        onboarding.sample_sources(settings, "hanbit")
    assert onboarding.list_sample_sets(settings, Store.for_home(settings.home)) == []


def test_duration_rows_mark_estimates_and_the_current_provider():
    rows = onboarding.duration_rows("quickstart", "claude")
    assert [r[0] for r in rows] == ["ollama", "claude", "codex", "openai"]
    assert any("your provider" in r[2] for r in rows if r[0] == "claude")
    assert all(("estimate" in r[2]) or ("measured" in r[2]) for r in rows)
