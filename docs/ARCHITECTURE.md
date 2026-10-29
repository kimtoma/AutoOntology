# Architecture

This document describes how AutoOntology is put together: its processes, its data flow,
where state lives, how model calls are routed, and where to extend it. The user-facing
overview is in the [README](../README.md).

## Components and ports

| Component | Where | Port | Role |
|---|---|---|---|
| AutoOntology service | `autoontology/web` (FastAPI, uvicorn) | `127.0.0.1:7800` (`AUTOONTOLOGY_PORT`) | REST API under `/api`, SSE run logs, the playground, the web UI under `/ui`, the `/v1` model router, and the `/ingest` shims the engine calls back. |
| Engine backend | `vendor/auto-ontology` (NVIDIA Auto Ontology, git submodule) | `127.0.0.1:3101` (`BACKEND_PORT`) | Connections, catalog, semantic model, exports and NL-to-SQL, over HTTP `/api/...`. |
| CLI | `autoontology/cli.py` (typer), `autoontology/onboarding.py` | none | Runs `build` and `export` in-process; `up`/`down` supervise the two servers; `init`, `demo` and `provider` drive the onboarding. |
| Postgres 17 + pgvector | `docker compose` or your own server | `5432` | The engine's catalog and vectors (`autoontology`) and one database per file source (`autoontology_src_<name>`). |
| SQLite store | `<AUTOONTOLOGY_HOME>/autoontology.db` | none | Everything AutoOntology owns: sources, runs, documents, concepts, alignments, snapshots, playground. |
| Model providers | Ollama (`:11434`), an OpenAI-compatible URL, or the user's `claude` / `codex` CLI (chat only) | none | Chat (extraction, judges, playground, engine compile) and embeddings. |
| kordoc (optional) | `<AUTOONTOLOGY_HOME>/kordoc` (npm, Node 20 or newer); OCR models in kordoc's model cache | none | Reads HWP/HWPX/PDF/DOCX/PPTX/XLS/XLSX into Markdown and OCRs images and scanned PDFs (PP-OCRv5 korean), run offline as a subprocess with an argv list. |

`autoontology up` starts both servers as background processes (`engine/supervisor.py`),
binds them to 127.0.0.1, writes PID files under `<home>/run/` and logs under
`<home>/logs/`, and waits for their health endpoints. The service rejects requests whose
`Host` is not local or whose `Origin` is cross-site. A tailnet hostname listed in
`AUTOONTOLOGY_TAILSCALE_HOSTS` (forwarded by `tailscale serve`) is accepted only with the
`Tailscale-User-Login` identity header, optionally limited to `AUTOONTOLOGY_TAILSCALE_LOGINS`.

## How the engine is embedded

The engine is pinned as an **unmodified** git submodule (`vendor/auto-ontology`, commit
`eb8dd06`) and installed as an editable path dependency (`auto-ontology-server`).
AutoOntology uses it in two ways:

- **Over HTTP** (`engine/client.py`): it creates and updates connections, reads the
  catalog and Terms, fetches the model export (`model.yaml`, the Ossie zip), and relays
  playground NL-to-SQL (`/api/chat/completions`). Every request carries the user header
  `x-auto-ontology-user-id: autoontology`.
- **In-process** (`engine/jobs.py`, `engine/upstream_semantic.py`): it runs ingest, the
  PII/rule replay and semantic compilation by importing `auto_ontology` directly, and it
  creates the pgvector tables at the embedder's dimension. The engine hard-codes 2048
  dimensions; AutoOntology creates the tables first through the engine's own DDL.

The engine reads its environment at import time, so AutoOntology prepares `os.environ`
(Postgres settings, the expanded model triplets and `INGESTION_SERVICE_URL`) before the
first import. Engine internals that AutoOntology depends on are pinned by tests
(`tests/test_upstream_terms.py` and others), so a submodule bump that changes them fails
loudly.

## Data flow

### Build (`engine/build.py`)

One run of kind `build` covers every registered source, in registration order:

```
structured:<name>:load -> register -> ingest -> compile        (each structured source)
documents:<name>:read  -> extract  -> consolidate              (each documents source)
align:<documents source>                                       (against all built Terms)
merge -> snapshot
```

- **Structured path.**
  - `load` reads the files with DuckDB (CSV/TSV, Parquet, JSON, XLSX, SQLite) or
    python-calamine (XLS), normalizes column names and writes the tables into
    `autoontology_src_<name>`. A database URL
    source skips this step and is registered as is.
  - `register` creates or updates the engine connection.
  - `ingest` runs the engine's catalog ingest and embeds the catalog.
  - `compile` runs the engine's semantic compilation, which produces Terms, column
    attributes and semantic foreign keys.
- **Documents path.**
  - `read` uses the native readers (`.md`, `.txt`, `.html`, `.csv`/`.tsv` as one table
    section, Word 97-2003 `.doc` through olefile) or kordoc (with `--ocr` for PDFs; images
    are OCR'd) and produces section-aware chunks (tiktoken). With
    `AUTOONTOLOGY_VISION_MODEL` set, an image first gets an "Image description" section
    from an Ollama vision model (`unstructured/vision.py`). Without kordoc, documents that
    need it are skipped with a run warning, not counted as failed; without the OCR models,
    images and image-only PDFs are skipped the same way. A source whose documents are all
    skipped reads 0 documents and `consolidate` stores an empty ontology for it.
  - `extract` makes one LLM call per chunk and returns concepts, relations and verbatim
    evidence. The output is schema-validated, and evidence quotes are checked against the
    chunk text.
  - `consolidate` merges duplicates across chunks and documents with embedding similarity
    plus an LLM merge judge.
- **Align.** Each documents source is aligned against the union of all structured
  sources' Terms: embedding retrieval, then an LLM judge, then the identity policy
  (`identity-v2.2`, `unstructured/align.py`). Accepted `same` verdicts add the concept's
  names to the Term's synonyms, except instance-like names (record IDs such as `S037`,
  numbers/dates, company names with a legal-form marker) and names equal to a column of
  the Term's own table or an FK column pointing at it (`계약ID`, `부서명`; reason
  `column_name`), which are recorded as `rejected_aliases` (concept detail, and
  `align-report.md`, which every align pass writes to
  `<home>/reports/align/<documents source>/`). An accepted `same` stored before
  v2.2 whose concept name is instance-like still folds on an export-only run (only its
  aliases are filtered) until the next align or `build --resume` re-applies the policy.
- **Merge** (`ontology/model.py`). `MergedOntology` combines Terms, document concepts,
  alignment axioms, relations and evidence into one model under `AUTOONTOLOGY_BASE_IRI`,
  and gives every node an origin (`structured`, `document` or `aligned`).
- **Snapshot.** The model is saved as JSON in `ontology_snapshots` with a new revision.

`--source N` records the other sources' steps as skipped, but align, merge and snapshot
still cover everything. `--resume` reuses the latest build run. Each per-source step
stores an `inputs_sha` (the locator plus the path and sha256 of every input file; for a
documents source also kordoc's availability and, with images or PDFs, the OCR models'
readiness and the vision model; for a database-URL source its live schema
from `information_schema` on Postgres), and `--resume` keeps a step only when that hash is
unchanged. A database source that cannot be read, or is MySQL or Snowflake (no bundled
driver), never matches, so its steps always run again. align, merge and snapshot also run
again when the set of registered sources changed, and a resumed align re-judges a concept
whose candidate Terms changed. A re-run step invalidates the
later steps of its own source and every global step. Failed chunks or documents make the
run `failed` with a `partial` note, but the snapshot is still saved.

Concurrency: a build holds a store lease on the workspace and on every source, reaps runs
left by dead processes, and refuses to start while another build, export or apply is active.

### Export (`engine/export_job.py`)

```
collect -> serialize -> upstream -> ossie -> okf -> report
```

The export loads a snapshot; it never rebuilds the model. It writes into a temporary
folder and swaps it into `exports/r<rev:04d>/`:

- `serialize` writes `ontology.ttl`, `ontology.jsonld` and `ontology.graphml`. The Turtle
  is checked by a parse round-trip.
- `upstream` fetches each structured source's `model.yaml` and Ossie zip from the backend.
- `ossie` (`export/ossie.py`) merges the upstream semantic models, writes the unified
  ontology and the mapping, validates them against the vendored JSON Schemas
  (`third_party/ossie`, offline `$ref` resolution) and round-trips the semantic model
  through the engine's Ossie converter.
- `okf` (`export/okf.py`) writes the OKF v0.2 bundle and validates it.
- `report` writes `report.md` and `export.json`, whose `files` list names every file the
  exporter wrote.

Replacing an existing revision folder (under `exports/` or `--out`) is refused unless
every file in it, at any depth including `okf/`, `ossie/` and `upstream/`, is on that
list (`check_replaceable`). A folder whose `export.json` has no list, from before the list
existed, is refused with a message to move or delete it. During the swap the previous
folder is renamed to `r<NNNN>.prev-<ts>-<run>` and deleted after the new one is in place;
the run is `succeeded` only after that. GraphML edge ids are `e0`, `e1`, ... and unique
across the file.

`--format` runs only the steps that format needs and carries the other formats' entries
over from the previous export of the same revision. A validation failure writes
`validation.json`, keeps the previous folder untouched and fails the run.

### Operations: review, apply, diff, freshness

A person's decisions on what the build proposed are stored in `review_decisions`
(`store/decisions.py`), one per subject: an alignment (`subject_key` = concept id:
`accept` with `same`, `related`, `narrower` or `broader`, or `reject`), an alias
(`<term id>|<alias>`: `accept` or `reject`) or an unaligned concept (`acknowledge`).

- **Overlay** (`engine/overlay.py`). A decided concept's `alignments` row carries the
  decision: relation and Term from the decision, `accepted` for an accept, `none` for a
  reject, rationale `human decision: <note>`. The judge's verdict moves to the row's
  `judged_json` and comes back when the decision is removed. The align judge step checks
  for a decision before the resume and cache paths, so a decided concept is never judged
  again (its Term must still exist; otherwise it is judged as usual). The align apply step
  writes the synonyms of a person's accepted `same` to the Term whatever its confidence.
  In the model, an accepted `related`/`narrower`/`broader` row becomes an
  `OAxiom(accepted=True)`; RDF and Ossie serialise it exactly like a proposal, but it
  makes the Term `aligned`. An accepted alias moves from `rejected_aliases` to the Term's
  `alt_labels`. `MergedOntology.decisions` lists the reflected decisions, and OKF marks
  those concepts `stable`.
- **Apply** (`engine/apply.py`, run kind `apply`): `decisions -> merge -> snapshot`. It
  writes the decided rows and restores the rows whose decision was removed, adds the
  synonyms of accepted `same` decisions upstream, then merges and saves a revision. It
  needs the backend but never a model or the LLM proxy. It holds the workspace lease, and
  build, export and apply refuse to start while another of them is active. Every snapshot
  step (build or apply) sets `applied_revision` on the pending decisions the model
  reflects.
- **Review inbox** (`web/review.py`, `/api/review`). Items are built from the alignment
  rows (`al:` refused `same` proposals, `ax:` judge axioms with confidence >= 0.6), the
  latest model (`un:` unaligned document classes, new since the previous revision first;
  `alias:` rejected aliases) and freshness (`src:` stale sources). Ids are stable. A
  decided item that is no longer current is rebuilt from the copy its decision stored.
- **Diff** (`web/revisions.py`, `/api/ontology/diff`). Nodes are keyed by model local.
  A node counts as changed when its label, kind, definitions, synonyms, tables or
  relation/property counts differ. Axioms and merges are diffed by their ends. Each list
  holds at most 500 entries; `totals` counts all of them.
- **Freshness** (`web/freshness.py`, `/api/sources/freshness`, 60 s cache). For each
  source, the current `input_fingerprint` is compared with the `inputs_sha` of its newest
  succeeded build step. When they differ, the reasons say what changed: files compared
  with the stored documents (path, sha256) or the load manifest, the database schema, or
  the reader settings. A source with no recorded build is `never_built`. Unreadable
  inputs or an unreachable database give `unknown` with the error class.

### Playground (`autoontology/playground`)

The scope is always the latest snapshot. The retrieval index (`elements.py`, `index.py`)
embeds one text per Term, concept, rule, process, relation, axiom and evidence node. It is
rebuilt when the snapshot changes. Chat (`service.py`) retrieves the top-k elements,
quotes them inside a delimited context block and streams the answer with citations. SQL
is relayed to the engine. SPARQL (`sparql.py`) evaluates against the snapshot's Turtle
with rdflib in up to two spawn-context worker processes, with a semaphore of 2 and a row
cap. Past the 10 s deadline the worker is killed (408), so a query that never reads the
graph also stops; a worker above 1024 MB resident memory exits and the query answers 413
`sparql_memory_limit`. The service parses the graph too, so memory is about twice the
parsed graph, and the first query after a kill pays one process start and one Turtle
parse. A keyword pre-filter and an algebra-level guard refuse `SERVICE`, `FROM` and
`LOAD`.

## Store tables (SQLite, `store/db.py`)

| Table | Holds |
|---|---|
| `schema_version` | Migration level (v1: base schema, v2: snapshots and workspace-scoped playground, v3: `term_alias_additions.source_id`, v4: `term_legacy_descriptions`, v5: `documents.reader_fingerprint`, v6: `review_decisions` and `alignments.judged_json`). |
| `sources` | Registered sources: name, kind (`files`, `database`, `documents`), locator, catalog database id. |
| `source_leases` | Leases that keep builds, exports and deletes from overlapping. |
| `runs`, `steps` | Build, export and apply runs and their steps, with details, timings and `inputs_sha`. |
| `file_manifests`, `semantic_baselines` | Input manifests and the catalog schema a semantic layer was compiled from. |
| `documents`, `chunks` | Read documents (sha256, reader) and their chunks. |
| `concepts`, `relations`, `evidence` | Extracted and consolidated document knowledge. |
| `term_embeddings` | Cached Term passage embeddings, by text hash and model. |
| `alignments`, `alignment_previews` | Judge verdicts with the identity policy version, and dry-run proposals. |
| `term_alias_additions`, `term_refresh_pending` | Synonyms AutoOntology added to Terms (with `source_id`, the documents source whose alignment added them, since v3; NULL on older rows), and Terms awaiting a refresh. `source rm` of a documents source removes that source's additions from the upstream Terms first. |
| `ontology_snapshots` | One row per revision: the run, sources, counts and the full model JSON. |
| `review_decisions` | A person's decision per review subject (alignment, alias, concept), who made it, and the revision that first reflected it (`applied_revision`). |
| `playground_index`, `playground_index_meta` | The element vectors and the snapshot they were built from. |
| `playground_conversations`, `playground_messages` | Stored chats, keyed by workspace. |

The engine keeps its own state in Postgres: the catalog and semantic model tables in the
`autoontology` database, the `vdb.*` vector tables, and each file source's data in
`autoontology_src_<name>`. `config.validate_pg_database_name` refuses every other
database name.

## Formats (`autoontology/formats.py`)

One module holds every supported extension: `STRUCTURED_SUFFIXES` (and the per-loader sets
the structured loaders import), `DOCUMENT_NATIVE_SUFFIXES`, `DOCUMENT_KORDOC_SUFFIXES` and
`IMAGE_SUFFIXES`. `describe()` groups them by source kind with their engine (`native`,
`kordoc`, `kordoc-ocr`) and a note; `GET /api/status` returns it as `formats`, and the web
UI derives its upload `accept` lists, hints and file-by-file preview from it (a copy in
`ui/src/test/fixtures/formats.json` is checked against it by `tests/test_ui_formats_fixture.py`).

## Onboarding (`autoontology/onboarding.py`)

The logic behind `init`, `demo`, `provider` and the samples API, without prompts (the CLI
in `cli.py` adds the questions and the output):

- `detect_environment` builds a `Detection` from the same checks `doctor` runs
  (`check_postgres`, `check_node`, `check_kordoc`, `check_ocr_models`) plus
  `provider_info` for each chat provider, which asks the provider's own `status()` (the
  check `/api/status` uses). `recommend_provider` picks claude, then codex (when logged in),
  else ollama; `init` keeps a provider already chosen in `.env` or the shell.
- `EnvFile` edits `.env` in place: it replaces an active line or the first commented
  `# KEY=` line, else appends under one `# --- Set by autoontology init ---` block; every
  other byte stays. Writes are atomic, keep the file mode (0600 for a new file) and copy
  the previous file to `.env.bak-<timestamp>` first. `describe_change` masks
  KEY/PASSWORD/TOKEN/SECRET values.
- Sample sets: `SAMPLE_SETS` (`quickstart`, `hanbit`) is the allowlist under
  `settings.samples_dir` (`AUTOONTOLOGY_SAMPLES_DIR`). A set's `structured/`, `documents/`
  and `images/` folders become the sources `<prefix>`, `<prefix>-docs` and
  `<prefix>-images`. `register_sample_set` keeps a source already registered with the same
  kind and path; a source of the same name holding other data is a `SampleConflict` unless
  `replace=True` (then it is removed, with its database for a files source, and added
  again). `DEMO_DURATIONS` holds the expected first-build time per set and chat provider,
  marked measured or estimated.
- `demo` is `up` + a prune of upstream connections whose database is gone (live ones that
  no source here owns are only reported) + `register_sample_set` + `build` on the set's
  sources + `export` + `ui`.

The web API serves the same functions: `GET /api/samples` (`list_sample_sets`, with each
source's formats and the durations) and `POST /api/samples/load {set, replace?}`, which
holds the workspace through `RunManager.claim_idle` while it registers (409 `run_active`
while a run is active, 409 `sample_conflict` with the conflicting locators).

## Provider routing

The engine picks its LLM client by model name: only names starting with `openai/` use
`ChatOpenAI`. AutoOntology therefore expands the provider settings
(`config.expand_model_profile`) into the engine's model triplets (`DEFAULT_MODELS_*`,
`REASONING_*`, `NON_REASONING_*`, `RERANK_*`). Every chat triplet points at the service's
own `/v1` router:

```
engine / AutoOntology LLM call
   model = "openai/qwen3:8b" or "openai/<model>"
          |
          v
  service /v1/chat/completions  (web/providers/router.py)
     ollama provider: forwards to AUTOONTOLOGY_OLLAMA_ENDPOINT unchanged
                      (openai/qwen3:8b is the Ollama alias name) and adds
                      reasoning_effort: none so JSON schemas are enforced
     openai provider: strips one openai/ prefix, sets Authorization: Bearer
                      <AUTOONTOLOGY_OPENAI_API_KEY>, forwards to
                      AUTOONTOLOGY_OPENAI_BASE_URL
     claude provider: model openai/claude:<m> -> `claude -p ... --json-schema`
     codex provider:  model openai/chatgpt:<m> -> `codex exec ... --output-schema`
```

The `claude` and `codex` providers (`web/providers/claude_cli.py`, `codex_cli.py`, shared
machinery in `base.py`'s `CLIProvider`) run the user's logged-in CLI once per request:
argv without a shell, the prompt on stdin, tools/MCP/settings/`@file` mentions off, an
empty temporary working directory, API keys stripped from the child env (so the
subscription login is used), and for codex a private `CODEX_HOME` holding only a symlink
to the user's `auth.json` (a refreshed token is copied back atomically under a lock).
`split_model` routes by the prefix after `openai/`: `claude:`, and `chatgpt:` for codex
(the engine is given `chatgpt:` because LangChain sends model names containing "codex" to
`/v1/responses`; the router also accepts `codex:`); a prefix for a provider the service
does not run is refused with 400 `provider_not_active`. Each request has one
deadline (`AUTOONTOLOGY_PROVIDER_TIMEOUT_S`) covering the wait for one of
`AUTOONTOLOGY_PROVIDER_MAX_INFLIGHT` slots, the call and at most one re-ask when the
answer fails its JSON schema (422 after that); the schema is checked before any call and
the answer is validated in a killable worker process (`schema_worker.py`). A client
disconnect kills the CLI's process group (499). CLI failures map to OpenAI-style errors:
401 `not_logged_in`, 400 `model_not_found`, 429 `usage_limit`, 502 `provider_error` /
`tool_use_refused`, 503 `cli_not_found`, 504 `upstream_timeout`, 413 for a schema too
large for the command line. `expand_model_profile` also raises the engine's
`LLM_INVOKE_TIMEOUT_S` above the router deadline so the router always answers first.

Embeddings (`EMBED_*`) go directly to Ollama or to the OpenAI-compatible base URL. The
router redacts upstream error bodies (keys, bearer tokens) and caps their size.

## Settings API and restart (`web/settings_api.py`, `engine/restart.py`)

The engine freezes its model endpoints at import time in both processes (the backend, and
the service, which imports it in-process during builds), so a provider change needs both
to restart. `web/settings_api.py` (installed by `api.install` behind the same Host/Origin
guard) reads the selection from the layers without touching `os.environ`
(`pending_settings`: shell > the current `.env` > defaults, with `config.env_origin` per
key), compares it with the running `Settings` (`restart_required`), tests a candidate
in-process (a throwaway `FastAPI` app with `build_router(candidate)` behind
`httpx.ASGITransport`, plus one `embed_texts` call with explicit `EmbedParams`), and writes
`.env` through `onboarding.EnvFile` (backup, then mode 0600). Keys set in the shell are
refused (`shell_override`); an embedder whose dimension differs from the stored `vdb.*`
vectors (`jobs.vector_table_state`) needs `confirm_reingest`.

`POST /api/settings/restart` refuses while anything runs (runs, claims, CLI runs, the
playground, a provider test) or when the service was not started by `up` (its PID file
must name this process), claims the workspace, writes `run/restart-<id>.json` and starts
`python -m autoontology.engine.restart` detached: its own session (so `down`, which kills
the service's process group, does not kill it), stdio to `logs/restart.log`, and an
environment without the keys this process derived or copied from `.env`
(`restart.helper_env`). The helper takes `run/restart.lock`, re-reads `.env`, waits half a
second for the 202 to leave, runs `Supervisor.down()` and `up()`, waits for the old pid to
go, and records `done` (with the new service pid) or `failed` (masked error). The new
service answers `GET /api/settings/restart/{id}`; the UI polls `/health/live`, then that
record until a `done` comes from another pid. `autoontology restart` runs the same helper
in the foreground. `supervisor.child_env` lists every value it copies from `.env` in
`AUTOONTOLOGY_DERIVED_ENV`, so children treat them as `.env` values, not shell values.
`POSTGRES_*` and `INGESTION_SERVICE_URL` that came from built-in defaults (not from `.env`)
stay in the children's env as if set in the shell, so a later `.env` change to them needs
`down` + `up`. A record whose helper process is gone is marked `failed` ("interrupted")
on the next read instead of blocking later restarts; a failed restart releases the
workspace claim at once; the newest 20 records are kept. The claim does not cover the
playground, so a playground request started during the half-second grace is cut off by
the restart. The saved API key is used only for the base-URL origin it was saved with:
another origin needs a new key (400 `key_required`).

## Internationalisation

Two languages, English and Korean, with no extra dependency on either side.

- **CLI** (`autoontology/i18n.py`): `msg(key, **kw)` looks the key up in
  `autoontology/i18n_catalog/en.py` or `ko.py` (plain dicts with the same keys; a key
  missing in Korean falls back to English) and fills `{name}` placeholders. The language
  is `AUTOONTOLOGY_LANG` (shell, then `.env`), else `ko` when `LC_ALL` / `LC_MESSAGES` /
  `LANG` starts with `ko`, else `en`. It covers the onboarding commands (`init`, `demo`,
  `provider`, `setup`, `source add/rm`), their `--help` texts and the doctor table.
  Commands, error codes, status words (OK/WARN/FAIL/SKIP), check names and details stay
  English. Doctor `Check` objects and the web API keep English text: `format_checks`
  translates the headers and the known remedy descriptions only when printing
  (`i18n.remedy`), and `source rm` translates the known notices of `structured/remove.py`
  in the CLI (`i18n.notice`). Tables pad by display width (Hangul is double-width).
- **Web UI** (`ui/src/i18n/`): `en.ts` is the key set (it spreads `en.explore.ts`, the
  Ontology/Graph/Playground/Exports/Status strings) and `ko.ts` is typed as
  `Record<keyof typeof en, string>`, so a missing key fails `tsc`. The current language lives
  in `core.ts`, so pure helpers (label tables, the file classifier, durations) translate
  too; `LangProvider` re-mounts the tree under the query cache when it changes, and
  `useT()` returns `t(key, vars?)` with `{name}` interpolation (`tx` fills placeholders with
  React nodes, `tn` picks `.one`/`.other`). Dates, numbers and durations go through `Intl`
  with the current language. The language comes from
  `?lang=`, then `localStorage["autoontology.lang"]`, then `navigator.language` (`ko*`),
  else English; the header's globe menu switches it without a reload and updates
  `<html lang>`. Backend remedy commands and `detail` texts are shown verbatim.

To add a language, add a catalogue on both sides, extend `LANGS` in `i18n.py` and the UI
language list, and add it to the parity tests (`tests/test_i18n.py`,
`ui/src/test/i18n.test.ts`).

## Extension points

| To add | Start here |
|---|---|
| A structured file format | `formats.py` (the suffix sets), then `structured/loaders.py` (the DuckDB and calamine readers). |
| A database type | `structured/pipeline.py` (`connection_for`, `_URL_SCHEMES`); the engine must have a connector for it. |
| A document format | `formats.py` (the suffix sets), then `unstructured/readers.py` (native readers) or `unstructured/kordoc.py`. |
| Extraction behaviour | `unstructured/prompts.py`, `unstructured/schemas.py`, `unstructured/extractor.py`. |
| Alignment policy | `unstructured/align.py` (`apply_identity_policy`, `IDENTITY_POLICY_VERSION`). Bump the version so stored verdicts are re-judged. |
| An export format | A module in `export/`, a step in `engine/export_job.py` (`EXPORT_STEPS`, `EXPORT_ENTRIES`, `FORMAT_ENTRIES`), and `web/runs.py` `TOP_STEPS`. |
| A model provider | `web/providers/` (a `PassthroughProvider`, or a `CLIProvider` for a CLI login), `router.py` (`Registry`, `split_model`), `config.py` (`CHAT_PROVIDERS`, `expand_model_profile`). |
| A sample set | A folder `samples/<set>/{structured,documents,images}` and its name in `onboarding.SAMPLE_SETS` / `SAMPLE_PREFIXES` (and `DEMO_DURATIONS`). |
| A UI page | `ui/src/pages` (strings in `ui/src/i18n/en.ts` and `ko.ts`), then `uv run autoontology tools build-ui` and commit `autoontology/web/static/dist`. |
