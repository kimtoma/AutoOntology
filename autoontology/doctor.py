"""``autoontology doctor``: environment checks with remedies."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from autoontology.config import (
    PG_MAINTENANCE_DB,
    Settings,
    env_file_path,
    expand_model_profile,
    resolve_triplet_field,
    validate_model_profile,
)

Status = Literal["OK", "WARN", "FAIL", "SKIP"]
MODEL_CHECK_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""
    remedy: str = ""


def format_checks(checks: list[Check]) -> str:
    header = ("CHECK", "STATUS", "DETAIL", "REMEDY")
    rows = [(c.name, c.status, c.detail, c.remedy) for c in checks]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(3)]
    lines = []
    for row in [header, *rows]:
        cells = [row[i].ljust(widths[i]) for i in range(3)] + [row[3]]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def _guard(name: str, fn: Callable[[], Check], remedy: str = "") -> Check:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a check must never crash doctor
        return Check(
            name, "FAIL", f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}", remedy
        )


def _with_timeout(name: str, fn: Callable[[], Check], timeout: float, remedy: str) -> Check:
    box: dict[str, Check] = {}
    thread = threading.Thread(
        target=lambda: box.setdefault("r", _guard(name, fn, remedy)), daemon=True
    )
    thread.start()
    thread.join(timeout)
    return box.get("r") or Check(name, "FAIL", f"no answer within {timeout:.0f}s", remedy)


# --- individual checks -----------------------------------------------------------


def check_env_file() -> Check:
    path = env_file_path()
    if path.is_file():
        return Check(".env", "OK", str(path))
    return Check(
        ".env",
        "WARN",
        f"{path} missing: using the built-in defaults",
        "cp .env.example .env (optional)",
    )


def check_postgres(settings: Settings) -> list[Check]:
    from autoontology.engine import pg

    target = f"{settings.postgres_user}@{settings.postgres_host}:{settings.postgres_port}"
    try:
        with pg.connect(settings, PG_MAINTENANCE_DB) as conn:
            version = conn.execute("show server_version").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return [
            Check(
                "postgres",
                "FAIL",
                f"{target}: {type(exc).__name__}",
                "start Postgres, e.g. `docker compose up -d`",
            )
        ]
    out = [Check("postgres", "OK", f"{target} (server {version})")]
    db = settings.postgres_database
    setup = "uv run autoontology setup"

    def database() -> Check:
        if pg.database_exists(settings):
            return Check(f"database {db}", "OK", "exists")
        return Check(f"database {db}", "FAIL", "missing", setup)

    db_check = _guard(f"database {db}", database, setup)
    out.append(db_check)
    if db_check.status != "OK":
        return out

    def vector() -> Check:
        version = pg.extension_version(settings, "vector")
        if version:
            return Check("extension vector", "OK", f"version {version}")
        return Check(
            "extension vector",
            "FAIL",
            "not installed",
            f"{setup} (the server needs pgvector, e.g. the docker-compose.yml image)",
        )

    out.append(_guard("extension vector", vector, setup))
    out.append(check_migrations(settings))
    return out


def check_migrations(settings: Settings) -> Check:
    from autoontology.engine.migrate import current_revision, head_revision

    def run() -> Check:
        head = head_revision()
        current = current_revision(settings)
        if head and current == head:
            return Check("alembic", "OK", f"at head {head}")
        return Check(
            "alembic",
            "WARN",
            f"current {current or 'none'}, head {head}",
            "uv run autoontology setup",
        )

    return _guard("alembic", run, "uv run autoontology setup")


def check_ports(settings: Settings) -> list[Check]:
    from autoontology.engine.supervisor import Supervisor, listening_pids, port_in_use

    sup = Supervisor.from_settings(settings)
    owned = sup.owned_pids()
    out = []
    for label, port in (("backend", settings.backend_port), ("service", settings.port)):
        name = f"port {port} ({label})"
        if not port_in_use(port):
            out.append(Check(name, "OK", "free"))
            continue
        holders = listening_pids(port)
        ours = [p for p in holders if p in owned or _pgid(p) in owned]
        if holders and len(ours) == len(holders):
            out.append(
                Check(name, "OK", f"owned by AutoOntology (pid {', '.join(map(str, ours))})")
            )
        else:
            out.append(
                Check(
                    name,
                    "FAIL",
                    f"in use by pid {holders or 'unknown'}",
                    "stop that process or change the port in .env",
                )
            )
    return out


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def check_node() -> Check:
    from autoontology.unstructured import kordoc

    try:
        node, version = kordoc.require_node()
    except kordoc.KordocUnavailable as exc:
        return Check(
            "node",
            "WARN",
            exc.message,
            "install Node.js 20+ (only needed for HWP/PDF/DOCX/PPTX/XLSX documents)",
        )
    return Check("node", "OK", f"v{'.'.join(map(str, version))} ({node})")


def check_kordoc(settings: Settings) -> Check:
    """The document engine. Optional: without it MD/TXT/HTML still extract."""
    from autoontology.unstructured import kordoc

    remedy = kordoc.INSTALL_COMMAND
    try:
        engine = kordoc.resolve_engine(settings.home)
        version = kordoc.engine_version(engine)
    except kordoc.KordocError as exc:
        return Check(
            "kordoc",
            "WARN",
            f"{exc.message}; HWP/HWPX/PDF/DOCX/PPTX/XLSX inputs fail (MD/TXT/HTML work)",
            remedy,
        )
    detail = f"{version} at {engine.cli} (from {engine.origin})"
    recorded = kordoc.read_version_file(settings.home) if engine.origin == "home" else None
    if recorded and recorded["omit_optional"]:
        return Check(
            "kordoc",
            "WARN",
            detail + "; installed with --omit-optional: PDF/OCR unavailable",
            f"{remedy} --force",
        )
    return Check("kordoc", "OK", detail)


def check_profile(settings: Settings) -> Check:
    problems = validate_model_profile(settings)
    if problems:
        return Check(
            f"model profile {settings.model_profile}",
            "FAIL",
            "; ".join(problems[:3]),
            "set the AUTOONTOLOGY_OPENAI_* values in .env (see .env.example)",
        )
    models = expand_model_profile(settings)
    chat = resolve_triplet_field(models, "REASONING", "MODEL") or "(upstream default)"
    embed = resolve_triplet_field(models, "EMBED", "MODEL") or "(upstream default)"
    return Check(f"model profile {settings.model_profile}", "OK", f"chat {chat}, embed {embed}")


SETUP_OLLAMA = "uv run autoontology setup"


def check_chat_provider(settings: Settings) -> Check:
    """Which chat provider/model is selected and where upstream sends chat calls."""
    chat = settings.chat_provider
    origin = settings.selection_origin.get("chat_provider", "?")
    target = (
        settings.ollama_endpoint
        if chat == "ollama"
        else settings.openai_base_url or "(AUTOONTOLOGY_OPENAI_BASE_URL unset)"
    )
    return Check(
        "chat provider",
        "OK",
        f"{chat} {settings.effective_chat_model or '(unset)'} via "
        f"{settings.llm_proxy_endpoint} -> {target} (from {origin})",
    )


def check_embed_provider(settings: Settings) -> Check:
    models = expand_model_profile(settings)
    model = resolve_triplet_field(models, "EMBED", "MODEL") or "(upstream default)"
    endpoint = resolve_triplet_field(models, "EMBED", "ENDPOINT") or "(upstream default)"
    origin = settings.selection_origin.get("embed_provider", "?")
    return Check(
        "embed provider", "OK", f"{settings.embed_provider} {model} at {endpoint} (from {origin})"
    )


def check_ollama(settings: Settings) -> Check:
    from autoontology.engine.ollama import has_model

    url = f"{settings.ollama_root_url}/api/tags"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return Check(
            "ollama", "FAIL", f"{url}: {type(exc).__name__}", "start Ollama (`ollama serve`)"
        )
    names = {m.get("name", "") for m in resp.json().get("models", [])}
    wanted = [
        *([settings.ollama_chat_model] if settings.chat_provider == "ollama" else []),
        *([settings.ollama_embed_model] if settings.embed_provider == "ollama" else []),
    ]
    missing = [m for m in wanted if not has_model(names, m)]
    if missing:
        return Check("ollama", "FAIL", f"models missing: {', '.join(missing)}", SETUP_OLLAMA)
    return Check("ollama", "OK", f"{settings.ollama_root_url} has {', '.join(wanted)}")


def check_chat_route(settings: Settings) -> Check:
    """Where the backend sends chat calls with the ollama provider, and whether that is up."""
    url = settings.llm_proxy_endpoint
    try:
        up = httpx.get(f"{settings.service_url}/health/live", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        up = False
    state = "service up" if up else "service down (started by `autoontology up`)"
    return Check(
        "chat route",
        "OK",
        f"backend/build chat -> {url} (adds reasoning_effort=none: "
        f"{'on' if settings.ollama_no_think else 'OFF'}) -> "
        f"{settings.ollama_endpoint}; {state}",
    )


def check_ollama_chat(settings: Settings) -> Check:
    """Structured chat straight to Ollama with the body the /v1 passthrough would send
    (works with the services down)."""
    from autoontology.engine import ollama

    name = "chat endpoint"

    def run() -> Check:
        parsed, content, tokens = ollama.structured_probe(
            settings.ollama_endpoint, settings.ollama_chat_model, no_think=settings.ollama_no_think
        )
        detail = (
            f"{settings.ollama_chat_model} json_schema via proxy payload: "
            f"{content[:40]!r}, completion_tokens {tokens}"
        )
        if parsed is None:
            return Check(
                name,
                "FAIL",
                detail + " (not bare JSON: schema not enforced)",
                "keep AUTOONTOLOGY_OLLAMA_NO_THINK on / upgrade-check Ollama",
            )
        return Check(name, "OK", detail)

    return _with_timeout(name, run, MODEL_CHECK_TIMEOUT_S, "start Ollama (`ollama serve`)")


def check_no_think(settings: Settings) -> Check:
    """The alias itself (no injected field) must not think: the safety net when a caller
    bypasses the /v1 passthrough (see engine/ollama.py)."""
    from autoontology.engine import ollama

    name = "chat no-think"

    def run() -> Check:
        answer, tokens = ollama.chat_probe(settings.ollama_endpoint, settings.ollama_chat_model)
        detail = f"{settings.ollama_chat_model}: completion_tokens {tokens}, answer {answer[:20]!r}"
        if 0 <= tokens <= ollama.MAX_PROBE_COMPLETION_TOKENS:
            return Check(name, "OK", detail)
        return Check(
            name,
            "FAIL",
            detail + f" (max {ollama.MAX_PROBE_COMPLETION_TOKENS})",
            f"{SETUP_OLLAMA} --force",
        )

    return _with_timeout(name, run, MODEL_CHECK_TIMEOUT_S, f"{SETUP_OLLAMA} --force")


def check_vector_tables(settings: Settings) -> Check:
    """pgvector table dimension vs what the embedder emits (ingest fails on a mismatch)."""
    from autoontology.engine import jobs

    name = "vector tables"

    def run() -> Check:
        dim = jobs.embedding_dimension(settings)
        state = jobs.vector_table_state(settings)
        parts, status, remedy = [], "OK", ""
        for collection, found in state.items():
            if found is None:
                parts.append(f"{collection} absent")
                continue
            current, count = found
            parts.append(f"{collection} {current} ({count} rows)")
            if current.endswith(f"({dim})"):
                continue
            if count:
                status = "FAIL"
                remedy = (
                    "embedder changed after ingest: restore the old EMBED model, or "
                    "`autoontology source rm` every source and empty the vdb tables"
                )
            elif status == "OK":
                status = "WARN"
                remedy = "none needed: the next build recreates the empty table"
        return Check(name, status, f"embedder {dim}; " + ", ".join(parts), remedy)

    return _with_timeout(name, run, MODEL_CHECK_TIMEOUT_S, "check EMBED_* and Postgres")


def check_openai_chat(settings: Settings) -> Check:
    """One structured call to the OpenAI-compatible endpoint, in-process through the same
    passthrough the service's /v1 router uses (works with the services down)."""
    import asyncio

    from autoontology.engine.ollama import STRUCTURED_PROBE_PROMPT, STRUCTURED_PROBE_SCHEMA
    from autoontology.web.providers.openai import OpenAIProvider
    from autoontology.web.providers.openai_compat import (
        ChatMessage,
        ChatRequest,
        ProviderError,
    )

    name = "chat endpoint"
    remedy = "check AUTOONTOLOGY_OPENAI_BASE_URL/API_KEY/CHAT_MODEL in .env"

    def run() -> Check:
        model = settings.upstream_chat_model
        request = ChatRequest(
            model=model,
            messages=[ChatMessage("user", STRUCTURED_PROBE_PROMPT)],
            schema=STRUCTURED_PROBE_SCHEMA,
            schema_name="Ping",
            mode="json_schema",
            max_tokens=256,
        )
        try:
            resp = asyncio.run(OpenAIProvider(settings).complete(request))
        except ProviderError as exc:
            return Check(name, "FAIL", f"{model}: {exc.status} {exc.message}", remedy)
        return Check(
            name,
            "OK",
            f"{model}: {resp.content[:40]!r} in {resp.duration_s:.1f}s, "
            f"tokens {resp.prompt_tokens}/{resp.completion_tokens}",
        )

    return _with_timeout(name, run, MODEL_CHECK_TIMEOUT_S, remedy)


def check_embed(settings: Settings) -> Check:
    from autoontology.engine.jobs import apply_model_env

    def run() -> Check:
        apply_model_env(settings)
        from auto_ontology.utils.embedding import embed_texts, get_embed_params

        params = get_embed_params()
        vectors = embed_texts(["ping"], params, "query")
        if not vectors or not vectors[0]:
            return Check("embedding endpoint", "FAIL", "empty response", "check EMBED_* in .env")
        return Check("embedding endpoint", "OK", f"{params.model_name}: dim {len(vectors[0])}")

    return _with_timeout(
        "embedding endpoint",
        run,
        MODEL_CHECK_TIMEOUT_S,
        "check EMBED_* in .env / that the model server is up",
    )


def run_checks(settings: Settings, *, skip_models: bool = False) -> list[Check]:
    checks = [check_env_file()]
    checks += check_postgres(settings)
    checks += check_ports(settings)
    checks.append(_guard("node", check_node))
    checks.append(
        _guard("kordoc", lambda: check_kordoc(settings), "uv run autoontology tools install-kordoc")
    )
    profile = check_profile(settings)
    checks.append(profile)
    provider = _guard("chat provider", lambda: check_chat_provider(settings))
    checks.append(provider)
    checks.append(_guard("embed provider", lambda: check_embed_provider(settings)))
    blocked = profile.status == "FAIL" or provider.status == "FAIL"
    if "ollama" in (settings.chat_provider, settings.embed_provider):
        ollama = check_ollama(settings)
        checks.append(ollama)
        blocked = blocked or ollama.status == "FAIL"
    ollama = settings.chat_provider == "ollama"
    names = [
        *(["chat route"] if ollama else []),
        "chat endpoint",
        *(["chat no-think"] if ollama else []),
        "embedding endpoint",
        "vector tables",
    ]
    if skip_models or blocked:
        reason = "--skip-models" if skip_models else "model provider/server not ready"
        checks += [Check(n, "SKIP", reason) for n in names]
        return checks
    if ollama:
        checks.append(check_chat_route(settings))
        checks.append(check_ollama_chat(settings))
        checks.append(check_no_think(settings))
    else:
        checks.append(check_openai_chat(settings))
    embed = check_embed(settings)
    checks.append(embed)
    db_ok = all(c.status == "OK" for c in checks if c.name.startswith(("postgres", "database")))
    if embed.status != "OK" or not db_ok:
        checks.append(Check("vector tables", "SKIP", "embedding endpoint/database not ready"))
    else:
        checks.append(check_vector_tables(settings))
    return checks
