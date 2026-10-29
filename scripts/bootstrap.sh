#!/usr/bin/env bash
# AutoOntology bootstrap: check prerequisites, clone or update the repo, install, configure
# a model provider, build the quickstart sample set and print the web UI URL.
#
# Usage: scripts/bootstrap.sh [--dir PATH] [--provider claude|codex|ollama|openai]
#                             [--set quickstart|hanbit] [--no-demo] [--yes] [--dry-run]
#                             [--lang en|ko]
#   curl -fsSL https://raw.githubusercontent.com/kimtoma/AutoOntology/main/scripts/bootstrap.sh \
#     | bash -s -- --yes --provider claude
#
# Steps (each prints one status line, safe to re-run in the same folder):
#   1 prerequisites  git, uv, Postgres (Docker or POSTGRES_*), Node 20+ (optional, documents),
#                    the provider's CLI and login, Ollama (local embeddings)
#   2 clone/update   git clone --recurse-submodules | git pull --ff-only + submodule update
#   3 install        uv sync
#   4 configure      uv run autoontology init --yes --provider P [--docs|--no-docs]
#                    (+ AUTOONTOLOGY_PORT / BACKEND_PORT from the shell saved into .env)
#   5 demo           uv run autoontology demo --set S --no-open --replace   (unless --no-demo)
#   6 done           print the UI URL and the next steps
#
# Exit codes:
#   0  done (or --dry-run finished)
#   1  a step failed (its output is above; fix it and run the script again)
#   2  usage error (unknown flag or value; no terminal and no --yes)
#   3  the provider CLI is installed but not logged in (run the printed login command)
#   4  a prerequisite is missing or unreadable (the install/update hint is printed)
#
# Environment: POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE (use your own Postgres instead of
# Docker), AUTOONTOLOGY_PORT / BACKEND_PORT (service ports), AUTOONTOLOGY_CLAUDE_BIN /
# AUTOONTOLOGY_CODEX_BIN (CLI path when it is not on PATH), AUTOONTOLOGY_LANG=en|ko
# (language of the autoontology commands; --lang overrides it). Secrets are never printed.
# Compatible with bash 3.2 (macOS) and Linux. The whole script is one function called on the
# last line, so a truncated download (curl | bash) runs nothing.
set -euo pipefail

usage() {
  cat <<'EOF'
AutoOntology bootstrap: clone, install, configure and build the quickstart in one go.

Usage: bootstrap.sh [options]

  --dir PATH           where the checkout lives (default: the current AutoOntology checkout
                       when run inside one, else ./AutoOntology)
  --provider NAME      chat provider: claude (default) | codex | ollama | openai
  --set NAME           sample set for the demo: quickstart (default) | hanbit
  --no-demo            stop after `autoontology init` (no services, no build)
  --yes                non-interactive: install uv if missing, accept init's defaults,
                       replace sample sources of the same name (required without a terminal)
  --dry-run            print the plan and the prerequisite checks; change nothing
  --lang en|ko         language of the autoontology commands (default: AUTOONTOLOGY_LANG)
  -h, --help           this help

Exit codes: 0 done, 1 a step failed, 2 usage error, 3 provider not logged in,
4 prerequisite missing.

Re-running in the same folder is safe. Sample sources live in the Postgres catalog
`autoontology`: a second checkout on the same Postgres replaces the first one's sample
databases (autoontology_src_quick*) when it runs the demo with --yes.
EOF
}

die_usage() {
  echo "bootstrap: $1" >&2
  echo "run with --help for the options" >&2
  exit 2
}

say() { printf '%s\n' "$*"; }
step() { printf '[%s/6] %s\n' "$1" "$2"; }
ok() { printf '      ok    %s\n' "$*"; }
note() { printf '      note  %s\n' "$*"; }
plan() { printf '      dry-run: would run: %s\n' "$*"; }
q() { printf '%q' "$1"; }

# stop CODE MESSAGE [HINT...]: in --dry-run only report what would stop the real run.
stop() {
  local code="$1"; shift
  local msg="$1"; shift
  local line
  if [ "$DRY" -eq 1 ]; then
    printf '      dry-run: would stop here (exit %s): %s\n' "$code" "$msg"
    for line in "$@"; do printf '               %s\n' "$line"; done
    return 0
  fi
  printf '      FAIL  %s\n' "$msg" >&2
  for line in "$@"; do printf '            %s\n' "$line" >&2; done
  exit "$code"
}

have() { command -v "$1" >/dev/null 2>&1; }

is_checkout() {
  [ -f "$1/pyproject.toml" ] && [ -e "$1/.git" ] \
    && grep -q '^name = "autoontology"' "$1/pyproject.toml" 2>/dev/null
}

# Value of KEY from the shell, else from an active line of DIR/.env, else DEFAULT.
config_value() {
  local key="$1" default="$2" val
  val="${!key:-}"
  if [ -z "$val" ] && [ -n "$DIR" ] && [ -f "$DIR/.env" ]; then
    val=$(sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$key=//p" "$DIR/.env" \
      | tail -n 1 | sed -e 's/[[:space:]]*#.*$//' -e 's/^["'\'']//' -e 's/["'\'']$//')
  fi
  printf '%s' "${val:-$default}"
}

port_open() {
  local host="$1" port="$2"
  if have nc; then
    nc -z -w 2 "$host" "$port" >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1
  fi
}

# run_timeout SECS OUTFILE CMD...: run CMD with stdout+stderr in OUTFILE; 124 on timeout.
run_timeout() {
  local secs="$1" out="$2"; shift 2
  "$@" >"$out" 2>&1 </dev/null &
  local pid=$! ticks=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$ticks" -ge $((secs * 10)) ]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 0.1
    ticks=$((ticks + 1))
  done
  wait "$pid"
}

# resolve_cli NAME BIN_ENV [FALLBACK]: print the CLI path (env/.env, PATH, fallback) or nothing.
resolve_cli() {
  local name="$1" env_key="$2" fallback="${3:-}" bin
  bin=$(config_value "$env_key" "")
  if [ -n "$bin" ]; then
    case "$bin" in
      \~/*) bin="$HOME/${bin#\~/}" ;;
    esac
    if [ -x "$bin" ]; then printf '%s' "$bin"; return 0; fi
    bin=$(command -v "$bin" 2>/dev/null || true)
    [ -n "$bin" ] && printf '%s' "$bin"
    return 0
  fi
  bin=$(command -v "$name" 2>/dev/null || true)
  if [ -z "$bin" ] && [ -n "$fallback" ] && [ -x "$fallback" ]; then
    bin="$fallback"
  fi
  printf '%s' "$bin"
}

# claude_login_state FILE: print true | false | unknown for the top-level `loggedIn` key.
claude_login_state() {
  local file="$1" text compact
  if have python3; then
    python3 -I -c '
import json, sys
try:
    data = json.loads(open(sys.argv[1], encoding="utf-8", errors="replace").read())
except Exception:
    print("unknown"); sys.exit(0)
v = data.get("loggedIn") if isinstance(data, dict) else None
print("true" if v is True else "false" if v is False else "unknown")
' "$file" 2>/dev/null && return 0
  fi
  text=$(cat "$file")
  compact=$(printf '%s' "$text" | tr -d ' \n\t\r')
  case "$compact" in
    *'"loggedIn":true'*) printf 'true' ;;
    *'"loggedIn":false'*) printf 'false' ;;
    *) printf 'unknown' ;;
  esac
}

main() {
  local DEFAULT_REPO="https://github.com/kimtoma/AutoOntology.git"
  local UV_INSTALLER="https://astral.sh/uv/install.sh"
  DIR=""
  DRY=0
  local PROVIDER="claude" SET="quickstart" DEMO=1 YES=0
  local LANG_CHOICE="${AUTOONTOLOGY_LANG:-}" REPO_URL="$DEFAULT_REPO"

  while [ $# -gt 0 ]; do
    case "$1" in
      --dir)
        [ $# -ge 2 ] || die_usage "--dir needs a path"
        DIR="$2"; shift 2 ;;
      --dir=*) DIR="${1#*=}"; shift ;;
      --provider)
        [ $# -ge 2 ] || die_usage "--provider needs a value"
        PROVIDER="$2"; shift 2 ;;
      --provider=*) PROVIDER="${1#*=}"; shift ;;
      --set)
        [ $# -ge 2 ] || die_usage "--set needs a value"
        SET="$2"; shift 2 ;;
      --set=*) SET="${1#*=}"; shift ;;
      --lang)
        [ $# -ge 2 ] || die_usage "--lang needs a value"
        LANG_CHOICE="$2"; shift 2 ;;
      --lang=*) LANG_CHOICE="${1#*=}"; shift ;;
      --repo)  # hidden: clone from another URL or a local path (tests, mirrors)
        [ $# -ge 2 ] || die_usage "--repo needs a URL"
        REPO_URL="$2"; shift 2 ;;
      --repo=*) REPO_URL="${1#*=}"; shift ;;
      --no-demo) DEMO=0; shift ;;
      --yes|-y) YES=1; shift ;;
      --dry-run) DRY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done

  case "$PROVIDER" in
    claude|codex|ollama|openai) ;;
    *) die_usage "unknown provider '$PROVIDER' (use claude, codex, ollama or openai)" ;;
  esac
  case "$SET" in
    quickstart|hanbit) ;;
    *) die_usage "unknown sample set '$SET' (use quickstart or hanbit)" ;;
  esac
  case "$LANG_CHOICE" in
    ""|en|ko) ;;
    *) die_usage "unknown language '$LANG_CHOICE' (use en or ko)" ;;
  esac
  if [ -n "$LANG_CHOICE" ]; then
    export AUTOONTOLOGY_LANG="$LANG_CHOICE"
  fi

  # Without a terminal (curl | bash, an agent's shell) nobody can answer init/demo prompts.
  local INTERACTIVE=0
  [ -t 0 ] && INTERACTIVE=1
  if [ "$YES" -eq 0 ] && [ "$DRY" -eq 0 ] && [ "$INTERACTIVE" -eq 0 ]; then
    die_usage "no terminal to answer questions (e.g. curl | bash): pass --yes to run non-interactively, or --dry-run to see the plan"
  fi

  local START_TS
  START_TS=$(date +%s)

  # --- Resolve the checkout directory -----------------------------------------------------
  if [ -z "$DIR" ]; then
    local top src script_root=""
    top=$(git rev-parse --show-toplevel 2>/dev/null || true)
    src="${BASH_SOURCE[0]:-}"
    if [ -n "$src" ] && [ -f "$src" ]; then
      script_root=$(cd "$(dirname "$src")/.." 2>/dev/null && pwd || true)
    fi
    if [ -n "$top" ] && is_checkout "$top"; then
      DIR="$top"
    elif [ -n "$script_root" ] && is_checkout "$script_root"; then
      DIR="$script_root"
    else
      DIR="$PWD/AutoOntology"
    fi
  fi
  case "$DIR" in
    /*) ;;
    *) DIR="$PWD/$DIR" ;;
  esac
  local QDIR
  QDIR=$(q "$DIR")

  say "AutoOntology bootstrap: provider $PROVIDER, sample set $SET, directory $DIR"
  [ "$DRY" -eq 1 ] && say "(dry-run: nothing is installed, cloned or changed)"

  # --- 1. Prerequisites -------------------------------------------------------------------
  step 1 "prerequisites"

  if have git; then
    ok "git $(git --version 2>/dev/null | sed 's/^git version //')"
  else
    stop 4 "git is not installed" "macOS: xcode-select --install   Linux: install git with your package manager"
  fi

  if have uv; then
    ok "$(uv --version 2>/dev/null | head -n 1)"
  elif [ "$DRY" -eq 1 ]; then
    plan "curl -LsSf $UV_INSTALLER | sh   (uv is not installed)"
  elif [ "$YES" -eq 1 ]; then
    note "uv not found; installing it with the official installer ($UV_INSTALLER)"
    if ! curl -LsSf "$UV_INSTALLER" | sh; then
      stop 4 "the uv installer failed" "install uv yourself: https://docs.astral.sh/uv/getting-started/installation/"
    fi
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH
    have uv || stop 4 "uv was installed but is not on PATH" "open a new shell and run this script again"
    ok "$(uv --version 2>/dev/null | head -n 1) (installed for this run)"
  else
    stop 4 "uv is not installed" "install it:  curl -LsSf $UV_INSTALLER | sh" \
      "or re-run this script with --yes to install it automatically"
  fi

  local PG_HOST PG_PORT USE_COMPOSE=0 pg_from_env=0
  PG_HOST=$(config_value POSTGRES_HOST localhost)
  PG_PORT=$(config_value POSTGRES_PORT 5432)
  if [ -n "${POSTGRES_HOST:-}" ] || { [ -f "$DIR/.env" ] && grep -q '^[[:space:]]*POSTGRES_HOST=' "$DIR/.env" \
    && ! grep -q '^POSTGRES_HOST=localhost$' "$DIR/.env"; }; then
    pg_from_env=1
  fi
  if port_open "$PG_HOST" "$PG_PORT"; then
    ok "Postgres reachable at $PG_HOST:$PG_PORT"
  elif [ "$pg_from_env" -eq 1 ]; then
    note "Postgres at $PG_HOST:$PG_PORT (from POSTGRES_*) is not reachable yet; init checks it again"
  elif have docker && docker info >/dev/null 2>&1; then
    USE_COMPOSE=1
    ok "Docker daemon reachable (Postgres 17 + pgvector via docker compose up -d)"
  else
    stop 4 "no Postgres: Docker is not running and POSTGRES_* is not set" \
      "start Docker Desktop (or the docker service) and run this script again, or" \
      "use your own Postgres 17 + pgvector:  export POSTGRES_HOST=… POSTGRES_PORT=… POSTGRES_USER=… POSTGRES_PASSWORD=…"
  fi

  local DOCS_FLAG="--no-docs" node_ver node_major
  if have node; then
    node_ver=$(node --version 2>/dev/null | sed 's/^v//')
    node_major=${node_ver%%.*}
    case "$node_major" in
      ''|*[!0-9]*) node_major=0 ;;
    esac
    if [ "$node_major" -ge 20 ]; then
      DOCS_FLAG="--docs"
      ok "Node.js $node_ver (HWP/PDF/Office documents and OCR enabled)"
    else
      note "Node.js $node_ver is older than 20: PDF/HWP/Office documents are skipped (install Node 20+ for them)"
    fi
  else
    note "Node.js not found: PDF/HWP/Office documents are skipped (optional; install Node 20+ for them)"
  fi

  local cli_bin tmp_out rc state
  case "$PROVIDER" in
    claude)
      cli_bin=$(resolve_cli claude AUTOONTOLOGY_CLAUDE_BIN "$HOME/.claude/local/claude")
      if [ -z "$cli_bin" ]; then
        stop 4 "the claude CLI (Claude Code) was not found on PATH" \
          "install Claude Code: https://docs.claude.com/en/docs/claude-code/setup  (the desktop app alone is not enough)" \
          "shell aliases and functions are not visible to scripts: add claude to PATH or set" \
          "AUTOONTOLOGY_CLAUDE_BIN=/path/to/claude in the environment or .env; then sign in with: claude auth login"
      else
        tmp_out=$(mktemp "${TMPDIR:-/tmp}/ao-claude-status.XXXXXX")
        rc=0
        # Outside any parent Claude Code session's environment; the output is never printed.
        run_timeout 20 "$tmp_out" env -u CLAUDECODE -u ANTHROPIC_API_KEY "$cli_bin" auth status || rc=$?
        state=$(claude_login_state "$tmp_out")
        rm -f "$tmp_out"
        if [ "$state" = "true" ]; then
          ok "claude CLI logged in"
        elif [ "$state" = "false" ]; then
          stop 3 "the claude CLI is not logged in" \
            "run: claude auth login" \
            "(Claude Enterprise / Team: sign in with your organization account)" \
            "check with: claude auth status   then run this script again"
        elif [ "$rc" -eq 124 ]; then
          stop 4 "\`claude auth status\` did not answer within 20 s" \
            "run it yourself to see what it waits for, then run this script again"
        else
          stop 4 "could not read \`claude auth status\` (output not shown)" \
            "update Claude Code: claude update   then check: claude auth status"
        fi
      fi ;;
    codex)
      cli_bin=$(resolve_cli codex AUTOONTOLOGY_CODEX_BIN)
      if [ -z "$cli_bin" ]; then
        stop 4 "the codex CLI was not found on PATH" \
          "install it: npm install -g @openai/codex   then: codex login" \
          "or set AUTOONTOLOGY_CODEX_BIN=/path/to/codex (aliases are not visible to scripts)"
      else
        tmp_out=$(mktemp "${TMPDIR:-/tmp}/ao-codex-status.XXXXXX")
        rc=0
        run_timeout 20 "$tmp_out" "$cli_bin" login status || rc=$?
        rm -f "$tmp_out"
        if [ "$rc" -eq 0 ]; then
          ok "codex CLI logged in"
        elif [ "$rc" -eq 124 ]; then
          stop 4 "\`codex login status\` did not answer within 20 s"
        else
          stop 3 "the codex CLI is not logged in" "run: codex login   then run this script again"
        fi
      fi ;;
    openai)
      note "openai: init uses AUTOONTOLOGY_OPENAI_BASE_URL / _CHAT_MODEL from .env;" \
        "add AUTOONTOLOGY_OPENAI_API_KEY to .env yourself (never pass it on the command line)" ;;
    ollama)
      ok "ollama chat (local models, no login)" ;;
  esac

  local OLLAMA_URL
  OLLAMA_URL=$(config_value AUTOONTOLOGY_OLLAMA_ENDPOINT "http://localhost:11434/v1")
  OLLAMA_URL=${OLLAMA_URL%/}
  OLLAMA_URL=${OLLAMA_URL%/v1}
  if have curl && curl -fsS -m 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    ok "Ollama reachable at $OLLAMA_URL (local embeddings)"
  elif [ "$PROVIDER" = "openai" ]; then
    note "Ollama not reachable: fine if AUTOONTOLOGY_EMBED_PROVIDER=openai is set in .env"
  elif have ollama; then
    stop 4 "Ollama is installed but not running at $OLLAMA_URL (needed for embeddings)" \
      "start it: open the Ollama app, or run 'ollama serve' in another terminal; then run this script again"
  else
    stop 4 "Ollama is not installed (needed for the local embeddings, even with $PROVIDER chat)" \
      "install it: https://ollama.com/download  (macOS: brew install ollama), start it, run this script again"
  fi

  # --- 2. Clone or update -----------------------------------------------------------------
  step 2 "clone or update $DIR"
  if is_checkout "$DIR"; then
    if [ "$DRY" -eq 1 ]; then
      plan "git -C $QDIR pull --ff-only && git -C $QDIR submodule update --init"
    else
      if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        note "local changes in $DIR: not pulling"
      elif git -C "$DIR" pull --ff-only -q </dev/null >/dev/null 2>&1; then
        ok "updated ($(git -C "$DIR" rev-parse --short HEAD))"
      else
        note "could not fast-forward (no upstream or diverged): keeping $(git -C "$DIR" rev-parse --short HEAD)"
      fi
      git -C "$DIR" submodule update --init --recursive -q </dev/null || stop 1 "git submodule update failed"
      ok "submodules ready"
    fi
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    stop 4 "$DIR exists and is not an AutoOntology checkout" \
      "pick another directory with --dir, or move that one away"
  elif [ "$DRY" -eq 1 ]; then
    plan "git clone --recurse-submodules $REPO_URL $QDIR"
  else
    git clone --recurse-submodules -q "$REPO_URL" "$DIR" </dev/null || stop 1 "git clone failed"
    ok "cloned $(git -C "$DIR" rev-parse --short HEAD)"
  fi

  # run_in_dir LABEL CMD...: run CMD in $DIR (or print it in --dry-run). Without a terminal
  # the command reads /dev/null, so it can never swallow the rest of a piped script.
  run_in_dir() {
    local label="$1"; shift
    local shown="" arg
    for arg in "$@"; do shown="$shown $(q "$arg")"; done
    if [ "$DRY" -eq 1 ]; then
      plan "(cd $QDIR &&$shown)"
      return 0
    fi
    local status=0
    if [ "$INTERACTIVE" -eq 1 ]; then
      (cd "$DIR" && "$@") || status=$?
    else
      (cd "$DIR" && "$@" </dev/null) || status=$?
    fi
    if [ "$status" -eq 0 ]; then
      ok "$label"
    else
      stop 1 "$label failed (exit $status)" "fix the problem above and run this script again (it is safe to re-run)"
    fi
  }

  # --- 3. Install -------------------------------------------------------------------------
  step 3 "install (uv sync)"
  if [ "$USE_COMPOSE" -eq 1 ]; then
    run_in_dir "Postgres started (docker compose up -d)" docker compose up -d --wait
  fi
  run_in_dir "dependencies installed" uv sync

  # --- 4. Configure -----------------------------------------------------------------------
  step 4 "configure (autoontology init)"
  if [ "$YES" -eq 1 ]; then
    run_in_dir "configured: .env written, setup done" uv run autoontology init --yes --provider "$PROVIDER" "$DOCS_FLAG"
  else
    run_in_dir "configured: .env written, setup done" uv run autoontology init --provider "$PROVIDER" "$DOCS_FLAG"
  fi
  # Ports given in the shell only would be lost in the next shell: keep them in .env
  # (init's own .env editor, with a backup).
  if [ -n "${AUTOONTOLOGY_PORT:-}" ] || [ -n "${BACKEND_PORT:-}" ]; then
    run_in_dir "ports saved in .env" uv run python -c '
import os
from pathlib import Path
from autoontology.onboarding import EnvFile
env = EnvFile.load(Path(".env"))
for key in ("AUTOONTOLOGY_PORT", "BACKEND_PORT"):
    if os.environ.get(key):
        env.set(key, os.environ[key])
if env.changes():
    env.write()
'
  fi

  # --- 5. Demo ----------------------------------------------------------------------------
  if [ "$DEMO" -eq 1 ]; then
    step 5 "build the $SET sample set (autoontology demo)"
    if [ "$YES" -eq 1 ]; then
      run_in_dir "sample set built and exported" uv run autoontology demo --set "$SET" --no-open --replace
    else
      run_in_dir "sample set built and exported" uv run autoontology demo --set "$SET" --no-open
    fi
  else
    step 5 "demo skipped (--no-demo)"
  fi

  # --- 6. Done ----------------------------------------------------------------------------
  step 6 "done"
  local UI_PORT UI_URL code ELAPSED
  UI_PORT=$(config_value AUTOONTOLOGY_PORT 7800)
  UI_URL="http://127.0.0.1:$UI_PORT/ui/"
  if [ "$DEMO" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    code=""
    if have curl; then
      code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$UI_URL" 2>/dev/null || true)
    fi
    if [ "$code" = "200" ]; then
      ok "web UI answers (HTTP 200)"
    else
      note "the web UI did not answer yet; check with: cd $QDIR && uv run autoontology doctor"
    fi
  fi
  ELAPSED=$(( $(date +%s) - START_TS ))
  say ""
  say "UI: $UI_URL"
  say "Open it in a browser, or run: cd $QDIR && uv run autoontology ui"
  say "Next steps:"
  if [ "$DEMO" -eq 0 ]; then
    say "  cd $QDIR && uv run autoontology demo --set $SET     # build the sample set"
  fi
  say "  look at Ontology -> Graph -> Playground in the UI"
  say "  add your own data: uv run autoontology source add <folder> --name <name> && uv run autoontology build"
  say "  stop the services: uv run autoontology down"
  if [ "${AUTOONTOLOGY_LANG:-}" = "ko" ]; then
    say "다음 단계: 브라우저에서 위 UI 주소를 열고 온톨로지 → 그래프 → 플레이그라운드 순서로 보세요. 내 데이터는 'uv run autoontology source add'로 추가합니다."
  fi
  say "total time: ${ELAPSED}s"
}

main "$@"
