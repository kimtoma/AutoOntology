import sys

import pytest

from autoontology.config import (
    UPSTREAM_TRIPLET_PREFIXES,
    Settings,
    expand_model_profile,
    load_env,
    postgres_env,
    validate_model_profile,
)


def test_default_is_ollama_chat_and_embed(isolated_env):
    s = Settings.from_env()
    assert (s.chat_provider, s.embed_provider, s.model_profile) == ("ollama", "ollama", "ollama")
    assert s.selection_origin["chat_provider"] == "default"
    assert (s.port, s.backend_port) == (7800, 3101)
    assert (s.postgres_host, s.postgres_port, s.postgres_user, s.postgres_password,
            s.postgres_database) == ("localhost", 5432, "autoontology", "autoontology",
                                     "autoontology")
    assert s.ollama_endpoint == "http://localhost:11434/v1"


def test_ollama_expands_every_triplet(isolated_env, monkeypatch):
    monkeypatch.setenv("REASONING_MODEL", "nvidia/should-be-overridden")
    env = expand_model_profile(Settings.from_env())
    for prefix in UPSTREAM_TRIPLET_PREFIXES:
        assert env[f"{prefix}_API_KEY"] == "ollama"
    # Chat goes through the service's /v1 router (injects reasoning_effort=none);
    # embeddings go straight to Ollama.
    for prefix in ("DEFAULT_MODELS", "REASONING", "NON_REASONING", "RERANK"):
        assert env[f"{prefix}_ENDPOINT"] == "http://127.0.0.1:7800/v1"
        assert env[f"{prefix}_MODEL"] == "openai/qwen3:8b"
    assert env["EMBED_ENDPOINT"] == "http://localhost:11434/v1"
    assert env["EMBED_MODEL"] == "bge-m3"


def test_ollama_sets_llm_limits_only_when_unset(isolated_env, monkeypatch):
    env = expand_model_profile(Settings.from_env())
    assert (env["LLM_INVOKE_TIMEOUT_S"], env["LLM_MAX_INFLIGHT"],
            env["SEMANTIC_LLM_MAX_INFLIGHT"]) == ("180", "2", "1")
    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_S", "600")
    env = expand_model_profile(Settings.from_env())
    assert "LLM_INVOKE_TIMEOUT_S" not in env  # the user's value stays in force
    assert env["LLM_MAX_INFLIGHT"] == "2"


def test_ollama_overrides(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_ENDPOINT", "http://gpu-box:11434/v1")
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_CHAT_MODEL", "openai/llama3:8b")
    monkeypatch.setenv("AUTOONTOLOGY_OLLAMA_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("AUTOONTOLOGY_PORT", "7900")
    env = expand_model_profile(Settings.from_env())
    assert env["REASONING_ENDPOINT"] == "http://127.0.0.1:7900/v1"
    assert env["REASONING_MODEL"] == "openai/llama3:8b"
    assert env["EMBED_ENDPOINT"] == "http://gpu-box:11434/v1"
    assert env["EMBED_MODEL"] == "nomic-embed-text"


def _openai(monkeypatch, embed: str = "openai") -> None:
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_EMBED_PROVIDER", embed)
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1/")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_API_KEY", "nvapi-secret-value")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")


def test_openai_chat_goes_through_router_with_prefixed_model(isolated_env, monkeypatch):
    _openai(monkeypatch)
    s = Settings.from_env()
    assert s.model_profile == "openai"
    assert s.openai_base_url == "https://integrate.api.nvidia.com/v1"  # trailing / dropped
    assert s.upstream_chat_model == "openai/nvidia/nemotron-3-nano-30b-a3b"
    env = expand_model_profile(s)
    for prefix in ("DEFAULT_MODELS", "REASONING", "NON_REASONING", "RERANK"):
        assert env[f"{prefix}_ENDPOINT"] == "http://127.0.0.1:7800/v1"
        assert env[f"{prefix}_MODEL"] == "openai/nvidia/nemotron-3-nano-30b-a3b"
        # the router adds the real key; upstream only needs a non-empty one
        assert env[f"{prefix}_API_KEY"] != "nvapi-secret-value"
    assert env["EMBED_ENDPOINT"] == "https://integrate.api.nvidia.com/v1"
    assert env["EMBED_API_KEY"] == "nvapi-secret-value"
    assert env["EMBED_MODEL"] == "nvidia/nemotron-3-embed-1b"
    assert "LLM_INVOKE_TIMEOUT_S" not in env
    assert "nvapi-secret-value" not in repr(s)
    assert validate_model_profile(s) == []


def test_openai_chat_with_ollama_embeddings(isolated_env, monkeypatch):
    _openai(monkeypatch, embed="ollama")
    s = Settings.from_env()
    assert s.model_profile == "openai+ollama"
    env = expand_model_profile(s)
    assert env["EMBED_ENDPOINT"] == "http://localhost:11434/v1"
    assert env["EMBED_MODEL"] == "bge-m3"


def test_openai_validation_names_missing_values(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    problems = validate_model_profile(Settings.from_env())
    assert any("AUTOONTOLOGY_OPENAI_BASE_URL" in p for p in problems)
    assert any("AUTOONTOLOGY_OPENAI_API_KEY" in p for p in problems)
    assert any("AUTOONTOLOGY_OPENAI_CHAT_MODEL" in p for p in problems)
    assert validate_model_profile(Settings.from_env()) and not validate_model_profile(
        Settings(home=isolated_env))


@pytest.mark.parametrize("value", ["claude", "codex", "nvidia", "custom"])
def test_removed_providers_are_refused(isolated_env, monkeypatch, value):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", value)
    with pytest.raises(ValueError, match="AUTOONTOLOGY_CHAT_PROVIDER"):
        Settings.from_env()


def test_postgres_env_has_user_and_password(isolated_env):
    env = postgres_env(Settings.from_env())
    assert env["POSTGRES_USER"] == "autoontology"
    assert "POSTGRES_PASSWORD" in env  # upstream reads it with os.environ[...]
    assert env["POSTGRES_DATABASE"] == "autoontology"


def test_config_still_does_not_import_engine(isolated_env):
    sys.modules.pop("auto_ontology", None)
    expand_model_profile(Settings.from_env())
    assert "auto_ontology" not in sys.modules


def test_selection_precedence_shell_over_dotenv(isolated_env, monkeypatch):
    env_file = isolated_env / "test.env"
    env_file.write_text("AUTOONTOLOGY_CHAT_PROVIDER=openai\nAUTOONTOLOGY_EMBED_PROVIDER=openai\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    s = Settings.from_env()
    assert (s.chat_provider, s.embed_provider) == ("openai", "openai")
    assert s.selection_origin["chat_provider"] == ".env"
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")
    s = Settings.from_env()
    assert s.chat_provider == "ollama" and s.selection_origin["chat_provider"] == "shell"
    assert s.embed_provider == "openai"


def test_dotenv_values_loaded_into_environ_do_not_count_as_shell(isolated_env, monkeypatch):
    env_file = isolated_env / "test.env"
    env_file.write_text("AUTOONTOLOGY_CHAT_PROVIDER=openai\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "x")  # so monkeypatch restores "unset"
    monkeypatch.delenv("AUTOONTOLOGY_CHAT_PROVIDER")
    load_env()  # copies the .env value into os.environ, as every AutoOntology process does
    s = Settings.from_env()
    assert s.chat_provider == "openai" and s.selection_origin["chat_provider"] == ".env"


def test_derived_keys_from_a_parent_are_not_user_settings(isolated_env, monkeypatch):
    # a child process inherits the parent's derived triplets: they must not look user-set
    monkeypatch.setenv("REASONING_MODEL", "openai/qwen3:8b")
    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_S", "180")
    monkeypatch.setenv("AUTOONTOLOGY_DERIVED_ENV", "REASONING_MODEL,LLM_INVOKE_TIMEOUT_S")
    s = Settings.from_env()
    assert "REASONING_MODEL" not in s.upstream_models
    assert "LLM_INVOKE_TIMEOUT_S" not in s.llm_limits
    assert expand_model_profile(s)["LLM_INVOKE_TIMEOUT_S"] == "180"
