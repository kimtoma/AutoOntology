# SPDX-License-Identifier: MIT
"""Onboarding helpers behind ``autoontology init``, ``demo`` and ``provider``.

Pure functions, no prompts: what is installed and logged in (:func:`detect_environment`,
built from the ``doctor`` checks and the providers' own status), which chat provider to
suggest (:func:`recommend_provider`), an ``.env`` editor that keeps every other line as
it is (:class:`EnvFile`), and the sample sets (``samples/<set>/{structured,documents,
images}``) that ``demo`` and ``POST /api/samples/load`` register (:func:`register_sample_set`).
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from autoontology.config import CHAT_PROVIDERS, CLI_MODEL_VARS, CLI_PROVIDERS, Settings

#: Env var naming the chat model of each provider.
CHAT_MODEL_VARS: dict[str, str] = {
    "ollama": "AUTOONTOLOGY_OLLAMA_CHAT_MODEL",
    "openai": "AUTOONTOLOGY_OPENAI_CHAT_MODEL",
    **CLI_MODEL_VARS,
}
#: Login command per CLI provider (what the user types).
LOGIN_COMMANDS: dict[str, str] = {"claude": "claude auth login", "codex": "codex login"}
#: Comment line above the keys ``init`` appends (keys found elsewhere are edited in place).
INIT_BLOCK = "# --- Set by autoontology init ---"
RESTART_HINT = "uv run autoontology down && uv run autoontology up"

# --- detection ---------------------------------------------------------------------------


@dataclass
class ProviderInfo:
    name: str
    #: The endpoint answers (ollama), the base URL is set (openai), the CLI binary exists.
    found: bool
    #: Credentials accepted (CLI login; the openai key; always True for a reachable Ollama).
    logged_in: bool
    note: str = ""
    remedy: str = ""

    @property
    def ready(self) -> bool:
        return self.found and self.logged_in


@dataclass
class Detection:
    postgres_ok: bool
    postgres_detail: str
    docker: bool
    providers: dict[str, ProviderInfo]
    #: The configured Ollama chat/embedding models are pulled.
    ollama_models: bool
    node_ok: bool
    node_detail: str
    kordoc: bool
    ocr_ready: bool
    env_exists: bool
    env_path: Path
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def ollama_reachable(self) -> bool:
        info = self.providers.get("ollama")
        return bool(info and info.found)


def provider_info(settings: Settings, name: str) -> ProviderInfo:
    """Status of chat provider *name* (whatever provider ``settings`` selects), from the
    provider's own status check (the same one ``doctor`` and ``/api/status`` show)."""
    from autoontology.web.providers.router import Registry, cli_provider

    probe = dataclasses.replace(settings, chat_provider=name)
    try:
        st = Registry(probe).status(refresh=True)
        found = st.available
        if name in CLI_PROVIDERS:
            found = cli_provider(probe, name).binary() is not None
        return ProviderInfo(name, found, st.logged_in, st.detail, st.remedy)
    except Exception as exc:  # noqa: BLE001 - a status check must never crash init
        remedy = (
            f"install the {name} CLI, then `{LOGIN_COMMANDS[name]}`"
            if name in CLI_PROVIDERS
            else "uv run autoontology doctor"
        )
        return ProviderInfo(name, False, False, f"status failed: {type(exc).__name__}", remedy)


def detect_environment(settings: Settings, env_path: Path | None = None) -> Detection:
    """What is installed, reachable and logged in, using the ``doctor`` checks."""
    from autoontology import doctor
    from autoontology.config import env_file_path

    pg = doctor._guard("postgres", lambda: doctor.check_postgres(settings)[0])
    providers = {name: provider_info(settings, name) for name in CHAT_PROVIDERS}
    ollama = providers["ollama"]
    node = doctor._guard("node", doctor.check_node)
    kordoc = doctor._guard("kordoc", lambda: doctor.check_kordoc(settings))
    ocr = doctor._guard("ocr models", lambda: doctor.check_ocr_models(settings))
    path = env_path or env_file_path()
    return Detection(
        postgres_ok=pg.status == "OK",
        postgres_detail=pg.detail,
        docker=shutil.which("docker") is not None,
        providers=providers,
        ollama_models=ollama.found and not ollama.remedy,
        node_ok=node.status == "OK",
        node_detail=node.detail,
        kordoc=kordoc.status == "OK",
        ocr_ready=ocr.status == "OK",
        env_exists=path.is_file(),
        env_path=path,
        notes={"kordoc": kordoc.detail, "ocr": ocr.detail, "ollama": ollama.note},
    )


def recommend_provider(detection: Detection) -> str:
    """claude if logged in, then codex if logged in, then ollama (reachable or not: it is
    the local default and needs no account)."""
    for name in CLI_PROVIDERS:
        info = detection.providers.get(name)
        if info and info.ready:
            return name
    return "ollama"


# --- .env editor ------------------------------------------------------------------------

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BARE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@+,=~%-]*$")
_EXPORT_RE = re.compile(r"^\s*export\s+")


def _active_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")


def _commented_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*#\s*(?:export\s+)?{re.escape(key)}\s*=")


def _format_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("a .env value must be a single line")
    if _BARE_VALUE_RE.fullmatch(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse(text: str) -> dict[str, str]:
    return {k: v for k, v in dotenv_values(stream=io.StringIO(text)).items() if v is not None}


def _newline(line: str) -> str:
    return line[len(line.rstrip("\r\n")) :] or "\n"


class EnvFile:
    """An ``.env`` file edited line by line: ``set`` replaces the key's active line (or
    its commented ``# KEY=`` line) in place, else appends it under :data:`INIT_BLOCK`;
    every other line, comment and blank stays byte-for-byte."""

    def __init__(self, text: str = "", path: Path | None = None, *, existed: bool = False):
        self.path = path
        self.existed = existed
        self.original = text
        self.lines: list[str] = text.splitlines(keepends=True)
        #: Values ``changes()`` compares against: the file's own (none for a new file).
        self.baseline: dict[str, str] = _parse(text) if existed else {}

    @classmethod
    def load(cls, path: Path, template: Path | None = None) -> EnvFile:
        """*path*'s content; a missing file starts from *template* (``.env.example``)
        when given and present, else empty."""
        path = Path(path)
        if path.is_file():
            return cls(path.read_text(encoding="utf-8"), path, existed=True)
        if template is not None and Path(template).is_file():
            text = Path(template).read_text(encoding="utf-8")
            return cls(text, path, existed=False)
        return cls("", path, existed=False)

    # --- queries -------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        return _parse(self.render()).get(key)

    def values(self) -> dict[str, str]:
        return _parse(self.render())

    def changes(self) -> list[tuple[str, str | None, str | None]]:
        """``(key, old, new)`` for every key whose value differs from the loaded file."""
        before = self.baseline
        after = self.values()
        keys = list(dict.fromkeys([*before, *after]))
        return [(k, before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)]

    def render(self) -> str:
        return "".join(self.lines)

    # --- edits ---------------------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid .env key {key!r}")
        new = f"{key}={_format_value(str(value))}"
        active = _active_re(key)
        hits = [i for i, line in enumerate(self.lines) if active.match(line)]
        if not hits:
            commented = _commented_re(key)
            hits = [i for i, line in enumerate(self.lines) if commented.match(line)][:1]
        if hits and all(_parse(self.lines[i]).get(key) == str(value) for i in hits):
            return  # already set: the line stays byte-for-byte
        if hits:
            for i in hits:
                line = self.lines[i]
                keep = "export " if _EXPORT_RE.match(line) and active.match(line) else ""
                self.lines[i] = keep + new + _newline(line)
            return
        self._append(new + "\n")

    def unset(self, key: str) -> None:
        active = _active_re(key)
        self.lines = [line for line in self.lines if not active.match(line)]

    def _append(self, line: str) -> None:
        if self.lines and not self.lines[-1].endswith(("\n", "\r")):
            self.lines[-1] += "\n"
        block = next((i for i, x in enumerate(self.lines) if x.rstrip("\r\n") == INIT_BLOCK), None)
        if block is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("\n")
            self.lines += [INIT_BLOCK + "\n", line]
            return
        end = block + 1
        while end < len(self.lines) and self.lines[end].strip():
            end += 1
        self.lines.insert(end, line)

    # --- write ---------------------------------------------------------------------

    def write(self, path: Path | None = None) -> Path | None:
        """Write atomically (temp file + rename, mode 0600 or the file's own). When the file
        existed, it is first copied to ``<name>.bak-<timestamp>``; that path is returned.
        Nothing is written when the content did not change."""
        target = Path(path or self.path or ".env")
        text = self.render()
        backup: Path | None = None
        mode = 0o600
        if target.is_file():
            if target.read_text(encoding="utf-8") == text:
                return None
            # the file's own mode, but never more than owner read/write: .env holds keys
            mode = target.stat().st_mode & 0o777 & 0o600
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.name}.bak-{stamp}")
            n = 1
            while backup.exists():
                n += 1
                backup = target.with_name(f"{target.name}.bak-{stamp}-{n}")
            _private_copy(target, backup, scrub=not _keep_backup_secrets())
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.path, self.existed, self.original = target, True, text
        self.baseline = _parse(text)
        return backup


#: ``AUTOONTOLOGY_ENV_BACKUP_SECRETS=1`` keeps secret values in ``.env`` backups.
BACKUP_SECRETS_VAR = "AUTOONTOLOGY_ENV_BACKUP_SECRETS"


def _keep_backup_secrets() -> bool:
    return os.environ.get(BACKUP_SECRETS_VAR, "").strip().lower() in ("1", "true", "yes", "on")


_ASSIGN_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\r?\n?)$")


def scrub_secrets(text: str) -> str:
    """*text* (``.env`` content) with every non-empty secret value (a key name containing
    KEY, PASSWORD, TOKEN or SECRET) replaced by ``***``; every other line unchanged."""
    out = []
    for line in text.splitlines(keepends=True):
        m = _ASSIGN_RE.match(line)
        if m and SECRET_KEY_RE.search(m.group(2)) and m.group(4).strip() not in ("", '""', "''"):
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}***{m.group(5)}"
        out.append(line)
    return "".join(out)


def _private_copy(src: Path, dest: Path, *, scrub: bool = False) -> None:
    """Copy *src* to a new file *dest* that is 0600 from its creation (exclusive create,
    so the umask never makes the credentials readable by others while they are written),
    then copy *src*'s timestamps; a failed copy removes the partial file. With *scrub*,
    secret values are written as ``***`` (:func:`scrub_secrets`)."""
    data = src.read_bytes()
    if scrub:
        data = scrub_secrets(data.decode("utf-8")).encode("utf-8")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(dest)
        raise
    os.close(fd)
    st = src.stat()
    with contextlib.suppress(OSError):
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


SECRET_KEY_RE = re.compile(r"(KEY|PASSWORD|TOKEN|SECRET)", re.I)


def describe_change(key: str, old: str | None, new: str | None) -> str:
    """One line for a changed key; secret values are never shown."""

    def show(value: str | None) -> str:
        if value is None:
            return "(unset)"
        if SECRET_KEY_RE.search(key):
            return "(hidden)" if value else "(empty)"
        return value or "(empty)"

    return f"{key}: {show(old)} -> {show(new)}"


# --- sample sets ---------------------------------------------------------------------------

#: The sample sets ``demo`` and ``/api/samples`` know (direct subfolders of samples_dir).
SAMPLE_SETS: tuple[str, ...] = ("quickstart", "hanbit")
#: Source-name stem per set: ``quick``, ``quick-docs``; ``hanbit``, ``hanbit-docs``, ...
SAMPLE_PREFIXES: dict[str, str] = {"quickstart": "quick", "hanbit": "hanbit"}
#: (subfolder, kind, source-name suffix), in registration order.
SAMPLE_PARTS: tuple[tuple[str, str, str], ...] = (
    ("structured", "structured", ""),
    ("documents", "documents", "-docs"),
    ("images", "documents", "-images"),
)


class SampleSetError(ValueError):
    """Unknown or missing sample set."""


@dataclass
class SampleSource:
    name: str
    #: ``structured`` | ``documents`` (the ``source add --kind`` value)
    kind: str
    path: Path
    files: int
    bytes: int
    #: the file extensions present (lowercase, dotted, sorted)
    formats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path),
            "files": self.files,
            "bytes": self.bytes,
            "formats": self.formats,
        }


def _part_files(path: Path, kind: str) -> list[Path]:
    if kind == "structured":
        from autoontology.structured.loaders import discover_files

        return discover_files(path)
    from autoontology.unstructured.readers import discover_documents

    return discover_documents(path)


def sample_set_dir(settings: Settings, set_name: str) -> Path:
    if set_name not in SAMPLE_SETS:
        raise SampleSetError(
            f"unknown sample set {set_name!r} (choose one of: {', '.join(SAMPLE_SETS)})"
        )
    path = Path(settings.samples_dir) / set_name
    if not path.is_dir():
        raise SampleSetError(
            f"sample set {set_name!r} not found at {path}: update your checkout "
            "(`git pull`) or point AUTOONTOLOGY_SAMPLES_DIR at the samples folder"
        )
    return path


def sample_sources(settings: Settings, set_name: str) -> list[SampleSource]:
    """The sources of *set_name*: one per existing ``structured``/``documents``/``images``
    subfolder that holds supported files."""
    root = sample_set_dir(settings, set_name)
    out = []
    for folder, kind, suffix in SAMPLE_PARTS:
        path = root / folder
        if not path.is_dir():
            continue
        files = _part_files(path, kind)
        if not files:
            continue
        out.append(
            SampleSource(
                name=SAMPLE_PREFIXES[set_name] + suffix,
                kind=kind,
                path=path.resolve(),
                files=len(files),
                bytes=sum(f.stat().st_size for f in files),
                formats=sorted({f.suffix.lower() for f in files}),
            )
        )
    if not out:
        raise SampleSetError(f"sample set {set_name!r} at {root} has no supported files")
    return out


def _store_kind(kind: str) -> str:
    return "files" if kind == "structured" else "documents"


def _registered_path(source: Any) -> Path | None:
    import json

    try:
        return Path(json.loads(source.locator)["path"]).resolve()
    except (ValueError, KeyError, TypeError):
        return None


def is_registered(store: Any, sample: SampleSource) -> bool:
    current = store.get_source(sample.name)
    return (
        current is not None
        and current.kind == _store_kind(sample.kind)
        and _registered_path(current) == sample.path
    )


def list_sample_sets(settings: Settings, store: Any) -> list[dict[str, Any]]:
    """Every sample set present under ``settings.samples_dir``, for ``GET /api/samples``."""
    out = []
    for set_name in SAMPLE_SETS:
        try:
            sources = sample_sources(settings, set_name)
        except SampleSetError:
            continue
        out.append(
            {
                "set": set_name,
                "path": str((Path(settings.samples_dir) / set_name).resolve()),
                "sources": [s.as_dict() for s in sources],
                "registered": all(is_registered(store, s) for s in sources),
                #: expected build time per chat provider (the table `demo` prints)
                "durations": {
                    provider: {"text": text, "measured": measured}
                    for provider, (text, measured) in DEMO_DURATIONS.get(set_name, {}).items()
                },
            }
        )
    return out


Notify = Callable[[str], None]


class SampleConflict(ValueError):
    """Sources with a sample source's name exist with another path or kind; replacing
    them deletes their database and extracted concepts, so it needs consent."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        names = ", ".join(c["name"] for c in conflicts)
        super().__init__(
            f"source(s) {names} already exist with other data; replacing them deletes their "
            "database and extracted concepts"
        )


def sample_conflicts(settings: Settings, store: Any, set_name: str) -> list[dict[str, Any]]:
    """``[{name, kind, registered, sample}]`` for every source of *set_name* whose name is
    taken by a source with another path or kind (``registered`` = its current locator)."""
    out = []
    for sample in sample_sources(settings, set_name):
        current = store.get_source(sample.name)
        if current is None or is_registered(store, sample):
            continue
        old = _registered_path(current)
        out.append(
            {
                "name": sample.name,
                "kind": current.kind,
                "registered": str(old) if old is not None else current.locator,
                "sample": str(sample.path),
            }
        )
    return out


def register_sample_set(
    settings: Settings,
    store: Any,
    set_name: str,
    *,
    notify: Notify | None = None,
    remove: Callable[..., None] | None = None,
    replace: bool = False,
) -> list[tuple[Any, str]]:
    """Register the sources of *set_name* idempotently. A source that already exists with
    the same kind and path is kept; one with another path (or kind) is removed (with its
    database and extracted concepts) and added again, but only with *replace*: otherwise
    :class:`SampleConflict` is raised before anything changes (checked again right before
    each removal, so a source registered meanwhile is never removed without it). Returns
    ``[(source, "created" | "kept" | "replaced")]``."""
    from autoontology.engine.extract import ensure_documents_source
    from autoontology.structured import pipeline

    say = notify or (lambda _m: None)
    if not replace and (conflicts := sample_conflicts(settings, store, set_name)):
        raise SampleConflict(conflicts)
    if remove is None:
        from autoontology.structured.remove import remove_source as remove
    out: list[tuple[Any, str]] = []
    for sample in sample_sources(settings, set_name):
        current = store.get_source(sample.name)
        action = "created"
        if current is not None:
            if is_registered(store, sample):
                out.append((current, "kept"))
                say(f"{sample.name}: already registered ({sample.path})")
                continue
            if not replace:
                # registered by someone else after the preflight: the consent covered
                # nothing of it, so stop before anything is removed
                old_path = _registered_path(current)
                raise SampleConflict(
                    [
                        {
                            "name": sample.name,
                            "kind": current.kind,
                            "registered": str(old_path) if old_path else current.locator,
                            "sample": str(sample.path),
                        }
                    ]
                )
            old = _registered_path(current) or current.locator
            say(
                f"{sample.name}: registered for {old} ({current.kind}); replacing it with "
                f"{sample.path} (its database and extracted concepts are deleted)"
            )
            remove(
                settings,
                store,
                current,
                drop_db=current.kind == "files",
                notify=lambda message, _warning=False: say(f"  {message}"),
            )
            action = "replaced"
        if sample.kind == "structured":
            source = pipeline.add_source(store, str(sample.path), name=sample.name)
        else:
            source = ensure_documents_source(store, sample.name, sample.path)
        say(f"{sample.name}: {action} ({sample.kind}, {sample.files} files, {sample.path})")
        out.append((source, action))
    return out


# --- demo durations --------------------------------------------------------------------

#: Expected first-build duration per sample set and chat provider: (text, measured).
#: Measured values come from fresh-clone rehearsals on an Apple M4 Mac mini (16 GB),
#: 2026-10-29: quickstart/ollama build 2732 s (demo 2748 s, ~46 min), quickstart/claude build
#: 461 s and 637 s in two runs in working copies, hanbit/claude build 2198 s (2164 s +
#: a 34 s --resume after a usage limit).
#: Estimates are scaled from those (codex: compile and judge calls took about twice as
#: long as claude's; ollama on hanbit: ~83 s per chunk x 141 chunks plus alignment).
#: quickstart: 3 tables, 28 document chunks with kordoc / 9 without; hanbit: 11 tables,
#: 132 document chunks + 9 image chunks = 141.
DEMO_DURATIONS: dict[str, dict[str, tuple[str, bool]]] = {
    "quickstart": {
        "ollama": ("~46 min (qwen3:8b on an Apple M4)", True),
        "claude": ("8-11 min", True),
        "codex": ("~15 min", False),
        "openai": ("5-15 min (depends on the endpoint)", False),
    },
    "hanbit": {
        "ollama": ("4-5 h (qwen3:8b, 141 chunks)", False),
        "claude": ("~37 min", True),
        "codex": ("~75 min", False),
        "openai": ("20-60 min (depends on the endpoint)", False),
    },
}


def duration_rows(set_name: str, current: str) -> list[tuple[str, str, str]]:
    """``(provider, expected time, note)`` rows for the duration table ``demo`` prints."""
    rows = []
    for provider, (text, measured) in DEMO_DURATIONS.get(set_name, {}).items():
        note = "measured" if measured else "≈ estimate"
        if provider == current:
            note += "  <- your provider"
        rows.append((provider, text, note))
    return rows
