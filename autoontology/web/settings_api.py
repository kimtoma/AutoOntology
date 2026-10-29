# SPDX-License-Identifier: MIT
"""The Settings page's API, under ``/api/settings``: choose, test and apply the model
provider without editing ``.env`` by hand.

* ``GET /api/settings``: what ``.env`` (and the shell) select now, where each value comes
  from, the CLI logins, Ollama's models, the stored vectors, and whether the running
  processes still use other settings (``restart_required``). Never a key: only
  ``api_key_set``.
* ``POST /api/settings/test``: a candidate (only the fields that change) checked without
  applying it: one pong through an in-process router for the candidate, one embedding.
* ``PUT /api/settings``: writes the candidate to ``.env`` (``onboarding.EnvFile``: mode
  0600, backup). Keys the shell sets are refused (400 ``shell_override``); an embedding
  dimension that differs from the stored vectors needs ``confirm_reingest`` (409
  ``embedding_dimension_changed``).
* ``POST /api/settings/restart``: 202; a detached helper (``engine/restart.py``) restarts
  the service and the backend, which the engine needs to pick up another provider.
  ``GET /api/settings/restart/{id}`` (answered by the new service) reports how it went.

The OpenAI-compatible API key is accepted only in the ``test``/``PUT`` bodies, written to
``.env`` only, and never logged nor returned. Every route sits behind the API's
local/tailnet guard (``api.install``'s ``_local_only``).
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import threading
import time
from typing import Any, Literal

import anyio
import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from autoontology.config import (
    CHAT_MODEL_RE,
    CHAT_PROVIDERS,
    CLI_DEFAULT_CHAT_MODELS,
    CLI_KNOWN_MODELS,
    CLI_PROVIDERS,
    EMBED_PROVIDERS,
    OLLAMA_DEFAULT_CHAT_MODEL,
    OLLAMA_DEFAULT_EMBED_MODEL,
    PLACEHOLDER_KEY_MARKERS,
    Settings,
    dotenv_layer,
    env_file_path,
    env_origin,
    validate_model_profile,
)
from autoontology.web.api import PREFIX, Api, ApiError, mask
from autoontology.web.runs import WORKSPACE, RunStartError

logger = logging.getLogger("autoontology.web")

#: The ``.env`` keys this page edits -> the ``Settings`` field each one sets.
EDITABLE: dict[str, str] = {
    "AUTOONTOLOGY_CHAT_PROVIDER": "chat_provider",
    "AUTOONTOLOGY_EMBED_PROVIDER": "embed_provider",
    "AUTOONTOLOGY_OLLAMA_CHAT_MODEL": "ollama_chat_model",
    "AUTOONTOLOGY_OLLAMA_EMBED_MODEL": "ollama_embed_model",
    "AUTOONTOLOGY_CLAUDE_MODEL": "claude_model",
    "AUTOONTOLOGY_CODEX_MODEL": "codex_model",
    "AUTOONTOLOGY_OPENAI_BASE_URL": "openai_base_url",
    "AUTOONTOLOGY_OPENAI_API_KEY": "openai_api_key",
    "AUTOONTOLOGY_OPENAI_CHAT_MODEL": "openai_chat_model",
    "AUTOONTOLOGY_OPENAI_EMBED_MODEL": "openai_embed_model",
}
VAR_OF: dict[str, str] = {field: var for var, field in EDITABLE.items()}
KEY_VAR = "AUTOONTOLOGY_OPENAI_API_KEY"
#: The field holding each chat / embedding provider's model.
CHAT_MODEL_FIELD: dict[str, str] = {
    "ollama": "ollama_chat_model",
    "openai": "openai_chat_model",
    "claude": "claude_model",
    "codex": "codex_model",
}
EMBED_MODEL_FIELD: dict[str, str] = {
    "ollama": "ollama_embed_model",
    "openai": "openai_embed_model",
}
#: What an unset key means (``Settings.from_env``).
DEFAULTS: dict[str, str] = {
    "chat_provider": "ollama",
    "embed_provider": "ollama",
    "ollama_chat_model": OLLAMA_DEFAULT_CHAT_MODEL,
    "ollama_embed_model": OLLAMA_DEFAULT_EMBED_MODEL,
    "claude_model": CLI_DEFAULT_CHAT_MODELS["claude"],
    "codex_model": CLI_DEFAULT_CHAT_MODELS["codex"],
}
BASE_URL_RE = re.compile(r"^https?://[^\s\"'<>]{1,290}$")
API_KEY_MAX = 1000
TEST_CHAT_TIMEOUT_S = 150.0
TEST_EMBED_TIMEOUT_S = 60.0
#: CLI login checks are slow (they run the CLI): cached this long.
CLI_STATUS_CACHE_S = 15.0
#: The workspace claim a restart holds is released after this long if the process is
#: still alive (the helper failed before stopping it).
RESTART_CLAIM_S = 180.0
RESTART_COMMAND = "uv run autoontology restart"
DIMENSION_WARNING = (
    "the new embedding model has {new} dimensions but the stored vectors have {old}: "
    "existing loaded data must be re-ingested (rebuild every source)"
)


class SettingsIn(BaseModel):
    """A candidate: only the fields that change. ``chat_model`` / ``embed_model`` belong to
    the (new or current) chat / embedding provider; ``""`` means that provider's default."""

    model_config = ConfigDict(extra="forbid")

    chat_provider: Literal["ollama", "openai", "claude", "codex"] | None = None
    chat_model: str | None = Field(None, max_length=120)
    embed_provider: Literal["ollama", "openai"] | None = None
    embed_model: str | None = Field(None, max_length=120)
    openai_base_url: str | None = Field(None, max_length=300)
    #: A new key (empty = keep the saved one). Never echoed.
    api_key: SecretStr | None = None
    #: test only: false tests without the saved key
    use_saved_key: bool = True
    #: PUT only: remove the saved key
    clear_api_key: bool = False
    #: PUT only: accept an embedding dimension change (the data must be re-ingested)
    confirm_reingest: bool = False


# --- layers --------------------------------------------------------------------------


def _key_value(body: SettingsIn) -> str:
    raw = body.api_key.get_secret_value() if body.api_key is not None else ""
    return raw.strip()


def pending_settings(running: Settings) -> tuple[Settings, dict[str, str]]:
    """The settings a restart would load (shell > the current ``.env`` > defaults), read
    from the layers without touching ``os.environ``, and the origin of every key."""
    dot = dotenv_layer()
    origins = {var: env_origin(var) for var in EDITABLE}
    values: dict[str, Any] = {}
    for var, field in EDITABLE.items():
        origin = origins[var]
        raw = os.environ.get(var, "") if origin == "shell" else dot.get(var, "")
        value = raw.strip()
        if field == "openai_base_url":
            value = value.rstrip("/")
        values[field] = value or DEFAULTS.get(field, "")
    for field, choices in (("chat_provider", CHAT_PROVIDERS), ("embed_provider", EMBED_PROVIDERS)):
        if values[field] not in choices:  # a typo in .env: the restart would refuse it
            values[field] = getattr(running, field)
    origin = {
        "chat_provider": origins["AUTOONTOLOGY_CHAT_PROVIDER"],
        "embed_provider": origins["AUTOONTOLOGY_EMBED_PROVIDER"],
    }
    return dataclasses.replace(running, **values, selection_origin=origin), origins


def _fields(s: Settings) -> tuple[Any, ...]:
    return tuple(getattr(s, f) for f in EDITABLE.values())


def restart_required(running: Settings, pending: Settings) -> bool:
    return _fields(running) != _fields(pending)


def _clean_model(value: str, what: str) -> str:
    value = value.strip()
    if value and not CHAT_MODEL_RE.fullmatch(value):
        raise ApiError(400, "invalid_settings", f"{what} is not a valid model name")
    return value


def url_origin(url: str) -> str:
    """``scheme://host:port`` of *url* (lower case, default port filled in; "" if none)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}:{port}"


def candidate_settings(pending: Settings, body: SettingsIn, *, test: bool) -> Settings:
    """*pending* with the body's changes applied (validated: 400 ``invalid_settings``)."""
    chat = body.chat_provider or pending.chat_provider
    embed = body.embed_provider or pending.embed_provider
    updates: dict[str, Any] = {"chat_provider": chat, "embed_provider": embed}
    if body.chat_model is not None:
        model = _clean_model(body.chat_model, "the chat model")
        updates[CHAT_MODEL_FIELD[chat]] = model or DEFAULTS.get(CHAT_MODEL_FIELD[chat], "")
    if body.embed_model is not None:
        model = _clean_model(body.embed_model, "the embedding model")
        updates[EMBED_MODEL_FIELD[embed]] = model or DEFAULTS.get(EMBED_MODEL_FIELD[embed], "")
    if body.openai_base_url is not None:
        url = body.openai_base_url.strip().rstrip("/")
        if url and not BASE_URL_RE.fullmatch(url):
            raise ApiError(
                400, "invalid_settings", "the base URL must be an http:// or https:// URL"
            )
        updates["openai_base_url"] = url
    key = _key_value(body)
    if key:
        if len(key) > API_KEY_MAX or any(c.isspace() or ord(c) < 32 for c in key):
            raise ApiError(400, "invalid_settings", "the API key must be one line without spaces")
        if any(marker in key for marker in PLACEHOLDER_KEY_MARKERS):
            raise ApiError(400, "invalid_settings", "the API key is still the placeholder")
        updates["openai_api_key"] = key
    elif (body.clear_api_key and not test) or (test and not body.use_saved_key):
        updates["openai_api_key"] = ""
    new_url = updates.get("openai_base_url", pending.openai_base_url)
    if (
        "openai_api_key" not in updates
        and pending.openai_api_key
        and url_origin(new_url) != url_origin(pending.openai_base_url)
    ):
        # the saved key is never sent to (nor saved for) another endpoint than its own
        old = url_origin(pending.openai_base_url) or "no endpoint"
        raise ApiError(
            400,
            "key_required",
            f"the saved API key belongs to {old}: enter the API key for "
            f"{url_origin(new_url) or 'the new endpoint'} (a saved key is never sent to "
            "another endpoint)",
            saved_origin=url_origin(pending.openai_base_url),
        )
    cand = dataclasses.replace(pending, **updates)
    problems = validate_model_profile(cand)
    if problems:
        raise ApiError(400, "invalid_settings", "; ".join(problems), problems=problems)
    return cand


def planned_writes(cand: Settings, body: SettingsIn) -> dict[str, str | None]:
    """``{var: value | None (= remove the line, i.e. the default)}`` for the keys the body
    sets."""
    out: dict[str, str | None] = {}
    if body.chat_provider is not None:
        out["AUTOONTOLOGY_CHAT_PROVIDER"] = cand.chat_provider
    if body.chat_model is not None:
        out[VAR_OF[CHAT_MODEL_FIELD[cand.chat_provider]]] = body.chat_model.strip() or None
    if body.embed_provider is not None:
        out["AUTOONTOLOGY_EMBED_PROVIDER"] = cand.embed_provider
    if body.embed_model is not None:
        out[VAR_OF[EMBED_MODEL_FIELD[cand.embed_provider]]] = body.embed_model.strip() or None
    if body.openai_base_url is not None:
        out["AUTOONTOLOGY_OPENAI_BASE_URL"] = cand.openai_base_url or None
    if _key_value(body):
        out[KEY_VAR] = cand.openai_api_key
    elif body.clear_api_key:
        out[KEY_VAR] = None
    return out


def _redact(text: str, *secrets: str) -> str:
    from autoontology.web.providers.base import scrub

    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return scrub(text, 300)


# --- embeddings and vectors ----------------------------------------------------------


def vector_state(settings: Settings) -> dict[str, Any]:
    """``{rows, dimension}`` of the stored vectors (None when unknown: no tables, no DB)."""
    try:
        from autoontology.engine import jobs

        state = jobs.vector_table_state(settings)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"rows": None, "dimension": None, "error": type(exc).__name__}
    rows, dimension = 0, None
    for found in state.values():
        if found is None:
            continue
        kind, count = found
        rows += int(count)
        match = re.search(r"\((\d+)\)", kind)
        if match and dimension is None:
            dimension = int(match.group(1))
    return {"rows": rows, "dimension": dimension}


def embed_vector(endpoint: str, key: str, model: str, boot: Settings) -> list[float]:
    """One embedding of a fixed text with exactly these parameters (no retries)."""
    from autoontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(boot)  # upstream reads its env at import: load it before importing
    from auto_ontology.utils.embedding import EmbedParams, embed_texts

    params = EmbedParams(
        model_name=model,
        embed_invoke_url=endpoint,
        api_key=key,
        request_timeout_s=TEST_EMBED_TIMEOUT_S,
    )
    vectors = embed_texts(
        ["dimension probe"], params, "query", timeout=TEST_EMBED_TIMEOUT_S, retries=0
    )
    if not vectors or not vectors[0]:
        raise RuntimeError("the embedding endpoint returned no vector")
    return vectors[0]


def probe_embedding(cand: Settings, boot: Settings) -> dict[str, Any]:
    from autoontology.playground.service import embed_triplet

    endpoint, key, model = embed_triplet(cand)
    start = time.monotonic()
    try:
        vector = embed_vector(endpoint, key, model, boot)
    except Exception as exc:  # noqa: BLE001 - the result says why
        return {
            "ok": False,
            "model": model,
            "seconds": round(time.monotonic() - start, 2),
            "error": _redact(f"{type(exc).__name__}: {exc}", key, cand.openai_api_key),
        }
    return {
        "ok": True,
        "model": model,
        "seconds": round(time.monotonic() - start, 2),
        "dimension": len(vector),
    }


def _embedder_changed(a: Settings, b: Settings) -> bool:
    from autoontology.playground.service import embed_triplet

    return embed_triplet(a) != embed_triplet(b)


# --- the API -------------------------------------------------------------------------


class SettingsApi:
    def __init__(self, api: Api, app: FastAPI) -> None:
        self.api = api
        self.app = app
        self._write_lock = threading.Lock()
        self._cli_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._restart_claim: threading.Timer | None = None
        #: the restart this process started (its claim is released if that one fails)
        self._restart_id: str | None = None

    @property
    def running(self) -> Settings:
        return self.api.settings

    # GET -------------------------------------------------------------------------------

    def cli_status(self, settings: Settings, name: str) -> dict[str, Any]:
        from autoontology import onboarding

        cached = self._cli_cache.get(name)
        if cached and time.monotonic() - cached[0] < CLI_STATUS_CACHE_S:
            info = cached[1]
        else:
            got = onboarding.provider_info(settings, name)
            from autoontology.web.providers.base import scrub

            info = {
                "found": got.found,
                "logged_in": got.logged_in,
                "detail": scrub(got.note, 300),
                "remedy": scrub(got.remedy, 300),
            }
            self._cli_cache[name] = (time.monotonic(), info)
        return {
            **info,
            "model": settings.cli_model(name),
            "default_model": CLI_DEFAULT_CHAT_MODELS[name],
            "known_models": list(CLI_KNOWN_MODELS[name]),
            "login_command": onboarding.LOGIN_COMMANDS[name],
        }

    def ollama_status(self, settings: Settings) -> dict[str, Any]:
        from autoontology.engine.ollama import list_models

        try:
            models: list[str] | None = sorted(list_models(settings.ollama_endpoint))
        except (httpx.HTTPError, ValueError, OSError):
            models = None
        return {
            "endpoint": settings.ollama_endpoint,
            "reachable": models is not None,
            "models": models or [],
            "chat_model": settings.ollama_chat_model,
            "embed_model": settings.ollama_embed_model,
        }

    def get(self) -> dict[str, Any]:
        from autoontology.engine.restart import running_restart

        pending, origins = pending_settings(self.running)
        running = self.running
        key = pending.openai_api_key
        return {
            "chat": {
                "provider": pending.chat_provider,
                "model": pending.effective_chat_model,
                "origin": origins["AUTOONTOLOGY_CHAT_PROVIDER"],
            },
            "embed": {
                "provider": pending.embed_provider,
                "model": pending.effective_embed_model,
                "origin": origins["AUTOONTOLOGY_EMBED_PROVIDER"],
            },
            "running": {
                "chat_provider": running.chat_provider,
                "chat_model": running.effective_chat_model,
                "embed_provider": running.embed_provider,
                "embed_model": running.effective_embed_model,
            },
            "openai": {
                "base_url": mask(pending.openai_base_url),
                "api_key_set": bool(key) and not any(m in key for m in PLACEHOLDER_KEY_MARKERS),
                "chat_model": pending.openai_chat_model,
                "embed_model": pending.openai_embed_model,
            },
            "cli": {name: self.cli_status(pending, name) for name in CLI_PROVIDERS},
            "ollama": self.ollama_status(pending),
            "origins": origins,
            "locked_keys": sorted(var for var, o in origins.items() if o == "shell"),
            "vectors": vector_state(running),
            "restart_required": restart_required(running, pending),
            "restart_in_progress": running_restart(running.run_dir) is not None,
            "service_pid": os.getpid(),
            "supervised": supervised(running),
            "restart_command": RESTART_COMMAND,
        }

    # test ------------------------------------------------------------------------------

    async def test(self, body: SettingsIn) -> dict[str, Any]:
        from autoontology.web.providers import probe
        from autoontology.web.providers.router import Registry, build_router

        pending, _ = pending_settings(self.running)
        cand = candidate_settings(pending, body, test=True)
        lock = self.api._provider_test_lock
        if not lock.acquire(blocking=False):
            raise ApiError(409, "test_in_progress", "a provider test is already running")
        try:
            app = FastAPI()
            registry = Registry(cand)
            app.include_router(build_router(cand, None, registry))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                chat = await probe.router_call(c, cand.upstream_chat_model, TEST_CHAT_TIMEOUT_S)
            embed = await anyio.to_thread.run_sync(probe_embedding, cand, self.running)
            vectors = await anyio.to_thread.run_sync(vector_state, self.running)
        finally:
            lock.release()
        secret = cand.openai_api_key
        chat_out = {
            "ok": bool(chat.get("ok")),
            "provider": cand.chat_provider,
            "model": cand.effective_chat_model,
            "seconds": chat.get("seconds"),
            "answer": _redact(str(chat.get("answer") or ""), secret) or None,
            "error": _redact(str(chat.get("error") or ""), secret) or None,
            "remedy": _redact(str(chat.get("remedy") or ""), secret) or None,
        }
        stored = vectors.get("dimension") if vectors.get("rows") else None
        embed["mismatch_with_store"] = bool(
            embed.get("ok") and stored is not None and embed.get("dimension") != stored
        )
        embed["stored_dimension"] = stored
        embed["provider"] = cand.embed_provider
        return {"chat": chat_out, "embed": embed}

    # PUT -------------------------------------------------------------------------------

    def put(self, body: SettingsIn) -> dict[str, Any]:
        from autoontology import onboarding
        from autoontology.engine.restart import running_restart

        if running_restart(self.running.run_dir) is not None:
            raise ApiError(409, "restart_in_progress", "a restart is running")
        if not self._write_lock.acquire(blocking=False):
            raise ApiError(409, "save_in_progress", "the settings are being saved")
        try:
            pending, origins = pending_settings(self.running)
            cand = candidate_settings(pending, body, test=False)
            writes = planned_writes(cand, body)
            for var, value in list(writes.items()):
                if origins.get(var) != "shell":
                    continue
                if (os.environ.get(var, "").strip() or None) == value:
                    del writes[var]  # the shell already sets this value: nothing to write
                    continue
                raise ApiError(
                    400,
                    "shell_override",
                    f"{var} is set in the shell environment of the service, which wins over "
                    ".env: change it there (or unset it) and restart",
                    key=var,
                )
            if _embedder_changed(pending, cand):
                self._check_dimension(cand, body.confirm_reingest)
            path = env_file_path()
            env_file = onboarding.EnvFile.load(path)
            for var, value in writes.items():
                if value is None:
                    env_file.unset(var)
                else:
                    env_file.set(var, value)
            written = [key for key, _old, _new in env_file.changes()]
            backup = env_file.write(path) if written else None
            if written:
                logger.info("settings: wrote %s to %s", ", ".join(written), path.name)
            new_pending, _ = pending_settings(self.running)
            self.api._status = None
            return {
                "written": written,
                "backup": backup.name if backup else None,
                "restart_required": restart_required(self.running, new_pending),
            }
        finally:
            self._write_lock.release()

    def _check_dimension(self, cand: Settings, confirmed: bool) -> None:
        vectors = vector_state(self.running)
        if not vectors.get("rows") or vectors.get("dimension") is None:
            return
        probe = probe_embedding(cand, self.running)
        if confirmed:
            return
        if not probe.get("ok"):
            raise ApiError(
                409,
                "embedding_check_failed",
                "the new embedding model could not be checked against the stored vectors "
                f"({probe.get('error')}); send confirm_reingest to save anyway",
                stored_dimension=vectors["dimension"],
            )
        if probe["dimension"] != vectors["dimension"]:
            raise ApiError(
                409,
                "embedding_dimension_changed",
                DIMENSION_WARNING.format(new=probe["dimension"], old=vectors["dimension"]),
                dimension=probe["dimension"],
                stored_dimension=vectors["dimension"],
                rows=vectors["rows"],
            )

    # restart ---------------------------------------------------------------------------

    def busy_reason(self) -> str | None:
        active = self.api.runs.busy()
        if active is not None:
            return f"a {active.kind} is running"
        try:
            if self.api.store.active_runs():
                return "a build or export is running"
        except Exception:  # noqa: BLE001 - no store yet: nothing runs
            pass
        playground = getattr(self.app.state, "playground", None)
        if playground is not None and getattr(playground, "_busy", None):
            return "a playground request is running"
        if self.api._provider_test_lock.locked():
            return "a provider test is running"
        return None

    def restart(self) -> dict[str, Any]:
        from autoontology.engine import restart

        if not supervised(self.running):
            raise ApiError(
                409,
                "not_supervised",
                "this service was not started by `uv run autoontology up`, so it cannot "
                "restart itself: stop it and start it again",
            )
        if restart.running_restart(self.running.run_dir) is not None:
            raise ApiError(409, "restart_in_progress", "a restart is already running")
        if reason := self.busy_reason():
            raise ApiError(409, "run_active", f"{reason}: restart when it has finished")
        try:  # held until this process stops: no run may start in between
            self.api.runs.claim_idle(WORKSPACE, "restart")
        except RunStartError as exc:
            raise ApiError(409, "run_active", str(exc)) from exc
        try:
            record = restart.spawn(self.running, previous_pid=os.getpid())
        except Exception as exc:
            self.api.runs.release(WORKSPACE)
            raise ApiError(500, "restart_failed", mask(f"{type(exc).__name__}: {exc}")) from exc
        timer = threading.Timer(RESTART_CLAIM_S, self.api.runs.release, args=(WORKSPACE,))
        timer.daemon = True
        timer.start()
        self._restart_claim = timer
        self._restart_id = record["id"]
        logger.info("settings: restart %s started (service pid %s)", record["id"], os.getpid())
        return {"restart_id": record["id"], "previous_pid": os.getpid()}

    def release_restart_claim(self) -> None:
        if self._restart_claim is not None:
            self._restart_claim.cancel()
            self._restart_claim = None
        self._restart_id = None
        self.api.runs.release(WORKSPACE)

    def restart_status(self, restart_id: str) -> dict[str, Any]:
        from autoontology.engine import restart

        if not restart.RESTART_ID_RE.fullmatch(restart_id):
            raise ApiError(404, "unknown_restart", "no such restart")
        restart.running_restart(self.running.run_dir)  # a dead helper's record -> failed
        record = restart.read_record(self.running.run_dir, restart_id)
        if record is None:
            raise ApiError(404, "unknown_restart", "no such restart")
        if record.get("state") == "failed" and restart_id == self._restart_id:
            self.release_restart_claim()  # this process keeps running: builds may start
        return {
            **{k: mask(v) if isinstance(v, str) else v for k, v in record.items()},
            "log_tail": restart.log_tail(self.running.logs_dir, restart_id),
            "service_pid": os.getpid(),
        }


def supervised(settings: Settings) -> bool:
    """True when this process is the service ``autoontology up`` started (its PID file)."""
    from autoontology.engine.supervisor import Supervisor

    try:
        sup = Supervisor.from_settings(settings)
        return sup.read_pid(sup.spec("service")) == os.getpid()
    except Exception:  # noqa: BLE001 - unknown = not supervised
        return False


def build_router(state: SettingsApi) -> APIRouter:
    router = APIRouter(prefix=f"{PREFIX}/settings")

    @router.get("")
    def get_settings() -> dict[str, Any]:
        return state.get()

    @router.post("/test")
    async def test_settings(body: SettingsIn) -> dict[str, Any]:
        return await state.test(body)

    @router.put("")
    def put_settings(body: SettingsIn) -> dict[str, Any]:
        return state.put(body)

    @router.post("/restart", status_code=202)
    def restart() -> JSONResponse:
        return JSONResponse(state.restart(), status_code=202)

    @router.get("/restart/{restart_id}")
    def restart_status(restart_id: str) -> dict[str, Any]:
        return state.restart_status(restart_id)

    return router


def install(app: FastAPI, api: Api) -> SettingsApi:
    state = SettingsApi(api, app)
    app.state.settings_api = state
    app.include_router(build_router(state))
    return state


__all__ = ["SettingsApi", "SettingsIn", "install", "pending_settings", "supervised"]
