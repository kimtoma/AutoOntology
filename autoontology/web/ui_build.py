"""``autoontology tools build-ui``: rebuild the committed web UI (``ui/`` -> ``web/static/dist``).

Only needed after changing ``ui/``; the service serves the committed build without Node.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from autoontology.web.api import UI_DIST

UI_SRC = Path(__file__).resolve().parents[2] / "ui"
#: The Vite 8 / Tailwind 4 toolchain needs Node 20.19+.
MIN_NODE = (20, 19)


class BuildUiError(Exception):
    pass


def _node_version(node: str) -> tuple[int, ...]:
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    try:
        return tuple(int(p) for p in out.stdout.strip().lstrip("v").split(".")[:2])
    except ValueError:
        return ()


def build_ui(src: Path = UI_SRC, *, echo: Callable[[str], None] = print,
             run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Path:
    """``pnpm install --frozen-lockfile && pnpm build`` in *src*; returns the dist dir."""
    if not (src / "package.json").is_file():
        raise BuildUiError(f"no UI sources at {src} (a source checkout is required)")
    node = shutil.which("node")
    if not node:
        raise BuildUiError("Node.js not found on PATH: install Node "
                           f"{MIN_NODE[0]}.{MIN_NODE[1]}+ (https://nodejs.org)")
    version = _node_version(node)
    if version and version < MIN_NODE:
        raise BuildUiError(f"Node {'.'.join(map(str, version))} is too old: the UI build "
                           f"needs {MIN_NODE[0]}.{MIN_NODE[1]}+")
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise BuildUiError("pnpm not found on PATH: `corepack enable pnpm` or "
                           "`npm install -g pnpm`")
    for argv in ([pnpm, "install", "--frozen-lockfile"], [pnpm, "build"]):
        echo(f"$ {' '.join(['pnpm', *argv[1:]])}  (in {src})")
        result = run(argv, cwd=src, check=False)
        if result.returncode != 0:
            raise BuildUiError(f"`pnpm {' '.join(argv[1:])}` failed (exit {result.returncode})")
    if not (UI_DIST / "index.html").is_file():
        raise BuildUiError(f"build finished but {UI_DIST}/index.html is missing")
    return UI_DIST
