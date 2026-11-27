# SPDX-License-Identifier: MIT
import os

import pytest

from ontology.config import upstream_model_var_names

_ISOLATED_VARS = [
    "ONTOLOGY_HOME",
    "ONTOLOGY_EXPORTS_DIR",
    "ONTOLOGY_PORT",
    "BACKEND_PORT",
    "ONTOLOGY_CHAT_PROVIDER",
    "ONTOLOGY_EMBED_PROVIDER",
    "ONTOLOGY_DERIVED_ENV",
    "ONTOLOGY_OLLAMA_ENDPOINT",
    "ONTOLOGY_OLLAMA_CHAT_MODEL",
    "ONTOLOGY_OLLAMA_EMBED_MODEL",
    "ONTOLOGY_OLLAMA_NO_THINK",
    "ONTOLOGY_OPENAI_BASE_URL",
    "ONTOLOGY_OPENAI_API_KEY",
    "ONTOLOGY_OPENAI_CHAT_MODEL",
    "ONTOLOGY_OPENAI_EMBED_MODEL",
    "ONTOLOGY_OPENAI_TIMEOUT_S",
    "ONTOLOGY_LLM_WORKERS",
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

#: Every developer/shell variable the package or upstream reads, by prefix: unit tests
#: start from none of them (integration tests keep the real environment).
_SCRUBBED_PREFIXES = (
    "ONTOLOGY_",
    "AUTOONTOLOGY_",  # the legacy names (read when ONTOLOGY_* is unset)
    "KORDOC_",
    "POSTGRES_",
    "LLM_",
    "SEMANTIC_LLM_",
    "TEXT_TO_SQL_",
)
_SCRUBBED_NAMES = {"BACKEND_PORT", "INGESTION_SERVICE_URL", "APP_URL", *_ISOLATED_VARS}
_KEPT = {"ONTOLOGY_INTEGRATION"}

#: CLI messages follow the locale (``LANG=ko_KR...`` means Korean): tests assert the
#: English texts unless they set ONTOLOGY_LANG themselves. Set at import so the
#: typer help texts (built when ``ontology.cli`` is imported) are English too.
os.environ.setdefault("ONTOLOGY_LANG", "en")


def _scrub_env(monkeypatch) -> None:
    for name in list(os.environ):
        if name in _KEPT:
            continue
        if name in _SCRUBBED_NAMES or name.startswith(_SCRUBBED_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ONTOLOGY_LANG", "en")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ONTOLOGY_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set ONTOLOGY_INTEGRATION=1 to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_home(request, tmp_path_factory, monkeypatch):
    """Unit tests never read the repo's real ``data/`` (store, run dir) or ``.env``:
    every non-integration test gets its own ONTOLOGY_HOME/EXPORTS_DIR (``isolated_env``
    still overrides them with its own tmp paths)."""
    if request.node.get_closest_marker("integration") is not None:
        return
    base = tmp_path_factory.mktemp("home")
    _scrub_env(monkeypatch)
    # nor the repo's .env (it may point at a live service, e.g. the integration ports)
    monkeypatch.setenv("ONTOLOGY_ENV_FILE", str(base / "absent.env"))
    monkeypatch.setenv("ONTOLOGY_HOME", str(base / "home"))
    monkeypatch.setenv("ONTOLOGY_EXPORTS_DIR", str(base / "exports"))


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """No repo .env and no model/Ontology vars from the developer's shell."""
    _scrub_env(monkeypatch)
    monkeypatch.setenv("ONTOLOGY_ENV_FILE", str(tmp_path / "absent.env"))
    for name in _ISOLATED_VARS:
        # setenv first so monkeypatch records the original state even when unset
        # (a later setenv in the test would otherwise record a value load_env() put there)
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("ONTOLOGY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ONTOLOGY_EXPORTS_DIR", str(tmp_path / "exports"))
    saved = dict(os.environ)
    yield tmp_path
    # load_env() copies .env values into os.environ behind monkeypatch's back
    for key in set(os.environ) - set(saved):
        del os.environ[key]
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _workflow_threads(monkeypatch):
    """No test starts the workflow scheduler unless it asks (``scheduler.AUTOSTART``);
    workflow worker threads are joined and the action listener removed afterwards."""
    from ontology.logic import scheduler, workflows

    monkeypatch.setattr(scheduler, "AUTOSTART", False)
    yield
    workflows.wait(10)
    workflows.uninstall()
