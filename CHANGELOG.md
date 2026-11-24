# Changelog

All notable changes to AutoOntology. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
behaviour).

## Unreleased

### Added

- **`ao`, a short alias of the `autoontology` command** (`pyproject.toml` `[project.scripts]`): the same Typer app, so every command and option works as `ao …`.
- **Outside agents can read the ontology** (`autoontology/web/agent_api.py`): an MCP
  endpoint `POST /mcp` (Streamable HTTP, JSON-RPC 2.0, protocol `2025-06-18`, JSON
  answers; `initialize`, `ping`, `tools/list`, `tools/call`, batches, notifications) and a
  REST API under `/agent/v1` (`revisions`, `ontology`, `concepts`, `concepts/{id}`,
  `sparql`, `exports`, `exports/{rev}.zip`, `openapi.json`) share six read-only tools:
  `list_revisions`, `get_ontology_summary`, `search_concepts`, `get_concept`,
  `sparql_query`, `list_exports`. Every request needs `Authorization: Bearer aok_...`,
  from localhost too (401 otherwise); a token may be pinned to one revision
  (`revision_pinned`), is limited to 120 requests a minute
  (`AUTOONTOLOGY_AGENT_RATE_PER_MIN`, 429 + `Retry-After`) and 1 MB bodies (413). Tokens
  are stored as SHA-256 only (schema v7: `agent_tokens`, `agent_calls` with the newest
  5000 calls) and managed through `GET/POST /api/agent/tokens`,
  `POST /api/agent/tokens/{id}/rotate`, `DELETE /api/agent/tokens/{id}`,
  `GET /api/agent/usage`, `GET /api/agent/tools`, and from the CLI with
  `agent tokens list|create|rotate|revoke` and `agent usage`.
- **Connect page in the web UI** (`/ui/connect`, after Exports in the sidebar): the MCP,
  REST and OpenAPI addresses with copy buttons and the rate limit; the tokens with status,
  revision pin, expiry, last use and 30-day call count; create (name, latest or a pinned
  revision, 30/90 days or no expiry), rotate and revoke with confirmations; the raw token
  shown once with Claude Code, Claude Desktop / Cursor and curl settings filled in (the same
  settings with a `<TOKEN>` placeholder below the tokens); the tools with their parameters;
  and the last 30 days of calls by tool and by day.
- **Download a whole export revision as one zip**: `GET /api/exports/{rev}.zip` (`3` or
  `r0003`) packs exactly the files `GET /api/exports` lists under `r0003/`, as
  `autoontology-r0003.zip`; the Exports page has a **Download all (zip)** button on every
  revision card and the CLI has `exports zip <rev> --out FILE|DIR`. Files whose symlink
  points outside the revision folder are no longer listed (the download already refused
  them).
- **Every web UI function from the CLI** (`autoontology/cli_ops.py`), each with `--json`
  and English/Korean help: `ontology revisions|diff|concepts|concept|graph` (and
  `ontology --revision R`), `runs show|logs [--follow]|cancel`, `source freshness` (alias
  `sources`), `samples list|load [--replace]`, `exports list|show|get`,
  `playground chat|sql|sparql|index`, `settings show|test|set|restart [--wait]` (the API
  key only through `--api-key-stdin`), `review list --page/--size`. Read-only commands run
  the UI's own `/api` route functions in-process, so they work with the service down; the
  others call the running service and exit 2 with the `up` hint when it is not running.
- **Settings page** (`/ui/settings`, gear after Status): choose the chat provider (Ollama,
  Claude subscription, Codex/ChatGPT subscription, OpenAI-compatible API with base URL and
  API key), the embedding provider and models, **Test** a candidate without applying it,
  and **Save and restart**. Backed by `GET`/`PUT /api/settings`, `POST /api/settings/test`,
  `POST /api/settings/restart` (202, a detached helper `autoontology.engine.restart`) and
  `GET /api/settings/restart/{id}`. The API key is written only to `.env` (mode 0600,
  backup) and never returned or logged; shell-set keys are read-only; an embedding
  dimension change needs `confirm_reingest`. Home/Status provider cards link to it.
- **`autoontology restart [--only backend]`** restarts the service and the backend with the
  current `.env`; `provider use` gains `--embed`, `--embed-model`, `--base-url` and
  `--openai-chat-model` (the key is never taken on the command line).
- **Overview dashboard** (`/ui/` once a snapshot exists): the revision with its build time
  and counts and the change since the previous revision (+added, −removed, ~changed,
  **View changes**); what needs a person's confirmation (counts per category and the first
  five items, decided in place); each source's freshness in plain words with **Build
  changed sources** (a resume build of those sources); the last five runs; and operations
  notices (a failed run, unapplied decisions with **Apply**, changed sources, a build older
  than 7 days, an export behind the latest revision). It refreshes every 30 s and when a
  run ends.
- **Review page** (`/ui/review`, with the pending count on its sidebar entry): six tabs,
  Proposals, Relations, Unaligned, Synonyms, Sources and Merged (automatic folds, not
  counted in the badge); each card shows the concept and
  its definition, the candidate Term and relation, the confidence, the rationale and up to
  three evidence quotes. **Accept** (as same, related, narrower or broader for proposals and
  relations), **Reject**, **Keep separate** (unaligned concepts) and **Undo**; `j`/`k` move,
  `a`/`r` decide. A banner applies the pending decisions (an `apply` run, no model call)
  and follows the run to the new revision. Category, filter and page live in the URL.
- **Ontology page**: a revision selector (older revisions read their own snapshot) and a
  **Changed in r{n}** view listing the added, changed and removed classes, merges and
  relation changes of that revision. The concept sheet shows the review decisions about a
  concept (accepted or rejected by a person, who, when, and whether a revision applied it),
  or that it is still a proposal.
- `GET /api/ontology/concepts/{id}` returns `decisions` (the review decisions about the
  concept, with `decided_by`, `decided_at`, `applied_revision` and the review `item_id`),
  and the diff names the ends of changed axioms and merges (`subject_name`,
  `object_name`, `term_name`).
- Run kind `apply` is labelled "Apply decisions" on the Build page and in the run sheet.
- **Remote access over Tailscale Serve**: `AUTOONTOLOGY_TAILSCALE_HOSTS` lists tailnet
  hostnames the Host guard accepts, only with the `Tailscale-User-Login` identity header
  (`tailscale_identity_required` otherwise, so Funnel traffic is refused);
  `AUTOONTOLOGY_TAILSCALE_LOGINS` optionally limits the logins (`access_denied`).
  `/api/status` reports `remote`. See README "Remote access over Tailscale".
- **Review decisions** (store schema v6, `review_decisions`). You can accept or reject an
  alignment proposal or a judge axiom (`same`, `related`, `narrower`, `broader`), accept
  or reject a rejected alias, or keep an unaligned concept separate (`acknowledge`). A
  decision overrides the align judge on every later pass; the judge's verdict is kept and
  comes back when the decision is removed. Accepted `related`/`narrower`/`broader` verdicts are the first
  accepted alignment axioms. OKF marks decided concepts `stable`.
- **`autoontology apply`** and `POST /api/runs {"kind": "apply"}`: a run of three steps,
  `decisions`, `merge` and `snapshot`. It reflects the decisions in a new revision with
  no model call. With nothing pending it writes no revision (merge and snapshot are
  `skipped`); `--force` / `{"force": true}` writes one anyway. When a decision rejects or
  removes a fold whose synonyms were already written, they are taken off the upstream
  Term again. You can also reject an automatic fold from the inbox
  (`GET /api/review?category=folds`).
- **Alignment status in exports**: every alignment axiom is marked `proposed`,
  `accepted` (by the judge) or `decided` (by a person). It appears as
  `ao:alignmentStatus` in RDF, in the OKF relation lines and in the Ossie `related_to`
  description.
- **Review inbox API**: `GET /api/review?category=&status=&page=&size=`,
  `GET /api/review/pending-count`, `POST`/`DELETE /api/review/{item_id}/decision`.
  `decided_by` is the tailnet login when the request comes through Tailscale Serve. CLI:
  `autoontology review list|accept|reject|acknowledge|undo`.
- **Revisions and diff**: `GET /api/ontology/revisions` and
  `GET /api/ontology/diff?from=&to=`, which lists added, removed and changed classes,
  axioms, merges and sources, plus the change in counts.
- **Source freshness**: `GET /api/sources/freshness[?refresh=1]` shows, per source,
  whether its inputs changed since the last build: files added, changed or removed, a
  schema change, a reader change, `never_built` or `unknown`.

### Changed

- **Engine updated to NVIDIA auto-ontology c96048c4 (1.3.x)**, from `eb8dd062` (1.1.0),
  89 upstream commits. `pyproject.toml` now needs `auto-ontology-server>=1.3.0`.
  - Key upstream improvements: text-to-SQL is a ReAct agent (`react_sql_agent`,
    `format_and_respond`, `unconstructable_sql_response`); compile adds term families
    (tables repeated under several prefixes), attaches suspected FK columns that found no
    target, prunes semantic FKs from empty or disjoint columns and ends with one LLM pass
    describing every column attribute; Hangul and other non-Latin table names and labels
    are kept in Term names (`5fc3e2b2`; `고객` used to become an empty Term name);
    a table whose key column has no values is no longer skipped (`67f5bd54`); PII column
    values are withheld from sampling, probes and embeddings; Postgres foreign keys to
    other schemas and SQLite implicit foreign keys are read.
  - Migrations: `7d864cacef9c` (term families) -> `84dd4f50dbba`
    (`catalog_column.distinct_ratio`) -> `caee572fa81c` (`catalog_column.distinct_values`).
    Run `uv run autoontology setup` once after updating; `doctor` shows `alembic` WARN
    until then.
  - Dependencies: the Ossie converter now installs from `apache/ossie-converters`
    (`cb4b46b9`, subdirectory `nvidia`; module `ossie_nvidia_auto_ontology` unchanged);
    `langgraph` and its packages are gone from the lock; the engine now accepts
    `openai>=2.44,<3` and an uncapped `langchain-openai` (the lock keeps openai 2.44.0
    and langchain-openai 1.3.4); `regex` is declared by the engine.
  - Behaviour: a build makes more LLM calls in `compile` (one attribute-description call
    per 15 attributes of a Term and table; for unmatched FK columns, a column-description
    call and, for a table without a Term, a term call; term families only re-embed) and a playground SQL question may take up to 24 tool calls.
    Column attributes in the exports keep the column's own description (a source comment
    or the compiler's column description, often Korean) and fall back to the engine's new
    English attribute sentence only when the column has none (`structured/semantic.py`,
    one extra `GET /api/columns/{table_id}` per table). Informational SQL answers arrive
    with `sql_code: ""` and `sql_response_from_db: null`; the CLI prints just the answer.
- The `/settings` UI route is the Settings page (it used to redirect to `/status`).
- The sidebar reads Overview, Review, Sources, Build, Ontology, Graph, Playground,
  Exports, Status, Settings. The first-run **Get started** checklist left it: `/ui/`
  shows the checklist only while there is no snapshot, and it stays at `/ui/setup`,
  linked as **Setup guide** from Settings and the command menu (⌘K).

### Fixed

- `provider use --base-url` refuses an endpoint on another origin (scheme, host, port)
  while an API key is saved, in `.env` or the shell, like the Settings page: the key is
  never carried to another endpoint. Change both on the Settings page, or drop a `.env` key
  with the new `--clear-api-key`.
- **Keep separate** now holds on every later pass: an acknowledged concept is never folded
  automatically (its row is `none`, rationale `kept separate by decision`; the judge's
  verdict is kept for **Undo**), even when it later gains a matching candidate, and
  acknowledging a folded concept takes its synonyms off the Term.
- Rejecting folds that two documents sources share takes the synonym off the Term once the
  last of them is rejected or its source removed, in any order or mix, together, or after
  a failed removal: a fold that kept the synonym in place takes it over.
- A pending decision whose concept is gone (its documents source was removed before
  apply) is retired by the next apply or build: kept for the record with `retired` in its
  payload, no longer counted as pending work.
- Source freshness finds a source's last build however many other runs (exports, no-op
  applies) followed it; concurrent freshness requests never start a second computation
  while one runs (a waiter that runs out of time gets the cached rows, or `computing`).
- Values the supervisor copied from `.env` into its children are now listed in
  `AUTOONTOLOGY_DERIVED_ENV`, so a restarted service re-reads a rewritten `.env` instead of
  keeping the old values as if they came from the shell.

## 0.2.0 - 2026-10-29

Onboarding for domain experts, many more input formats, subscription model providers and a
Korean interface.

### Added

- **Guide**: `docs/GUIDE.md` and `docs/GUIDE.ko.md`, a manual for domain experts (words
  explained, checklist, provider choice, the 10-minute path, preparing your own files,
  reading the results, troubleshooting, FAQ).
- **Onboarding commands**: `autoontology init` (detects Postgres, the chat providers,
  embeddings and Node, writes `.env` with a backup, runs `setup`; `--yes` for scripts),
  `autoontology demo [--set quickstart|hanbit] [--resume] [--replace] [--no-open]`
  (services, sample sources, build with per-step times, export, web UI) and
  `autoontology provider status|test|use`.
- **Claude and ChatGPT subscriptions as chat providers** (`AUTOONTOLOGY_CHAT_PROVIDER=claude`
  or `codex`): the logged-in `claude` / `codex` CLI runs as a locked-down subprocess per
  call, routed through the service's `/v1` router; login tokens are never sent or logged.
  `GET /api/status` reports the provider state, `POST /api/providers/test` makes one test
  call.
- **Formats**: CSV/TSV and legacy Word `.doc` documents (native), Excel 97-2003 `.xls`
  tables (python-calamine), OCR of images (PNG/JPEG/WebP) and scanned PDFs through kordoc's
  PP-OCRv5 models (`tools install-kordoc` downloads them), and optional image descriptions
  by an Ollama vision model (`AUTOONTOLOGY_VISION_MODEL`). `GET /api/status` lists every
  supported extension as `formats`.
- **Sample sets**: `samples/quickstart` (the previous samples) and `samples/hanbit`, a
  fictitious company with 9 table files in 7 formats (11 tables, 5,603 rows), 15 documents
  in 12 formats and 6 images, generated reproducibly by `scripts/gen_samples.py`.
  `GET /api/samples` and `POST /api/samples/load` serve them to the web UI.
- **Web UI**: a **Get started** page with a live five-step checklist, a provider card with a
  Test button, sample loading, a file-by-file format preview in the Sources dialog, plain
  step names and timings on the Build page, and OCR/vision rows on the Status page.
- **Korean**: the web UI switches between English and Korean (globe menu, `?lang=ko`), and
  the onboarding commands print Korean with `AUTOONTOLOGY_LANG=ko` or a Korean locale.
- `doctor` rows for the chat provider login, the OCR models, the vision model and orphaned
  upstream connections; every FAIL/WARN row has a remedy. `tools prune-connections`
  removes upstream connections whose database is gone.
- `CHANGELOG.md`.

### Changed

- The README starts with a five-line quickstart (`init` + `demo`), a provider comparison
  and the sample sets with measured build times; the reference sections follow.
- The 0.1.0 samples (the tables and the documents folder) moved to
  `samples/quickstart/structured` and `samples/quickstart/documents`; the demo source names
  are `quick` and `quick-docs`.
- Identity policy `identity-v2.2`: instance-like names (record IDs such as `S037`,
  numbers/dates, company names with a legal-form marker such as `주식회사`, `㈜` or `Inc.`)
  never become Term synonyms or identity evidence, and a concept named like one is never
  `same`. Names equal to a column of the Term's own table or an FK column pointing at
  it (`계약ID`, `부서명`) never become Term synonyms either. Both are listed as rejected synonyms in the concept detail
  and in `align-report.md`, which `build` and `demo` write to
  `<home>/reports/align/<documents source>/`.
- With a non-Ollama chat provider, `setup` pulls only the embedding model.
- `source rm --force --drop-db` keeps the database when the upstream connection could not
  be deleted, and says how to clean it up later.
- New runtime dependencies: `olefile` (BSD-2-Clause) and `python-calamine` (MIT).

## 0.1.0 - 2026-10-28

First public release.

- One build over structured sources (CSV, TSV, XLSX, JSON, Parquet, SQLite, Postgres,
  MySQL, Snowflake) through NVIDIA Auto Ontology, and documents (Markdown, text, HTML, and
  with kordoc HWP/HWPX, PDF, DOCX, PPTX, XLSX) through LLM extraction, consolidation and
  alignment under the `identity-v2.1` policy, saved as versioned snapshots.
- Exports to Turtle, JSON-LD, GraphML, Apache Ossie and OKF, with validation.
- Web UI (Sources, Build, Ontology, Graph, Playground, Exports, Status), playground chat
  with citations, NL-to-SQL and read-only SPARQL.
- Ollama or any OpenAI-compatible endpoint as model providers.
