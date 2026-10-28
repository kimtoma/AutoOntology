# SPDX-License-Identifier: MIT
"""AutoOntology settings.

This module must stay importable without importing ``auto_ontology``: upstream reads
its environment at import time, so AutoOntology loads ``.env`` here first and only then
imports the engine.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "auto-ontology"

#: Chat providers. All go through the service's /v1 router (web/providers/router.py):
#: ``ollama`` (local, default), ``openai`` (any OpenAI-compatible endpoint: OpenAI,
#: NVIDIA build.nvidia.com, vLLM, LM Studio, ...), ``claude`` and ``codex`` (the user's
#: logged-in ``claude`` / ``codex`` CLI, i.e. a Claude or ChatGPT subscription).
CHAT_PROVIDERS: tuple[str, ...] = ("ollama", "openai", "claude", "codex")
#: Providers backed by a subscription CLI login (no API key, no embeddings).
CLI_PROVIDERS: tuple[str, ...] = ("claude", "codex")
#: Default chat model per CLI provider (verified with the installed CLIs, 2026-10-28).
CLI_DEFAULT_CHAT_MODELS: dict[str, str] = {"claude": "claude-sonnet-5-5", "codex": "gpt-6-astra"}
#: Model names each CLI provider is known to accept (listed by /v1/models; another valid
#: name still works, doctor warns about it).
CLI_KNOWN_MODELS: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
        "claude-haiku-5-5",
        "sonnet",
        "opus",
        "fable",
        "haiku",
    ),
    "codex": ("gpt-6-astra", "gpt-5.5"),
}
#: Model-name prefix upstream sends for each CLI provider (``openai/<prefix>:<model>``).
#: Codex is "chatgpt", not "codex": langchain_openai sends every model name containing
#: "codex" to the Responses API (/v1/responses), which the router does not serve.
CLI_MODEL_PREFIXES: dict[str, str] = {"claude": "claude", "codex": "chatgpt"}
#: Env var naming each CLI provider's model.
CLI_MODEL_VARS: dict[str, str] = {
    "claude": "AUTOONTOLOGY_CLAUDE_MODEL",
    "codex": "AUTOONTOLOGY_CODEX_MODEL",
}
#: Embedding providers: ``ollama`` goes to Ollama directly, ``openai`` to
#: ``AUTOONTOLOGY_OPENAI_BASE_URL`` directly (no router).
EMBED_PROVIDERS: tuple[str, ...] = ("ollama", "openai")
#: Provider selection keys -> env var. Precedence: shell > .env > defaults.
SELECTION_VARS: dict[str, str] = {
    "chat_provider": "AUTOONTOLOGY_CHAT_PROVIDER",
    "embed_provider": "AUTOONTOLOGY_EMBED_PROVIDER",
}
CHAT_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")
#: Keys a parent process derived for its children (comma list): a child re-derives them
#: from the user's own layers instead of mistaking them for shell values.
DERIVED_ENV_VAR = "AUTOONTOLOGY_DERIVED_ENV"

# Upstream model-endpoint triplets: <PREFIX>_ENDPOINT / _API_KEY / _MODEL.
UPSTREAM_TRIPLET_PREFIXES: tuple[str, ...] = (
    "DEFAULT_MODELS",
    "REASONING",
    "NON_REASONING",
    "EMBED",
    "RERANK",
)
UPSTREAM_TRIPLET_FIELDS: tuple[str, ...] = ("ENDPOINT", "API_KEY", "MODEL")

# Profile "ollama". Upstream builds ChatOpenAI only for model names starting with openai/,
# azure/ or aws/ and passes the name through unchanged (vendor/auto-ontology/auto_ontology/
# utils/llm_invoke.py:228-253); every other name gets ChatNVIDIA, whose fixed 60 s read
# timeout ignores LLM_INVOKE_TIMEOUT_S. So the profile uses an Ollama *alias* literally
# named "openai/qwen3:8b" (built by `autoontology setup`: qwen3:8b with
# num_ctx 16384 and thinking forced off), which Ollama resolves verbatim. Embeddings always
# use the OpenAI client (utils/embedding.py), so "bge-m3" works as-is (1024 dimensions).
OLLAMA_DEFAULT_ENDPOINT = "http://localhost:11434/v1"
OLLAMA_DEFAULT_CHAT_MODEL = "openai/qwen3:8b"
OLLAMA_BASE_CHAT_MODEL = "qwen3:8b"
OLLAMA_DEFAULT_EMBED_MODEL = "bge-m3"
# Ollama ignores the key, but upstream raises when REASONING/NON_REASONING keys are empty.
OLLAMA_API_KEY = "ollama"
# One local Ollama runner serves every call: bound concurrency and give slow local calls
# room. Upstream reads these at import time (utils/llm_invoke.py:48,64;
# semantic/compile.py:30). Applied only when the user has not set them.
OLLAMA_LLM_LIMITS: dict[str, str] = {
    "LLM_INVOKE_TIMEOUT_S": "180",
    "LLM_MAX_INFLIGHT": "2",
    "SEMANTIC_LLM_MAX_INFLIGHT": "1",
}
#: Subscription CLIs: each provider runs at most AUTOONTOLOGY_PROVIDER_MAX_INFLIGHT (2)
#: calls at once in the service, and one request (slot wait + call + schema re-ask) has one
#: deadline, AUTOONTOLOGY_PROVIDER_TIMEOUT_S (default 120 s).
DEFAULT_PROVIDER_TIMEOUT_S = 120.0
CLI_LLM_LIMITS: dict[str, str] = {
    "LLM_MAX_INFLIGHT": "2",
    "SEMANTIC_LLM_MAX_INFLIGHT": "2",
}


def cli_llm_limits(provider_timeout_s: float) -> dict[str, str]:
    """Upstream's per-call timeout sits above the router's whole-request deadline
    (2 x deadline + 30 s), so the router always answers (504 at worst) before upstream
    gives up and retries on top of a call that is still running."""
    return {"LLM_INVOKE_TIMEOUT_S": str(int(2 * provider_timeout_s + 30)), **CLI_LLM_LIMITS}


#: Key upstream sends to the service's /v1 router when chat uses the ``openai`` provider
#: (upstream refuses empty keys; the router replaces it with AUTOONTOLOGY_OPENAI_API_KEY).
ROUTER_API_KEY = "autoontology-router"
#: Default whole-request timeout of the router's ``openai`` passthrough, in seconds.
DEFAULT_OPENAI_TIMEOUT_S = 180.0
# Placeholder shipped in .env.example; treated as "no key".
PLACEHOLDER_KEY_MARKERS: tuple[str, ...] = ("your-api-key-here",)


# The Postgres server is shared with other apps: AutoOntology only ever touches its catalog
# DB and its per-source DBs. engine/pg.py creates only names that pass this regex; the
# catalog name itself must not use the per-source prefix (validate_catalog_database_name).
PG_DATABASE_RE = re.compile(r"^autoontology(_src_[a-z0-9_]+)?$")
#: Maintenance DB used only for server/existence checks (never migrated or written).
PG_MAINTENANCE_DB = "postgres"
#: Postgres truncates identifiers to NAMEDATALEN-1 = 63 bytes, so two longer names could
#: address the same database (a DROP of one would drop the other): refuse them.
PG_IDENTIFIER_MAX_BYTES = 63
#: Longest structured source name: ``autoontology_src_`` (17 bytes) + name <= 63 bytes.
SOURCE_NAME_MAX_LEN = PG_IDENTIFIER_MAX_BYTES - len("autoontology_src_")


def validate_pg_database_name(name: str) -> str:
    """Return ``name`` if it is ``autoontology`` or ``autoontology_src_<a-z0-9_>``; else raise."""
    if not PG_DATABASE_RE.fullmatch(name or ""):
        raise ValueError(
            f"POSTGRES_DATABASE {name!r} is not an AutoOntology database: only 'autoontology' or "
            "'autoontology_src_<lowercase letters, digits, _>' are allowed (the Postgres server "
            "is shared with other apps). Fix POSTGRES_DATABASE in .env or your shell."
        )
    if len(name.encode("utf-8")) > PG_IDENTIFIER_MAX_BYTES:
        raise ValueError(
            f"database name {name!r} is {len(name.encode('utf-8'))} bytes; Postgres truncates "
            f"identifiers to {PG_IDENTIFIER_MAX_BYTES} bytes, so it could address another "
            f"database (source names are limited to {SOURCE_NAME_MAX_LEN} characters)."
        )
    return name


#: Prefix of every per-source database; the catalog database may never carry it.
SOURCE_DB_PREFIX = "autoontology_src_"


def validate_catalog_database_name(name: str) -> str:
    """``validate_pg_database_name`` plus: not a per-source name (``autoontology_src_*``),
    so a files source can never load into, or ``--drop-db``, the catalog database."""
    validate_pg_database_name(name)
    if name.startswith(SOURCE_DB_PREFIX):
        raise ValueError(
            f"POSTGRES_DATABASE {name!r} is reserved for per-source databases "
            f"({SOURCE_DB_PREFIX}<name>); the catalog database must be a different name "
            "(default 'autoontology'). Fix POSTGRES_DATABASE in .env or your shell."
        )
    return name


def assert_source_database(settings: Settings, name: str) -> str:
    """Return *name* if it is a per-source database distinct from the catalog; else raise.

    Called right before every CREATE / load / DROP of a source database."""
    validate_pg_database_name(name)
    if not name.startswith(SOURCE_DB_PREFIX):
        raise ValueError(f"{name!r} is not a per-source database ({SOURCE_DB_PREFIX}<name>)")
    if name == settings.postgres_database:
        raise ValueError(
            f"refusing to touch {name!r}: it is the catalog database (POSTGRES_DATABASE), "
            "not a source database"
        )
    return name


def upstream_model_var_names() -> list[str]:
    return [f"{p}_{f}" for p in UPSTREAM_TRIPLET_PREFIXES for f in UPSTREAM_TRIPLET_FIELDS]


def env_file_path() -> Path:
    """The ``.env`` AutoOntology uses (``AUTOONTOLOGY_ENV_FILE`` or ``<repo>/.env``)."""
    return Path(os.environ.get("AUTOONTOLOGY_ENV_FILE", REPO_ROOT / ".env"))


#: Values this process copied from ``.env`` into ``os.environ`` (to tell them from shell
#: values, for the origin shown by ``doctor``/``status``).
_DOTENV_LOADED: dict[str, str] = {}


def load_env(env_file: Path | None = None, *, override: bool = False) -> Path | None:
    """Load ``.env`` (default: repo root) into ``os.environ``. Returns the path loaded."""
    path = env_file or env_file_path()
    if path.is_file():
        values = {k: v for k, v in dotenv_values(path).items() if v is not None}
        for key, value in values.items():
            if override or key not in os.environ:
                _DOTENV_LOADED[key] = value
        load_dotenv(path, override=override)
        return path
    return None


def derived_env_keys(env: dict[str, str] | None = None) -> set[str]:
    raw = (os.environ if env is None else env).get(DERIVED_ENV_VAR, "")
    return {k for k in raw.split(",") if k}


def dotenv_layer(env_file: Path | None = None) -> dict[str, str]:
    """The ``.env`` file's own values (not what ``os.environ`` ended up with)."""
    path = env_file or env_file_path()
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def shell_value(name: str) -> str | None:
    """``os.environ[name]`` when it came from the shell, not from ``.env`` nor derived by
    a parent AutoOntology process; else None."""
    value = os.environ.get(name)
    if value is None or name in derived_env_keys():
        return None
    if _DOTENV_LOADED.get(name) == value and dotenv_layer().get(name) == value:
        return None
    return value


def user_env(env_file: Path | None = None) -> dict[str, str]:
    """``os.environ`` as the user set it: keys a parent derived are dropped (their ``.env``
    value, if any, is put back)."""
    env = dict(os.environ)
    derived = derived_env_keys(env)
    if not derived:
        return env
    dot = dotenv_layer(env_file)
    for key in derived:
        env.pop(key, None)
        if key in dot:
            env[key] = dot[key]
    env.pop(DERIVED_ENV_VAR, None)
    return env


def resolve_selection(env_file: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """(values, origin) of chat_provider/embed_provider. Precedence: shell env > ``.env`` >
    default (``ollama`` for both)."""
    layers: list[tuple[str, dict[str, str]]] = [
        (
            "shell",
            {
                k: v.strip()
                for k, var in SELECTION_VARS.items()
                if (v := shell_value(var)) and v.strip()
            },
        ),
        (
            ".env",
            {
                k: v.strip()
                for k, var in SELECTION_VARS.items()
                if (v := dotenv_layer(env_file).get(var)) and v.strip()
            },
        ),
    ]
    values: dict[str, str] = {}
    origin: dict[str, str] = {}
    for key, choices in (("chat_provider", CHAT_PROVIDERS), ("embed_provider", EMBED_PROVIDERS)):
        for name, layer in layers:
            if key in layer:
                if layer[key] not in choices:
                    raise ValueError(
                        f"{SELECTION_VARS[key]} must be one of {choices}, got "
                        f"{layer[key]!r} (from {name})"
                    )
                values[key], origin[key] = layer[key], name
                break
        else:
            values[key], origin[key] = "ollama", "default"
    return values, origin


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw!r}")
    return value


DEFAULT_WORKSPACE = "default"
DEFAULT_BASE_IRI = "http://example.com/autoontology#"
WORKSPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def _workspace(raw: str) -> str:
    value = raw.strip() or DEFAULT_WORKSPACE
    if not WORKSPACE_RE.fullmatch(value):
        raise ValueError(
            f"AUTOONTOLOGY_WORKSPACE must be letters, digits, _, . and - (max 63), got {raw!r}"
        )
    return value


def _base_iri(raw: str) -> str:
    value = raw.strip() or DEFAULT_BASE_IRI
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|^`\\]+", value):
        raise ValueError(f"AUTOONTOLOGY_BASE_IRI must be an absolute IRI, got {raw!r}")
    if not value.endswith(("#", "/")):
        value += "#"
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean (1/0, true/false), got {raw!r}")


@dataclass(frozen=True)
class Settings:
    home: Path
    exports_dir: Path = REPO_ROOT / "exports"
    port: int = 7800
    backend_port: int = 3101
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "autoontology"
    postgres_password: str = "autoontology"
    postgres_database: str = "autoontology"
    #: ollama | openai | claude | codex
    chat_provider: str = "ollama"
    #: ollama | openai
    embed_provider: str = "ollama"
    #: Where each selection key came from (shell, .env, default); for status.
    selection_origin: dict[str, str] = field(default_factory=dict)
    #: The user's own upstream triplets (shell/.env); the provider expansion overrides them.
    upstream_models: dict[str, str] = field(default_factory=dict)
    ollama_endpoint: str = OLLAMA_DEFAULT_ENDPOINT
    ollama_chat_model: str = OLLAMA_DEFAULT_CHAT_MODEL
    ollama_embed_model: str = OLLAMA_DEFAULT_EMBED_MODEL
    #: ollama: the /v1 passthrough adds reasoning_effort=none (AUTOONTOLOGY_OLLAMA_NO_THINK).
    ollama_no_think: bool = True
    #: Generic OpenAI-compatible provider (AUTOONTOLOGY_OPENAI_*).
    openai_base_url: str = ""
    openai_api_key: str = field(default="", repr=False)
    openai_chat_model: str = ""
    openai_embed_model: str = ""
    #: Whole-request timeout of the router's openai passthrough (AUTOONTOLOGY_OPENAI_TIMEOUT_S).
    openai_timeout_s: float = DEFAULT_OPENAI_TIMEOUT_S
    #: Subscription CLI providers (AUTOONTOLOGY_CLAUDE_MODEL / AUTOONTOLOGY_CODEX_MODEL).
    claude_model: str = CLI_DEFAULT_CHAT_MODELS["claude"]
    codex_model: str = CLI_DEFAULT_CHAT_MODELS["codex"]
    #: One CLI request's whole deadline (AUTOONTOLOGY_PROVIDER_TIMEOUT_S).
    provider_timeout_s: float = DEFAULT_PROVIDER_TIMEOUT_S
    #: LLM_* limits the user set explicitly (shell or .env); the provider never overrides them.
    llm_limits: dict[str, str] = field(default_factory=dict)
    #: The workspace = the one ontology this home builds (AUTOONTOLOGY_WORKSPACE); its name
    #: is the ontology's name.
    workspace: str = DEFAULT_WORKSPACE
    #: Namespace of every IRI the ontology mints (AUTOONTOLOGY_BASE_IRI).
    base_iri: str = DEFAULT_BASE_IRI
    #: Body rows a CSV/TSV *document* keeps (AUTOONTOLOGY_DOC_TABLE_MAX_ROWS).
    doc_table_max_rows: int = 200
    #: Optional Ollama vision model that captions image documents (AUTOONTOLOGY_VISION_MODEL;
    #: empty = OCR text only).
    vision_model: str = ""
    #: Largest file the native document readers accept (AUTOONTOLOGY_DOC_MAX_BYTES).
    doc_max_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        validate_catalog_database_name(self.postgres_database)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_env(env_file)
        env = user_env(env_file)
        home_raw = env.get("AUTOONTOLOGY_HOME", "").strip()
        home = Path(home_raw).expanduser() if home_raw else REPO_ROOT / "data"
        exports_raw = env.get("AUTOONTOLOGY_EXPORTS_DIR", "").strip()
        selection, origin = resolve_selection(env_file)
        return cls(
            home=home,
            exports_dir=Path(exports_raw).expanduser() if exports_raw else REPO_ROOT / "exports",
            port=_int_env("AUTOONTOLOGY_PORT", 7800),
            backend_port=_int_env("BACKEND_PORT", 3101),
            postgres_host=env.get("POSTGRES_HOST", "localhost"),
            postgres_port=_int_env("POSTGRES_PORT", 5432),
            postgres_user=env.get("POSTGRES_USER", "autoontology"),
            postgres_password=env.get("POSTGRES_PASSWORD", "autoontology"),
            postgres_database=env.get("POSTGRES_DATABASE", "autoontology"),
            chat_provider=selection["chat_provider"],
            embed_provider=selection["embed_provider"],
            selection_origin=origin,
            upstream_models={k: env[k] for k in upstream_model_var_names() if k in env},
            ollama_endpoint=env.get("AUTOONTOLOGY_OLLAMA_ENDPOINT", "").strip()
            or OLLAMA_DEFAULT_ENDPOINT,
            ollama_chat_model=env.get("AUTOONTOLOGY_OLLAMA_CHAT_MODEL", "").strip()
            or OLLAMA_DEFAULT_CHAT_MODEL,
            ollama_embed_model=env.get("AUTOONTOLOGY_OLLAMA_EMBED_MODEL", "").strip()
            or OLLAMA_DEFAULT_EMBED_MODEL,
            ollama_no_think=_bool_env("AUTOONTOLOGY_OLLAMA_NO_THINK", True),
            openai_base_url=env.get("AUTOONTOLOGY_OPENAI_BASE_URL", "").strip().rstrip("/"),
            openai_api_key=env.get("AUTOONTOLOGY_OPENAI_API_KEY", "").strip(),
            openai_chat_model=env.get("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "").strip(),
            openai_embed_model=env.get("AUTOONTOLOGY_OPENAI_EMBED_MODEL", "").strip(),
            openai_timeout_s=_float_env("AUTOONTOLOGY_OPENAI_TIMEOUT_S", DEFAULT_OPENAI_TIMEOUT_S),
            claude_model=env.get("AUTOONTOLOGY_CLAUDE_MODEL", "").strip()
            or CLI_DEFAULT_CHAT_MODELS["claude"],
            codex_model=env.get("AUTOONTOLOGY_CODEX_MODEL", "").strip()
            or CLI_DEFAULT_CHAT_MODELS["codex"],
            provider_timeout_s=_float_env(
                "AUTOONTOLOGY_PROVIDER_TIMEOUT_S", DEFAULT_PROVIDER_TIMEOUT_S
            ),
            llm_limits={k: env[k] for k in OLLAMA_LLM_LIMITS if env.get(k, "").strip()},
            workspace=_workspace(env.get("AUTOONTOLOGY_WORKSPACE", "")),
            base_iri=_base_iri(env.get("AUTOONTOLOGY_BASE_IRI", "")),
            doc_table_max_rows=max(1, _int_env("AUTOONTOLOGY_DOC_TABLE_MAX_ROWS", 200)),
            vision_model=env.get("AUTOONTOLOGY_VISION_MODEL", "").strip(),
            doc_max_bytes=max(1, _int_env("AUTOONTOLOGY_DOC_MAX_BYTES", 50 * 1024 * 1024)),
        )

    @property
    def model_profile(self) -> str:
        """Short name of the provider pair: ``ollama``/``openai`` when both roles agree, else
        ``<chat>+<embed>`` (e.g. ``openai+ollama``)."""
        if self.chat_provider == self.embed_provider:
            return self.chat_provider
        return f"{self.chat_provider}+{self.embed_provider}"

    @property
    def effective_chat_model(self) -> str:
        """The model the chat provider runs."""
        if self.chat_provider == "openai":
            return self.openai_chat_model
        if self.chat_provider in CLI_PROVIDERS:
            return self.cli_model(self.chat_provider)
        return self.ollama_chat_model

    def cli_model(self, provider: str) -> str:
        """The model a CLI provider (``claude`` / ``codex``) runs."""
        return self.claude_model if provider == "claude" else self.codex_model

    @property
    def effective_embed_model(self) -> str:
        if self.embed_provider == "openai":
            return self.openai_embed_model
        return self.ollama_embed_model

    @property
    def upstream_chat_model(self) -> str:
        """The model name upstream sends to the router. It must start with ``openai/`` so
        upstream uses its ChatOpenAI client; the Ollama alias already does, the openai
        provider's model always gets one more prefix (the router strips exactly one), so a
        real id like ``openai/gpt-oss-120b`` reaches the endpoint unchanged."""
        model = self.effective_chat_model
        if self.chat_provider == "openai":
            return f"openai/{model}"
        if self.chat_provider in CLI_PROVIDERS:
            # the router dispatches by this prefix (web/providers/router.py:split_model)
            return f"openai/{CLI_MODEL_PREFIXES[self.chat_provider]}:{model}"
        return model

    @property
    def backend_url(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    @property
    def service_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def run_dir(self) -> Path:
        return self.home / "run"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def llm_proxy_endpoint(self) -> str:
        """The service's OpenAI-compatible /v1 router (upstream's chat endpoint)."""
        return f"{self.service_url}/v1"

    @property
    def ollama_root_url(self) -> str:
        """Ollama native API root (the endpoint without its ``/v1`` suffix)."""
        return self.ollama_endpoint.rstrip("/").removesuffix("/v1")


def postgres_env(settings: Settings, database: str | None = None) -> dict[str, str]:
    """``POSTGRES_*`` env for upstream. Both USER and PASSWORD must exist (os.environ[...])."""
    return {
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password,
        "POSTGRES_DATABASE": validate_pg_database_name(database or settings.postgres_database),
    }


CHAT_TRIPLET_PREFIXES: tuple[str, ...] = ("DEFAULT_MODELS", "REASONING", "NON_REASONING")


def expand_model_profile(settings: Settings) -> dict[str, str]:
    """Upstream ``<PREFIX>_{ENDPOINT,API_KEY,MODEL}`` env for the selected providers.

    * chat (both providers): DEFAULT_MODELS/REASONING/NON_REASONING point at the service's
      /v1 router with model ``openai/qwen3:8b`` (ollama alias) or ``openai/<chat model>``
      (openai provider; the router strips the prefix and adds the bearer key). RERANK
      points at the router too: neither backend serves reranking, and upstream's only
      caller (semantic/bridge_tables.py) falls back to the first Term on error.
    * embed ``ollama``: EMBED points at Ollama (``bge-m3``); ``openai``: at
      ``AUTOONTOLOGY_OPENAI_BASE_URL`` with the API key and ``AUTOONTOLOGY_OPENAI_EMBED_MODEL``.

    * chat ``claude`` / ``codex``: the same router with model ``openai/claude:<m>`` /
      ``openai/chatgpt:<m>`` (the router runs the user's logged-in CLI).

    Plus the provider's LLM_* limits (Ollama's, or ``cli_llm_limits`` for the CLI
    providers) for any limit the user has not set.
    """
    key = OLLAMA_API_KEY if settings.chat_provider == "ollama" else ROUTER_API_KEY
    env: dict[str, str] = {}
    for prefix in (*CHAT_TRIPLET_PREFIXES, "RERANK"):
        env[f"{prefix}_ENDPOINT"] = settings.llm_proxy_endpoint
        env[f"{prefix}_API_KEY"] = key
        env[f"{prefix}_MODEL"] = settings.upstream_chat_model
    if settings.embed_provider == "openai":
        env["EMBED_ENDPOINT"] = settings.openai_base_url
        env["EMBED_API_KEY"] = settings.openai_api_key
        env["EMBED_MODEL"] = settings.openai_embed_model
    else:
        env["EMBED_ENDPOINT"] = settings.ollama_endpoint
        env["EMBED_API_KEY"] = OLLAMA_API_KEY
        env["EMBED_MODEL"] = settings.ollama_embed_model
    if settings.chat_provider == "ollama":
        limits = OLLAMA_LLM_LIMITS
    elif settings.chat_provider in CLI_PROVIDERS:
        limits = cli_llm_limits(settings.provider_timeout_s)
    else:
        limits = {}
    for name, value in limits.items():
        if name not in settings.llm_limits:
            env[name] = value
    return env


def _is_placeholder(value: str) -> bool:
    return any(marker in value for marker in PLACEHOLDER_KEY_MARKERS)


def resolve_triplet_field(models: dict[str, str], prefix: str, field_name: str) -> str:
    """Mirror of upstream ``model_config.resolve`` without built-in defaults."""
    return models.get(f"{prefix}_{field_name}", "") or models.get(
        f"DEFAULT_MODELS_{field_name}", ""
    )


def model_profile_warnings(settings: Settings) -> list[str]:
    """Configurations that run but probably not as intended (doctor shows them as WARN)."""
    warnings: list[str] = []
    model = settings.ollama_chat_model
    if settings.chat_provider == "ollama" and not model.startswith("openai/"):
        warnings.append(
            f"AUTOONTOLOGY_OLLAMA_CHAT_MODEL {model!r} lacks the openai/ prefix: upstream then "
            "uses its ChatNVIDIA client (60 s timeout) instead of ChatOpenAI; name an Ollama "
            "alias such as openai/qwen3:8b"
        )
    if settings.chat_provider in CLI_PROVIDERS:
        model = settings.cli_model(settings.chat_provider)
        known = CLI_KNOWN_MODELS[settings.chat_provider]
        if model not in known:
            warnings.append(
                f"{CLI_MODEL_VARS[settings.chat_provider]} {model!r} is not one of the "
                f"models known to work ({', '.join(known)})"
            )
    return warnings


#: Values codex accepts for ``model_reasoning_effort`` (AUTOONTOLOGY_CODEX_REASONING_EFFORT).
CODEX_REASONING_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")


def codex_reasoning_effort() -> str:
    """AUTOONTOLOGY_CODEX_REASONING_EFFORT as set ("" = codex's default)."""
    return os.environ.get("AUTOONTOLOGY_CODEX_REASONING_EFFORT", "").strip()


def validate_model_profile(settings: Settings) -> list[str]:
    """Problems with the selected providers' configuration (empty list = valid)."""
    problems: list[str] = []
    uses_openai = "openai" in (settings.chat_provider, settings.embed_provider)
    if uses_openai:
        if not settings.openai_base_url:
            problems.append("AUTOONTOLOGY_OPENAI_BASE_URL is not set")
        if not settings.openai_api_key:
            problems.append("AUTOONTOLOGY_OPENAI_API_KEY is not set")
        elif _is_placeholder(settings.openai_api_key):
            problems.append("AUTOONTOLOGY_OPENAI_API_KEY is still the .env.example placeholder")
    if settings.chat_provider == "openai":
        if not settings.openai_chat_model:
            problems.append("AUTOONTOLOGY_OPENAI_CHAT_MODEL is not set")
        elif not CHAT_MODEL_RE.fullmatch(settings.openai_chat_model):
            problems.append(
                f"AUTOONTOLOGY_OPENAI_CHAT_MODEL {settings.openai_chat_model!r} "
                "is not a valid model name"
            )
    if settings.embed_provider == "openai" and not settings.openai_embed_model:
        problems.append("AUTOONTOLOGY_OPENAI_EMBED_MODEL is not set")
    if settings.chat_provider in CLI_PROVIDERS:
        model = settings.cli_model(settings.chat_provider)
        if not CHAT_MODEL_RE.fullmatch(model):
            problems.append(
                f"{CLI_MODEL_VARS[settings.chat_provider]} {model!r} is not a valid model name"
            )
    effort = codex_reasoning_effort()
    if settings.chat_provider == "codex" and effort and effort not in CODEX_REASONING_EFFORTS:
        problems.append(
            f"AUTOONTOLOGY_CODEX_REASONING_EFFORT {effort[:40]!r} is not one of "
            + ", ".join(CODEX_REASONING_EFFORTS)
        )
    return problems
