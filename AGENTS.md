# AutoOntology — notes for coding agents

AutoOntology builds one versioned ontology from structured data (CSV/Excel/Parquet/Postgres)
and unstructured documents (HWP/PDF/Office/Markdown/images) on top of the unmodified
NVIDIA Auto Ontology engine (`vendor/auto-ontology`), with a CLI, a web UI and RDF/OWL,
GraphML, Ossie and OKF exports. It runs locally: Postgres 17 + pgvector, Ollama for
embeddings, and a chat model from Ollama, the user's `claude`/`codex` CLI login, or an
OpenAI-compatible API.

## Set up and run in one command

```bash
scripts/bootstrap.sh --yes --provider claude        # prerequisites, uv sync, init, quickstart demo, UI URL
scripts/bootstrap.sh --dry-run                      # print the plan only
```

If the user asks to set AutoOntology up, run the bootstrap (or the
`/setup-autoontology` skill in `.claude/skills/`). It takes about 10 minutes: run it in the
background (always with `--yes`; without a terminal it exits 2) and wait. Exit code 3 means the provider CLI is
not logged in: ask the user to run `claude auth login` (Claude Enterprise/Team: the
organization account) themselves, then run the script again. Exit code 4 names a missing
prerequisite and how to install it (a `claude` that is only a shell alias: set
`AUTOONTOLOGY_CLAUDE_BIN`). `AUTOONTOLOGY_LANG=ko` (or `--lang ko`) prints the
CLI messages in Korean.

## Commands

```bash
uv sync                                              # Python 3.12 deps (uv)
docker compose up -d                                 # Postgres 17 + pgvector (or POSTGRES_* in .env)
uv run autoontology init --yes --provider claude     # writes .env, runs setup (add --docs for kordoc)
uv run autoontology demo --set quickstart --no-open  # up, register samples, build, export
uv run autoontology ui                               # http://127.0.0.1:7800/ui/ (AUTOONTOLOGY_PORT)
uv run autoontology doctor                           # every check with a remedy
uv run ao doctor                                     # `ao` is a short alias of `autoontology`
uv run autoontology down                             # stop the services
uv run autoontology ontology concepts -q 계약 --json   # every UI page has commands (--json):
#   ontology revisions|diff|concept|graph, runs show|logs|cancel, source freshness,
#   samples list|load, exports list|show|get|zip, playground chat|sql|sparql|index,
#   settings show|test|set|restart, agent tokens list|create|rotate|revoke, agent usage,
#   project list|show|create|rename|archive|unarchive,
#   user create|list|disable|enable|link, member list|add|role|remove, audit,
#   object types|bind|list|get, logic list|show|apply|validate|test|activate|retire,
#   action list|preview|run|runs|approve|reject|revert, workflow list|run|runs|show|cancel
#   (docs/GUIDE.md "The same from a terminal"; outside agents: POST /mcp, /agent/v1)
uv run ao -p <slug> build                            # --project/-p (or AUTOONTOLOGY_PROJECT) picks
#   the project for any command; API: /api/projects/<slug>/... (/api/... = the default project)
uv run ao user create you@example.com --admin       # first admin (prints an invite link), then
#   AUTOONTOLOGY_AUTH=accounts + AUTOONTOLOGY_PUBLIC_URL in .env and `ao down && ao up`;
#   `ao -p <slug> member add <email> --role viewer|reviewer|editor|owner`, `ao --as <email>` for audit

uv run ruff check . && uv run ruff format --check . && uv run pytest -q   # unit tests
AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration                   # real Postgres + Ollama
cd ui && pnpm install && pnpm lint && pnpm test && pnpm build            # web UI
uv run autoontology tools build-ui                   # rebuild the committed bundle in autoontology/web/static/dist
```

## Layout

- `autoontology/` — the Python package: `cli.py` (Typer), `config.py` (`.env` + shell),
  `onboarding.py` (init/demo), `structured/`, `unstructured/`, `ontology/`, `export/`,
  `engine/` (upstream client), `web/` (FastAPI service, `/api`, `/v1` chat router,
  `providers/`), `doctor.py`, `i18n.py`.
- `ui/` — the React web UI (Vite, pnpm); its build is committed under `autoontology/web/static/dist`.
- `samples/` — sample sets `quickstart` and `hanbit` (Korean content, fictional company).
- `vendor/auto-ontology` — the upstream engine as a git submodule. Read-only.
- `docs/` — `GUIDE.md` (domain experts), `ARCHITECTURE.md`; `scripts/` — bootstrap and generators.

## Rules

- Never edit anything under `vendor/`. Work around engine behaviour in `autoontology/` and
  pin it with a test.
- Every new Python file starts with `# SPDX-License-Identifier: MIT`.
- Code, comments, commit messages and docs are in English; sample data and prompts may
  stay Korean. Keep `README.md`/`README.ko.md` and `docs/GUIDE.md`/`GUIDE.ko.md` in step.
- Databases: only create or drop `autoontology` and `autoontology_src_<name>`. The Postgres
  server may be shared with other applications; never touch other databases.
- Secrets: never write API keys or tokens into tracked files, logs or test output; `.env`
  and `.env.bak-*` are git-ignored. The claude/codex providers use the user's own CLI login,
  never an API key.
- Tests first for behaviour changes; integration tests stay behind `AUTOONTOLOGY_INTEGRATION=1`.
- Accounts (`AUTOONTOLOGY_AUTH=off|accounts`, default off) must leave off mode unchanged. A
  new route needs a permission from `PROJECT_PERMISSIONS` in `autoontology/web/projects.py`
  (`ctx.require`) or `require_installation`, covered in `tests/test_permissions.py`
  (sign-in: `tests/test_auth_api.py`, store: `tests/test_accounts.py`, CLI:
  `tests/test_cli_users.py`). Never log passwords, session ids or login links.
- If `ui/` changes, rebuild and commit the bundle in the same change.
