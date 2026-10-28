# SPDX-License-Identifier: MIT
import pytest

from autoontology import doctor
from autoontology.config import Settings
from autoontology.doctor import Check, exit_code, format_checks


def test_format_checks_aligns_columns():
    checks = [
        Check(".env", "OK", "/x/.env"),
        Check("alembic", "WARN", "current none", "run `autoontology migrate`"),
        Check("postgres", "FAIL", "refused", "docker compose up -d"),
    ]
    lines = format_checks(checks).splitlines()
    assert lines[0].split() == ["CHECK", "STATUS", "DETAIL", "REMEDY"]
    status_col = lines[0].index("STATUS")
    assert [line[status_col : status_col + 4] for line in lines[1:]] == ["OK  ", "WARN", "FAIL"]
    assert lines[2].endswith("run `autoontology migrate`")


def test_exit_code_only_fails_on_fail():
    assert exit_code([Check("a", "OK"), Check("b", "WARN"), Check("c", "SKIP")]) == 0
    assert exit_code([Check("a", "OK"), Check("b", "FAIL")]) == 1


def _stub_infra(monkeypatch):
    monkeypatch.setattr(doctor, "check_env_file", lambda: Check(".env", "OK"))
    monkeypatch.setattr(doctor, "check_postgres", lambda s: [Check("postgres", "OK")])
    monkeypatch.setattr(doctor, "check_ports", lambda s: [Check("port", "OK")])
    monkeypatch.setattr(doctor, "check_node", lambda: Check("node", "OK"))
    monkeypatch.setattr(doctor, "check_kordoc", lambda s: Check("kordoc", "OK"))


def test_skip_models(isolated_env, monkeypatch):
    _stub_infra(monkeypatch)
    monkeypatch.setattr(doctor, "check_ollama", lambda s: Check("ollama", "OK"))

    def boom(_):
        raise AssertionError("model checks must be skipped")

    monkeypatch.setattr(doctor, "check_ollama_chat", boom)
    monkeypatch.setattr(doctor, "check_embed", boom)
    checks = doctor.run_checks(Settings.from_env(), skip_models=True)
    names = [c.name for c in checks]
    assert "ollama" in names
    assert [c.status for c in checks if "endpoint" in c.name] == ["SKIP", "SKIP"]
    assert exit_code(checks) == 0


def _openai(monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "m")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_EMBED_MODEL", "e")


def test_ollama_check_only_for_ollama_profile(isolated_env, monkeypatch):
    _stub_infra(monkeypatch)
    _openai(monkeypatch)
    monkeypatch.setattr(doctor, "check_ollama", lambda s: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(doctor, "check_openai_chat", lambda s: Check("chat endpoint", "OK"))
    monkeypatch.setattr(doctor, "check_embed", lambda s: Check("embedding endpoint", "FAIL"))
    checks = doctor.run_checks(Settings.from_env())
    assert "ollama" not in [c.name for c in checks]
    assert exit_code(checks) == 1


def test_invalid_profile_skips_network_checks(isolated_env, monkeypatch):
    _stub_infra(monkeypatch)
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")  # no base URL nor key
    checks = doctor.run_checks(Settings.from_env())
    profile = next(c for c in checks if c.name.startswith("model profile"))
    assert profile.status == "FAIL" and "AUTOONTOLOGY_OPENAI_BASE_URL" in profile.detail
    assert [c.status for c in checks if "endpoint" in c.name] == ["SKIP", "SKIP"]


def test_openai_chat_check_calls_the_endpoint_directly(isolated_env, monkeypatch):
    import httpx

    from autoontology.web.providers import passthrough

    _openai(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"p"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    real = passthrough.PassthroughProvider.client
    monkeypatch.setattr(
        passthrough.PassthroughProvider,
        "client",
        lambda self: setattr(self, "transport", httpx.MockTransport(handler)) or real(self),
    )
    check = doctor.check_openai_chat(Settings.from_env())
    assert check.status == "OK", check
    assert str(seen[0].url) == "https://llm.example.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-real"


def test_missing_env_file_is_only_a_warning(isolated_env):
    check = doctor.check_env_file()
    assert check.status == "WARN" and "defaults" in check.detail


def test_guard_turns_exceptions_into_fail():
    def explode():
        raise RuntimeError("kaput\nsecond line")

    check = doctor._guard("x", explode, "fix it")
    assert (check.status, check.detail, check.remedy) == ("FAIL", "RuntimeError: kaput", "fix it")


def test_timeout_check():
    import time

    check = doctor._with_timeout("slow", lambda: time.sleep(5) or Check("slow", "OK"), 0.2, "r")
    assert check.status == "FAIL" and "0s" in check.detail


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *_):
        return self

    def fetchone(self):
        return ("17.0",)


def test_postgres_errors_after_connect_become_fail_rows(isolated_env, monkeypatch):
    import psycopg

    from autoontology.engine import pg

    settings = Settings.from_env()
    monkeypatch.setattr(pg, "connect", lambda *a, **k: _FakeConn())

    def refuse(*_a, **_k):
        raise psycopg.OperationalError("connection to autoontology refused\\ndetail")

    monkeypatch.setattr(pg, "database_exists", refuse)
    checks = doctor.check_postgres(settings)
    assert [(c.name, c.status) for c in checks] == [
        ("postgres", "OK"),
        ("database autoontology", "FAIL"),
    ]
    assert "OperationalError" in checks[1].detail

    monkeypatch.setattr(pg, "database_exists", lambda *_: True)
    monkeypatch.setattr(pg, "extension_version", refuse)
    monkeypatch.setattr(doctor, "check_migrations", lambda s: Check("alembic", "OK"))
    checks = doctor.check_postgres(settings)
    assert [c.status for c in checks] == ["OK", "OK", "FAIL", "OK"]
    assert checks[2].name == "extension vector"


class _Resp:
    def __init__(self, names):
        self._names = names

    def raise_for_status(self):
        return None

    def json(self):
        return {"models": [{"name": n} for n in self._names]}


def test_ollama_check_wants_the_no_think_alias(isolated_env, monkeypatch):
    settings = Settings.from_env()
    monkeypatch.setattr(doctor.httpx, "get", lambda *a, **k: _Resp(["qwen3:8b", "bge-m3:latest"]))
    check = doctor.check_ollama(settings)
    assert check.status == "FAIL" and "openai/qwen3:8b" in check.detail
    assert check.remedy == "uv run autoontology setup"
    monkeypatch.setattr(
        doctor.httpx, "get", lambda *a, **k: _Resp(["openai/qwen3:8b", "bge-m3:latest"])
    )
    assert doctor.check_ollama(settings).status == "OK"


def test_no_think_check_reports_completion_tokens(isolated_env, monkeypatch):
    from autoontology.engine import ollama

    settings = Settings.from_env()
    monkeypatch.setattr(ollama, "chat_probe", lambda *a, **k: ("pong", 2))
    check = doctor.check_no_think(settings)
    assert check.status == "OK" and "completion_tokens 2" in check.detail
    monkeypatch.setattr(ollama, "chat_probe", lambda *a, **k: ("<think>..", 190))
    check = doctor.check_no_think(settings)
    assert check.status == "FAIL" and "completion_tokens 190" in check.detail
    assert "autoontology setup" in check.remedy


def test_vector_table_dimension_row(isolated_env, monkeypatch):
    from autoontology.engine import jobs

    settings = Settings.from_env()
    monkeypatch.setattr(jobs, "embedding_dimension", lambda s: 1024)
    monkeypatch.setattr(
        jobs,
        "vector_table_state",
        lambda s: {"data_objects_layer": ("halfvec(1024)", 10), "semantic_layer": None},
    )
    check = doctor.check_vector_tables(settings)
    assert check.status == "OK" and "embedder 1024" in check.detail
    monkeypatch.setattr(
        jobs,
        "vector_table_state",
        lambda s: {"data_objects_layer": ("halfvec(2048)", 0), "semantic_layer": None},
    )
    assert doctor.check_vector_tables(settings).status == "WARN"
    monkeypatch.setattr(
        jobs,
        "vector_table_state",
        lambda s: {"data_objects_layer": ("halfvec(2048)", 5), "semantic_layer": None},
    )
    check = doctor.check_vector_tables(settings)
    assert check.status == "FAIL" and "2048" in check.detail and "1024" in check.detail


def test_ollama_profile_runs_no_think_and_vector_rows(isolated_env, monkeypatch):
    _stub_infra(monkeypatch)
    monkeypatch.setattr(doctor, "check_ollama", lambda s: Check("ollama", "OK"))
    monkeypatch.setattr(
        doctor,
        "check_openai_chat",
        lambda s: (_ for _ in ()).throw(AssertionError("wrong provider")),
    )
    monkeypatch.setattr(doctor, "check_ollama_chat", lambda s: Check("chat endpoint", "OK"))
    monkeypatch.setattr(doctor, "check_no_think", lambda s: Check("chat no-think", "OK"))
    monkeypatch.setattr(doctor, "check_embed", lambda s: Check("embedding endpoint", "OK"))
    monkeypatch.setattr(doctor, "check_vector_tables", lambda s: Check("vector tables", "OK"))
    import httpx

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(doctor.httpx, "get", down)
    checks = doctor.run_checks(Settings.from_env())
    names = [c.name for c in checks]
    assert names[-5:] == [
        "chat route",
        "chat endpoint",
        "chat no-think",
        "embedding endpoint",
        "vector tables",
    ]
    route = next(c for c in checks if c.name == "chat route")
    assert route.status == "OK" and "http://127.0.0.1:7800/v1" in route.detail
    assert "service down" in route.detail


def test_ollama_chat_check_uses_proxy_payload_directly(isolated_env, monkeypatch):
    import httpx

    from autoontology.engine import ollama

    settings = Settings.from_env()
    seen = {}

    def fake_post(url, json, **kw):
        seen["url"], seen["body"] = url, json
        content = seen.get("content", '{"answer": "pong"}')
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"completion_tokens": 6},
            },
        )

    monkeypatch.setattr(ollama.httpx, "post", fake_post)
    check = doctor.check_ollama_chat(settings)
    assert check.status == "OK" and "completion_tokens 6" in check.detail
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"  # not the service
    assert seen["body"]["reasoning_effort"] == "none"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    seen["content"] = '```json\n{"answer": "pong"}\n```'
    assert doctor.check_ollama_chat(settings).status == "FAIL"


def test_kordoc_row_warns_with_install_command(isolated_env, monkeypatch):
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", str(isolated_env / "nobin"))
    check = doctor.check_kordoc(Settings.from_env())
    assert check.status == "WARN" and check.remedy == "uv run autoontology tools install-kordoc"
    assert "MD/TXT/HTML work" in check.detail
    node = doctor.check_node()
    assert node.status == "WARN" and "Node.js 20+" in node.remedy


def test_kordoc_row_ok_and_omit_optional(isolated_env, monkeypatch):
    import shutil

    import pytest

    if shutil.which("node") is None:
        pytest.skip("needs node")
    from autoontology.unstructured import kordoc

    settings = Settings.from_env()
    cli = kordoc.home_cli(settings.home)
    cli.parent.mkdir(parents=True)
    cli.write_text("console.log('4.24.13')", encoding="utf-8")
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    check = doctor.check_kordoc(settings)
    assert check.status == "OK" and check.detail.startswith("4.24.13 at ")
    (settings.home / "kordoc" / "kordoc.version").write_text("4.24.13\nomit=optional\n")
    check = doctor.check_kordoc(settings)
    assert check.status == "WARN" and "PDF/OCR unavailable" in check.detail


def test_ollama_chat_model_without_router_prefix_warns(isolated_env, monkeypatch):
    """Review M-7: upstream picks ChatNVIDIA (60 s timeout) for names without openai/."""
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_CHAT_MODEL", "qwen3:8b")
    check = doctor.check_profile(Settings.from_env())
    assert check.status == "WARN" and "openai/ prefix" in check.detail
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_CHAT_MODEL", "openai/qwen3:8b")
    assert doctor.check_profile(Settings.from_env()).status == "OK"


def _boom(*_a, **_k):
    raise RuntimeError("unexpected")


@pytest.mark.parametrize("chat", ["ollama", "claude", "openai"])
def test_every_fail_or_warn_row_has_a_remedy(isolated_env, monkeypatch, chat):
    """Whatever goes wrong, each FAIL/WARN row says what to do."""
    from autoontology.web.providers import router

    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", chat)
    monkeypatch.setenv("POSTGRES_PORT", "1")  # nothing listens there
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_ENDPOINT", "http://127.0.0.1:1/v1")
    for name in ("check_node", "check_kordoc", "check_ocr_models", "check_embed_provider"):
        monkeypatch.setattr(doctor, name, _boom)
    monkeypatch.setenv("AUTOONTOLOGY_VISION_MODEL", "gemma4:e4b")
    monkeypatch.setattr(
        router.Registry,
        "status",
        lambda self, *a, **k: router.ProviderStatus(chat, False, False, "down", ""),
    )
    checks = doctor.run_checks(Settings.from_env(), skip_models=True)
    bad = [c for c in checks if c.status in ("FAIL", "WARN")]
    assert bad, checks
    assert [c.name for c in bad if not c.remedy.strip()] == []
    vision = doctor._guard("vision model", _boom, doctor.VISION_REMEDY)
    assert vision.remedy


def test_missing_env_file_points_at_init(isolated_env):
    check = doctor.check_env_file()
    assert check.status == "WARN" and "autoontology init" in check.remedy
