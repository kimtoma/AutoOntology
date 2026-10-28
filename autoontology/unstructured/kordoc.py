"""kordoc (https://github.com/chrisryugj/kordoc, MIT, npm ``kordoc``, Node >= 20): the
document engine for HWP/HWPX/HWPML, PDF, DOCX, PPTX, XLS/XLSX and images.

AutoOntology runs the CLI as a subprocess (no shell), one process per document::

    node <cli.js> --format chunks --table-format gfm --silent -- <absolute file path>

stdout is JSON: a list of RAG chunks on success, ``{"success": false, "code", "error"}``
(exit 1) on failure. Everything kordoc returns is document data -- untrusted, never
instructions. ``KORDOC_OFFLINE=1`` is always set so the engine never touches the
network (OCR models must then already be present locally).

Engine resolution: ``KORDOC_CLI`` (path to ``dist/cli.js``) -> ``<home>/kordoc/
node_modules/kordoc/dist/cli.js`` (``autoontology tools install-kordoc``) -> ``kordoc`` on
PATH (the real ``dist/cli.js`` behind the npm shim). Node: ``KORDOC_NODE`` -> ``node``
on PATH, major version >= 20.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("autoontology.kordoc")

DEFAULT_VERSION = "4.24.13"
MIN_NODE_MAJOR = 20
DEFAULT_TIMEOUT_S = 300.0
INSTALL_COMMAND = "uv run autoontology tools install-kordoc"
VERSION_FILE = "kordoc.version"
#: File types kordoc parses (``kordoc --help``; HWPML is ``.hml``).
EXTENSIONS: frozenset[str] = frozenset(
    {
        ".hwp",
        ".hwpx",
        ".hml",
        ".hwpml",
        ".pdf",
        ".docx",
        ".pptx",
        ".xls",
        ".xlsx",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }
)
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")


class KordocError(RuntimeError):
    """kordoc failed on a document (``code`` is kordoc's, or one of ours in CAPS)."""

    def __init__(self, code: str, message: str, *, stderr: str = "") -> None:
        super().__init__(f"kordoc {code}: {message}")
        self.code = code
        self.message = message
        self.stderr = stderr


class KordocUnavailable(KordocError):
    """No usable engine (Node or kordoc missing); the message names the remedy."""


@dataclass(frozen=True)
class KordocEngine:
    node: str
    cli: Path
    origin: str  # "KORDOC_CLI" | "home" | "PATH"


@dataclass(frozen=True)
class KordocChunk:
    id: str
    type: str  # "text" | "table" | "heading" | ...
    breadcrumb: tuple[str, ...]
    text: str
    page: int | None


# --- resolution -----------------------------------------------------------------------


def install_dir(home: Path) -> Path:
    return Path(home) / "kordoc"


def home_cli(home: Path) -> Path:
    return install_dir(home) / "node_modules" / "kordoc" / "dist" / "cli.js"


def _cli_behind_shim(shim: str) -> Path | None:
    real = Path(os.path.realpath(shim))
    if real.suffix == ".js" and real.is_file():
        return real
    # npm global layout: <prefix>/bin/kordoc -> <prefix>/lib/node_modules/kordoc/dist/cli.js
    for candidate in (
        real.parent.parent / "lib" / "node_modules" / "kordoc" / "dist" / "cli.js",
        real.parent / "node_modules" / "kordoc" / "dist" / "cli.js",
    ):
        if candidate.is_file():
            return candidate
    return None


def resolve_cli(home: Path, env: Mapping[str, str] | None = None) -> tuple[Path, str] | None:
    env = os.environ if env is None else env
    explicit = env.get("KORDOC_CLI", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise KordocUnavailable("ENGINE_MISSING", f"KORDOC_CLI={explicit} does not exist")
        return path, "KORDOC_CLI"
    if home_cli(home).is_file():
        return home_cli(home), "home"
    shim = shutil.which("kordoc", path=env.get("PATH"))
    if shim:
        cli = _cli_behind_shim(shim)
        if cli is not None:
            return cli, "PATH"
    return None


def resolve_node(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    explicit = env.get("KORDOC_NODE", "").strip()
    if explicit:
        return explicit if Path(explicit).expanduser().is_file() else None
    return shutil.which("node", path=env.get("PATH"))


def node_version(node: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(p) for p in match.groups()) if match else None


def require_node(env: Mapping[str, str] | None = None) -> tuple[str, tuple[int, ...]]:
    node = resolve_node(env)
    if not node:
        raise KordocUnavailable(
            "NODE_MISSING",
            f"Node.js >= {MIN_NODE_MAJOR} not found (install Node.js 20+, or set KORDOC_NODE)",
        )
    version = node_version(node)
    if version is None or version[0] < MIN_NODE_MAJOR:
        shown = ".".join(map(str, version)) if version else "unknown"
        raise KordocUnavailable(
            "NODE_TOO_OLD",
            f"{node} is Node {shown}; kordoc needs >= {MIN_NODE_MAJOR} (install Node.js 20+)",
        )
    return node, version


def resolve_engine(home: Path, env: Mapping[str, str] | None = None) -> KordocEngine:
    """The engine to run, or :class:`KordocUnavailable` naming the install command."""
    found = resolve_cli(home, env)
    if found is None:
        raise KordocUnavailable(
            "ENGINE_MISSING",
            "the kordoc document engine is not installed (needed for "
            f"HWP/HWPX/PDF/DOCX/PPTX/XLSX/images); run `{INSTALL_COMMAND}`",
        )
    node, _ = require_node(env)
    return KordocEngine(node=node, cli=found[0], origin=found[1])


def timeout_s(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = env.get("AUTOONTOLOGY_KORDOC_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError as exc:
        raise ValueError(f"AUTOONTOLOGY_KORDOC_TIMEOUT must be a number, got {raw!r}") from exc


# --- running --------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    return {**os.environ, "KORDOC_OFFLINE": "1"}


def run_cli(
    engine: KordocEngine, args: list[str], *, timeout: float | None = None, label: str = ""
) -> subprocess.CompletedProcess[str]:
    cmd = [engine.node, str(engine.cli), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or timeout_s(),
            env=_child_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise KordocError(
            "TIMEOUT",
            f"{label or args[:1]} took longer than "
            f"{exc.timeout:.0f}s (AUTOONTOLOGY_KORDOC_TIMEOUT)",
        ) from exc
    except OSError as exc:
        raise KordocUnavailable("ENGINE_MISSING", f"cannot run {engine.node}: {exc}") from exc
    for line in (proc.stderr or "").splitlines():
        if line.strip():
            logger.info("kordoc: %s", line.strip()[:500])
    return proc


def _decode(proc: subprocess.CompletedProcess[str], label: str) -> Any:
    stderr_tail = (proc.stderr or "").strip()[-500:]
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and data.get("success") is False:
        raise KordocError(
            str(data.get("code") or "FAILED"),
            f"{label}: {str(data.get('error') or 'parse failed')[:300]}",
            stderr=stderr_tail,
        )
    if proc.returncode != 0 or data is None:
        raise KordocError(
            f"EXIT_{proc.returncode}",
            f"{label}: no JSON on stdout ({stderr_tail or 'no stderr'})",
            stderr=stderr_tail,
        )
    return data


def _page(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _path_args(path: Path) -> list[str]:
    """Options first, then ``--`` and the absolute path: a file named ``-x.hwp`` is never
    read as an option (verified: kordoc's commander parser honours ``--``)."""
    return ["--", str(Path(path).resolve())]


def parse_chunks(
    engine: KordocEngine,
    path: Path,
    *,
    timeout: float | None = None,
    extra_args: list[str] | None = None,
) -> list[KordocChunk]:
    """``--format chunks`` (the extractor's input). Malformed entries are skipped."""
    path = Path(path)
    proc = run_cli(
        engine,
        [
            "--format",
            "chunks",
            "--table-format",
            "gfm",
            "--silent",
            *(extra_args or []),
            *_path_args(path),
        ],
        timeout=timeout,
        label=path.name,
    )
    data = _decode(proc, path.name)
    if not isinstance(data, list):
        raise KordocError("BAD_OUTPUT", f"{path.name}: expected a JSON list of chunks")
    chunks: list[KordocChunk] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            logger.warning("kordoc %s: chunk %d is malformed, skipped", path.name, i)
            continue
        crumbs = item.get("breadcrumb")
        crumbs = tuple(str(c) for c in crumbs) if isinstance(crumbs, list) else ()
        chunks.append(
            KordocChunk(
                id=str(item.get("id") or f"c{i + 1:04d}"),
                type=str(item.get("type") or "text"),
                breadcrumb=crumbs,
                text=item["text"],
                page=_page(item.get("page")),
            )
        )
    return chunks


def parse_json(engine: KordocEngine, path: Path, *, timeout: float | None = None) -> dict:
    """``--format json --no-images``: markdown, blocks, metadata, pageCount."""
    path = Path(path)
    proc = run_cli(
        engine,
        ["--format", "json", "--no-images", "--table-format", "gfm", "--silent", *_path_args(path)],
        timeout=timeout,
        label=path.name,
    )
    data = _decode(proc, path.name)
    if not isinstance(data, dict):
        raise KordocError("BAD_OUTPUT", f"{path.name}: expected a JSON object")
    return data


def engine_version(engine: KordocEngine) -> str:
    proc = run_cli(engine, ["--version"], timeout=30, label="--version")
    version = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if proc.returncode != 0 or not version:
        raise KordocError(
            f"EXIT_{proc.returncode}", "`--version` failed", stderr=(proc.stderr or "")[-300:]
        )
    return version


# --- install --------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallResult:
    action: str  # "installed" | "already installed"
    version: str
    cli: Path
    omit_optional: bool


def read_version_file(home: Path) -> dict[str, Any] | None:
    path = install_dir(home) / VERSION_FILE
    if not path.is_file():
        return None
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return {"version": lines[0] if lines else "", "omit_optional": "omit=optional" in lines[1:]}


def install_kordoc(
    home: Path,
    *,
    version: str = DEFAULT_VERSION,
    omit_optional: bool = False,
    force: bool = False,
    npm: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    echo: Callable[[str], None] = print,
) -> InstallResult:
    """``npm install --prefix <home>/kordoc --no-audit --no-fund kordoc@<version>``."""
    if not _VERSION_RE.fullmatch(version):
        raise KordocError("BAD_VERSION", f"not a kordoc version: {version!r}")
    target = install_dir(home)
    recorded = read_version_file(home)
    if (
        not force
        and recorded
        and recorded["version"] == version
        and recorded["omit_optional"] == omit_optional
        and home_cli(home).is_file()
    ):
        return InstallResult("already installed", version, home_cli(home), omit_optional)
    require_node()
    npm = npm or shutil.which("npm")
    if not npm:
        raise KordocUnavailable(
            "NPM_MISSING", "npm not found (it ships with Node.js: install Node.js 20+)"
        )
    target.mkdir(parents=True, exist_ok=True)
    argv = [npm, "install", "--prefix", str(target), "--no-audit", "--no-fund", f"kordoc@{version}"]
    if omit_optional:
        argv.append("--omit=optional")
    echo("running: " + " ".join(argv))
    proc = runner(argv, check=False)
    if proc.returncode != 0:
        raise KordocError(
            f"EXIT_{proc.returncode}",
            "npm install failed (see the output above; npm needs network access for this one step)",
        )
    cli = home_cli(home)
    if not cli.is_file():
        raise KordocError("ENGINE_MISSING", f"npm finished but {cli} does not exist")
    lines = [version] + (["omit=optional"] if omit_optional else [])
    (target / VERSION_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return InstallResult("installed", version, cli, omit_optional)
