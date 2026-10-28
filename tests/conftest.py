import os

import pytest

from autoontology.config import upstream_model_var_names

_ISOLATED_VARS = [
    "AUTOONTOLOGY_HOME",
    "AUTOONTOLOGY_EXPORTS_DIR",
    "AUTOONTOLOGY_PORT",
    "BACKEND_PORT",
    "AUTOONTOLOGY_CHAT_PROVIDER",
    "AUTOONTOLOGY_EMBED_PROVIDER",
    "AUTOONTOLOGY_DERIVED_ENV",
    "AUTOONTOLOGY_OLLAMA_ENDPOINT",
    "AUTOONTOLOGY_OLLAMA_CHAT_MODEL",
    "AUTOONTOLOGY_OLLAMA_EMBED_MODEL",
    "AUTOONTOLOGY_OLLAMA_NO_THINK",
    "AUTOONTOLOGY_OPENAI_BASE_URL",
    "AUTOONTOLOGY_OPENAI_API_KEY",
    "AUTOONTOLOGY_OPENAI_CHAT_MODEL",
    "AUTOONTOLOGY_OPENAI_EMBED_MODEL",
    "AUTOONTOLOGY_OPENAI_TIMEOUT_S",
    "AUTOONTOLOGY_LLM_WORKERS",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DATABASE",
    "LLM_INVOKE_TIMEOUT_S",
    "LLM_MAX_INFLIGHT",
    "SEMANTIC_LLM_MAX_INFLIGHT",
    *upstream_model_var_names(),
]


def pytest_collection_modifyitems(config, items):
    if os.environ.get("AUTOONTOLOGY_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set AUTOONTOLOGY_INTEGRATION=1 to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_home(request, tmp_path_factory, monkeypatch):
    """Unit tests never read the repo's real ``data/`` (store, run dir) or ``.env``:
    every non-integration test gets its own AUTOONTOLOGY_HOME/EXPORTS_DIR (``isolated_env``
    still overrides them with its own tmp paths)."""
    if request.node.get_closest_marker("integration") is not None:
        return
    base = tmp_path_factory.mktemp("home")
    # nor the repo's .env (it may point at a live service, e.g. the integration ports)
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(base / "absent.env"))
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(base / "home"))
    monkeypatch.setenv("AUTOONTOLOGY_EXPORTS_DIR", str(base / "exports"))


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """No repo .env and no model/AutoOntology vars from the developer's shell."""
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(tmp_path / "absent.env"))
    for name in _ISOLATED_VARS:
        # setenv first so monkeypatch records the original state even when unset
        # (a later setenv in the test would otherwise record a value load_env() put there)
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AUTOONTOLOGY_EXPORTS_DIR", str(tmp_path / "exports"))
    saved = dict(os.environ)
    yield tmp_path
    # load_env() copies .env values into os.environ behind monkeypatch's back
    for key in set(os.environ) - set(saved):
        del os.environ[key]
    os.environ.update(saved)
