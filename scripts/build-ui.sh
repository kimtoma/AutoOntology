#!/usr/bin/env bash
# Rebuild the committed web UI: ui/ -> ontology/web/static/dist (= `uv run onto tools build-ui`).
set -euo pipefail
cd "$(dirname "$0")/../ui"
command -v node >/dev/null || { echo "Node.js not found on PATH (needs 20.19+)" >&2; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm not found on PATH (corepack enable pnpm)" >&2; exit 1; }
pnpm install --frozen-lockfile
pnpm build
echo "built: $(cd ../ontology/web/static/dist && pwd)"
