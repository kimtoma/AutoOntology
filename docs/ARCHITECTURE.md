# Architecture

This document describes how AutoOntology is put together: its processes, its data flow,
where state lives, how model calls are routed, and where to extend it. The user-facing
overview is in the [README](../README.md).

## Components and ports

| Component | Where | Port | Role |
|---|---|---|---|
| AutoOntology service | `autoontology/web` (FastAPI, uvicorn) | `127.0.0.1:7800` (`AUTOONTOLOGY_PORT`) | REST API under `/api`, SSE run logs, the playground, the web UI under `/ui`, sign-in (`/api/auth`, `/auth/magic`), the outside-agent surfaces (`/mcp`, `/agent/v1`), the `/v1` model router, and the `/ingest` shims the engine calls back. |
| Engine backend | `vendor/auto-ontology` (NVIDIA Auto Ontology, git submodule) | `127.0.0.1:3101` (`BACKEND_PORT`) | Connections, catalog, semantic model, exports and NL-to-SQL, over HTTP `/api/...`. |
| CLI | `autoontology/cli.py` (typer), `autoontology/onboarding.py` | none | Runs `build` and `export` in-process; `up`/`down` supervise the two servers; `init`, `demo` and `provider` drive the onboarding. |
| Postgres 17 + pgvector | `docker compose` or your own server | `5432` | The engine's catalog and vectors (`autoontology`) and one database per file source (`autoontology_src_<name>`; `autoontology_src_<slug_>__<name>` in a project other than the default one). |
| SQLite store | `<AUTOONTOLOGY_HOME>/autoontology.db` | none | Everything AutoOntology owns: projects, sources, runs, documents, concepts, alignments, snapshots, playground, agent tokens, and (schema v9) accounts and the audit log. |
| Model providers | Ollama (`:11434`), an OpenAI-compatible URL, or the user's `claude` / `codex` CLI (chat only) | none | Chat (extraction, judges, playground, engine compile) and embeddings. |
| kordoc (optional) | `<AUTOONTOLOGY_HOME>/kordoc` (npm, Node 20 or newer); OCR models in kordoc's model cache | none | Reads HWP/HWPX/PDF/DOCX/PPTX/XLS/XLSX into Markdown and OCRs images and scanned PDFs (PP-OCRv5 korean), run offline as a subprocess with an argv list. |

`autoontology up` starts both servers as background processes (`engine/supervisor.py`),
binds them to 127.0.0.1, writes PID files under `<home>/run/` and logs under
`<home>/logs/`, and waits for their health endpoints. The service rejects requests whose
`Host` is not local or whose `Origin` is cross-site. A tailnet hostname listed in
`AUTOONTOLOGY_TAILSCALE_HOSTS` (forwarded by `tailscale serve`) is accepted only with the
`Tailscale-User-Login` identity header, optionally limited to `AUTOONTOLOGY_TAILSCALE_LOGINS`.
With `AUTOONTOLOGY_AUTH=accounts` a sign-in gate runs after this guard (see
[Accounts and permissions](#accounts-and-permissions)).

## Projects

One installation holds several projects (schema v8, `store/db.py`). Each has its own
sources, runs, review decisions, revisions (r1, r2, ... per project), exports,
playground index and conversations, and outside-agent tokens. Rows from before projects
belong to the default project (`prj_default`), whose slug is the workspace's
(`AUTOONTOLOGY_WORKSPACE`, slugified) and can be renamed. A slug is
`^[a-z][a-z0-9-]{1,23}$`; ids (`prj_<hex>`) never change.

- **Store.** `Store.project(ref)` returns a `ProjectStore`: every read and write is
  limited to that project, and an object looked up by a store-wide id (run, source)
  belonging to another project reads as missing. It stands in for a `Store` wherever the
  engine takes one, so a build of a project aligns, merges and snapshots only that
  project's sources.
- **Databases.** A source of the default project keeps `autoontology_src_<name>`; a new
  source of another project gets `autoontology_src_<slug_>__<name>` (the name budget is
  `46 - len(slug) - 2`). A database belongs to one project: registering the same external
  database in a second project is refused.
- **Exports** live in `exports/<slug>/r<rev:04d>/`. Legacy `exports/rNNNN` folders move
  into the default project's folder once (recorded in `exports/.layout-projects.json`).
  A slug rename (`PATCH /api/projects/{project}`) renames `exports/<old>/` to
  `exports/<new>/` together with the store row (both or neither), and is refused while a
  run of that project is active.
- **Uploads** of the default project stay in `<home>/uploads/<name>/`; other projects'
  go to `<home>/uploads/.projects/<project id>/<name>/`.
- **Locks.** Run-manager keys and store leases are per project; v1 adds a global engine
  gate, so one build, export or apply runs across the installation (another project's run
  is refused with 409, never queued). Queries and the playground run per project.
- **API.** Every project-scoped route is canonical under `/api/projects/{project}/...`
  and kept as an alias under `/api/...` for the default project
  (`web/projects.py`). The `ProjectContext` dependency resolves the slug (404
  `unknown_project`), refuses writes to an archived project (409 `project_archived`) and
  carries the caller (`actor`: the signed-in user's email with accounts on, else the
  tailnet login or `local`) and their role in the project; `require(permission)` checks
  the role table (see [Accounts and permissions](#accounts-and-permissions)). Installation-wide routes
  (`/api/status`, `/api/providers/test`, `/api/settings`) and the management routes
  (`/api/projects`) have no project. The outside-agent surfaces (`/mcp`, `/agent/v1`)
  take the project from the bearer token.
- **CLI.** `--project/-p SLUG` (or `AUTOONTOLOGY_PROJECT`) before the command selects the
  project: store commands use its `ProjectStore`, API commands call its canonical routes.

## Accounts and permissions

Sign-in is off by default (`AUTOONTOLOGY_AUTH=off`): every caller past the Host/Origin
guard is a synthetic admin named after the tailnet login or `local`, exactly as before
accounts. `AUTOONTOLOGY_AUTH=accounts` turns on users, sessions, project memberships and
the audit log (schema v9; `store/accounts.py`, `web/auth.py`, `web/accounts_api.py`,
`web/mailer.py`).

**Who is asking** (`auth.AuthService.resolve`, cached per request by `current_user`):

1. the CLI's local key (`X-AutoOntology-Internal`, see below): an admin named `cli`, or the
   user named by `X-AutoOntology-As` (`ao --as <email>`) for audit attribution;
2. the session cookie: a random id whose SHA-256 is the `sessions` row. The cookie is
   `HttpOnly`, `SameSite=Strict`, `Path=/`, no `Domain`, `Max-Age` =
   `AUTOONTOLOGY_SESSION_HOURS` (default 12, 1 to 720). It is `Secure` and named
   `__Host-ao_session` when `AUTOONTOLOGY_PUBLIC_URL` is https, the proxy sends
   `X-Forwarded-Proto: https` or the `Host` is not local; plain-http local use gets
   `ao_session`. A Secure request reads only the `__Host-` name. Every login issues a new id
   and ends the session it replaces; logout clears both names;
3. the Tailscale identity header on a configured tailnet hostname, mapped to the active
   user with that email;
4. else nobody. A disabled user is nobody.

**Passwords and tokens** (`store/accounts.py`): PBKDF2-HMAC-SHA256, 240,000 rounds,
16-byte salt, 12 to 1024 characters; `verify_password` spends one PBKDF2 on every path so
its timing does not reveal whether an account exists. Single-use login tokens
(`login_tokens`, SHA-256 only) have three purposes: `magic` (sign-in, 15 minutes),
`reset` (15 minutes) and `invite` (7 days). Issuing one deletes the user's earlier unused
token of that purpose; disabling a user deletes their sessions and unused tokens.

**Sign-in flows** (`/api/auth/*`, `/auth/magic/*`; public, but behind the Host/Origin
guard, which is what stops login CSRF):

- Password: `POST /api/auth/login` answers 401 `invalid_credentials` for every failure.
  Rate limits are in-memory sliding windows per process: 20 failures a minute per client
  IP and 5 per (email, IP) give 429 `too_many_attempts` with `Retry-After` (attempts in
  flight count). Failures for an email from all addresses never refuse; they only delay
  the failed answer (0.25 s doubling, at most 4 s), so nobody can lock a user out.
- Magic link, two steps: `POST /api/auth/magic-link` always answers 202 and mails a link
  only for an active user when SMTP is configured (5 a minute per IP and per (email, IP),
  20 an hour per email). `GET /auth/magic/<token>` never signs in (mail scanners prefetch
  links): it serves a no-store confirm page whose button posts the token to
  `POST /api/auth/magic`, which consumes it and sets the cookie.
- Invitation and reset: the link is `/ui/invite#token=...&purpose=invite|reset` (the token
  is in the fragment, so it reaches no server log or `Referer`); the page posts it with the
  new password to `POST /api/auth/accept-invite`, which sets the password, ends the user's
  other sessions and signs in. A weak password leaves the token usable.
- `POST /api/auth/password` (`{current, new}`) ends every other session; wrong `current`
  passwords count against the same IP and (email, IP) limits.
- The client IP is the socket peer, or the first `X-Forwarded-For` entry when the peer is
  loopback (the local gateway); no other header is trusted.

Links point at `AUTOONTOLOGY_PUBLIC_URL` (else the request's base URL); `ao doctor` warns
when accounts are on and it is unset or not https. **Mail** (`web/mailer.py`, stdlib
`smtplib`): `AUTOONTOLOGY_SMTP_HOST`, `_PORT` (587, STARTTLS by default; 465 TLS),
`_USER`, `_PASSWORD`, `_FROM`, `_STARTTLS`; English and Korean templates by the request's
`Accept-Language`. Without a host nothing is sent, `GET /api/auth/me` reports
`magic_link_enabled: false` and the UI hides the button; admins copy links from
`/ui/users` or `ao user link`. Neither the SMTP password nor a link is ever logged.

**Where permissions are enforced.**

- Gate (`auth.engine_refusal`, run by the service middleware after the Host/Origin
  guard): every path except `/api/auth`, `/auth/magic`, `/health`, `/mcp`, `/agent/v1`,
  `/ui` and `/` needs a caller (401 `login_required`).
- Project routes: `ProjectContext` (`web/projects.py`) resolves the caller's membership;
  a non-member gets 404 `unknown_project` (a project's existence is not revealed) and
  admins are owners of every project. Each handler calls `ctx.require(permission)` (403
  `forbidden`, the body names the permission). The one role table is
  `PROJECT_PERMISSIONS`: viewer (`project.read`, `exports.read`, `playground.ask`) <
  reviewer (`review.decide`, `review.apply`) < editor (`sources.write`, `runs.start`,
  `agent.tokens`, `playground.manage`) < owner (`project.manage`, `members.manage`,
  `audit.read`).
- Installation routes call `require_installation` (admins only): `project.create`,
  `settings.manage` (settings, provider test, restart), `users.manage`, `audit.read_all`.
- Archived projects refuse writes (409 `project_archived`) except archive/unarchive and
  playground questions; `AUTOONTOLOGY_ARCHIVED_PLAYGROUND=deny` refuses those too.
- Outside agents keep their bearer tokens; with accounts on a token stops working when
  its `created_by` user is disabled or no longer a member of the token's project (tokens
  without a creator keep working).
- The UI reads `permissions` and `installation_permissions` from `GET /api/auth/me` and
  hides or disables what the caller cannot do (`ui/src/lib/auth.ts`); the server decides.

**Audit log** (`audit_log`, written by `ProjectContext.audit` / `projects.audit` and the
sign-in routes): sign-ins and failures, password changes, users, members, agent tokens,
run starts and cancels, review decisions and undos, sources, samples, settings changes and
restarts, project create/rename/archive/unarchive, each with the user, project, client IP
and a JSON detail. It has no foreign keys, so it outlives the users and projects it names.
`GET /api/audit` pages newest first (`before_id`); owners must name a project they own,
admins see everything. CLI entries carry `user_id` NULL and `actor: cli` unless `--as`
names a user.

**Local trust: the engine's routes** (`config.py`, "local trust"). With accounts on, the
service keeps a random secret in `<home>/run/internal.key` (0600, created on first use)
and derives one key per purpose with HMAC-SHA256 (`derived_key`), so leaking one opens
only its own door:

| Purpose | Who sends it | Opens |
|---|---|---|
| `engine` | the engine, as its OpenAI API key (`Authorization: Bearer aoe-...`) | `/v1` only |
| `ingest` | the engine, through `INGESTION_SERVICE_URL=http://127.0.0.1:<port>/internal/<key>` (it cannot add headers there); the middleware rewrites the path to `/ingest*` | `/ingest`, `/ingest/delete` only |
| `local` | the CLI, the provider probe and the playground, as `X-AutoOntology-Internal` | everything, as the admin `cli` |

Anyone else reaches `/v1` and `/ingest*` only with an admin session (403 otherwise); a
wrong `/internal/<key>` is 404. No address or `Host` is trusted for this, because behind a
local gateway every request arrives from 127.0.0.1 with a local `Host`, just like the
engine's. Switching `AUTOONTOLOGY_AUTH` or rotating `internal.key` needs a full restart
(`ao down && ao up`): each process keeps the keys it derived.

**Public deployment behind a gateway.** The service still binds to 127.0.0.1; a gateway on
the same machine (or a tunnel to it) forwards to it. The Host/Origin guard still runs: the
forwarded request carries a local `Host`, and a non-GET request's `Origin` must match it
(or be absent). Set `AUTOONTOLOGY_PUBLIC_URL` to the https address people open (links and
the Secure `__Host-ao_session` cookie depend on it) and send `X-Forwarded-For` /
`X-Forwarded-Proto`. Of the browser's credentials only the session cookie needs to cross
the gateway. At the edge, block `/v1`, `/ingest` and `/internal`, and never forward
`X-AutoOntology-Internal`, `X-AutoOntology-As` or, outside `/mcp` and `/agent/v1`,
`Authorization`.

## How the engine is embedded

The engine is pinned as an **unmodified** git submodule (`vendor/auto-ontology`, commit
`c96048c`, package 1.3.0) and installed as an editable path dependency
(`auto-ontology-server>=1.3.0`).
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
first import. With accounts on, the engine's OpenAI API key for `/v1` and its
`INGESTION_SERVICE_URL` carry keys derived from `<home>/run/internal.key` (see
[Accounts and permissions](#accounts-and-permissions)). Engine internals that AutoOntology depends on are pinned by tests
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
    attributes and semantic foreign keys. Since engine 1.3 it also groups tables repeated
    under several prefixes into term families, attaches suspected FK columns that found no
    target, and ends with one LLM pass that writes a sentence onto every column attribute
    (batches of 15 per Term and table). AutoOntology's snapshot keeps the column's own
    description first (a source comment or the compiler's column description, often
    Korean) and uses that engine sentence only for a column without one
    (`structured/semantic.py`).
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

Concurrency: a build holds a store lease on its project and on every source, plus the
installation-wide engine gate, reaps runs left by dead processes, and refuses to start
while another build, export or apply is active (in any project).

### Export (`engine/export_job.py`)

```
collect -> serialize -> upstream -> ossie -> okf -> report
```

The export loads a snapshot; it never rebuilds the model. It writes into a temporary
folder and swaps it into `exports/<project slug>/r<rev:04d>/`:

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
(`store/decisions.py`), one per subject:

| Subject | `subject_key` | Decisions |
|---|---|---|
| alignment (proposal, axiom or automatic fold) | concept id | `accept` with `same`, `related`, `narrower` or `broader`; `reject` |
| alias | `<term id>\|<alias>` | `accept`, `reject` |
| unaligned concept | concept id | `acknowledge` |

A concept holds one decision: an alignment decision replaces its `concept` one and vice
versa. Relation directions: accepting `broader` means the concept is narrower than the
Term (concept ⊑ Term), `narrower` means Term ⊑ concept, `related` is `skos:related`.
`decided_by` is the signed-in user's email with accounts on. With accounts off it is the
tailnet login when the request came through a configured tailnet hostname, else `local`:
attribution for the record, not authentication (a local process can send any Host and
login header).

- **Overlay** (`engine/overlay.py`). A decided concept's `alignments` row carries the
  decision: relation and Term from the decision, `accepted` for an accept, `none` for a
  reject, rationale `human decision: <note>`. The judge's verdict moves to the row's
  `judged_json` and comes back when the decision is removed. The align judge step checks
  for a decision before the resume and cache paths, so a decided concept is never judged
  again (its Term must still exist; otherwise it is judged as usual).
  A `concept/acknowledge` decision (**Keep separate**) is laid over as a rejection
  (`overlay.alignment_decisions`): `none`, never accepted, rationale `kept separate by
  decision`, on the fresh, cached, resumed and apply paths alike.
- **Synonyms upstream.** The align apply step writes the synonyms of a person's accepted
  `same` to the Term whatever its confidence. When a decision rejects or removes a fold
  whose synonyms were already written, the same step takes them off the Term
  (`TermWriter.remove_synonyms`, then `mark_aliases_removed`; the record stays, so the
  alias stays untrusted). It removes the recorded additions the rejected rows account for
  that no accepted `same` of any documents source still accounts for, whichever source
  wrote them. A synonym another fold still protects is handed over to that fold (its row's
  `applied` lists it under `supported`, and `term_alias_additions` names its source), so
  rejecting that fold or removing its source later removes it. Rejection and `source rm`
  share this one ownership model (`engine/align.py`: `alias_supporters`,
  `departing_aliases`, `hand_over_aliases`): the alias log records what AutoOntology wrote,
  and who still supports a synonym is derived from the accepted folds of every documents
  source when it is about to come off. A synonym AutoOntology never wrote stays. So the
  Term's `skos:altLabel` and the concept's own class never both carry the name in RDF,
  Ossie or OKF. This runs in a build's align pass and in apply. What to take off is read
  from the rows themselves (`overlay.unfold_pending`): a row keeps the `applied` record of
  its upstream write until the removal succeeds, so a failed removal or a crash between
  judge and apply is completed by the next apply or `build --resume`. The decision counts
  as applied only after that.
- **Model and exports.** An accepted `related`/`narrower`/`broader` row becomes an
  `OAxiom(accepted=True)`, which makes the Term `aligned`; a decided one also has
  `decided=True`. Every alignment axiom carries a status, `proposed`, `accepted` (by the
  judge) or `decided` (by a person):
  - RDF: `ao:alignmentStatus` on the reified statement;
  - OKF: the `is related to` / `is a kind of` lines;
  - Ossie: the `related_to` description.
  A decided axiom's rationale is the decision note followed by the judge's rationale. An
  accepted alias moves from `rejected_aliases` to the Term's `alt_labels`.
  `MergedOntology.decisions` lists the reflected decisions, and OKF marks those concepts
  `stable`.
- **Apply** (`engine/apply.py`, run kind `apply`): `decisions -> merge -> snapshot`. It
  writes the decided rows and restores the rows whose decision was removed, adds and
  removes synonyms upstream as above, then merges and saves a revision. It needs the
  backend but never a model or the LLM proxy. It holds the workspace lease, and build,
  export and apply refuse to start while another of them is active. Every snapshot step
  (build or apply) sets `applied_revision` on the pending decisions the model reflects.

  Apply writes no revision when nothing is pending (`pending_work`: no unapplied
  decision, no removed one). In that case merge and snapshot are recorded `skipped` with
  reason `no pending decisions`, the run succeeds, and `applied_revision` is the latest
  revision. `--force` / `{"force": true}` writes one anyway. A scheduled run therefore adds
  no empty revisions:

  ```
  # crontab: every night, rebuild what changed, then reflect the review decisions
  30 2 * * * cd /path/to/AutoOntology && uv run autoontology build --resume && uv run autoontology apply
  ```
- **Review inbox** (`web/review.py`, `/api/review`). Items come from three places:
  - the alignment rows: `al:` refused `same` proposals and `ax:` judge axioms with
    confidence >= 0.6; also `fold:` automatic folds (accepted `same`), listed only with
    `category=folds` and never counted in the badge total;
  - the latest model: `un:` unaligned document classes (new since the previous revision
    first) and `alias:` rejected aliases. An alias decision that was applied and then
    removed is listed as pending again.
  - the freshness cache: `src:` stale sources.

  Ids are stable. A decided item that is no longer current is rebuilt from the copy its
  decision stored. Freshness is computed only for `category=stale_sources` or
  `?sources=1`; every other listing and `/api/review/pending-count` read only the cache.
- **Diff** (`web/revisions.py`, `/api/ontology/diff`). The default `from` is the newest
  revision older than `to`. Nodes are keyed by model local. A node counts as changed when
  its label, kind, definitions, synonyms, tables or relation/property counts differ.
  Axioms and merges are diffed by their ends (named in `subject_name`/`object_name`/
  `term_name`). Each list holds at most 500 entries; `totals` counts all of them.
- **Concept decisions** (`web/concept_decisions.py`). The concept detail
  (`/api/ontology/concepts/{id}`) lists the current decisions about the concept: a
  document concept's alignment, fold and acknowledge decisions, a Term's alias decisions,
  each with `decided_by`, `decided_at`, `applied_revision` and its review `item_id`.
- **Freshness** (`web/freshness.py`, `/api/sources/freshness`, 60 s cache). For each
  source, the current `input_fingerprint` is compared with the `inputs_sha` of its newest
  succeeded build step. When they differ, the reasons say what changed: files compared
  with the stored documents (path, sha256) or the load manifest, the database schema, or
  the reader settings. A source with no recorded build is `never_built`. Unreadable
  inputs or an unreachable database give `unknown` with the error class (a database is
  read once). The computation runs outside the cache lock, one at a time: a caller that
  arrives meanwhile waits for it and shares its result.

### Playground (`autoontology/playground`)

The scope is always the latest snapshot. The retrieval index (`elements.py`, `index.py`)
embeds one text per Term, concept, rule, process, relation, axiom and evidence node. It is
rebuilt when the snapshot changes. Chat (`service.py`) retrieves the top-k elements,
quotes them inside a delimited context block and streams the answer with citations. SQL
is relayed to the engine, whose text-to-SQL is a ReAct agent since 1.3 (steps
`react_sql_agent`, then `format_and_respond` or `unconstructable_sql_response`; up to
`TEXT_TO_SQL_REACT_MAX_TOOL_CALLS` tool calls per question, each one chat call through
the `/v1` router; the engine's default is 24, AutoOntology exports
`AUTOONTOLOGY_SQL_MAX_TOOL_CALLS`, default 12, unless the user set the engine variable).
At most `AUTOONTOLOGY_SQL_CONCURRENCY` (default 2) SQL streams run at once (429
`sql_busy`); a slot is released when the stream ends or the client leaves. The SSE events keep the `step` / `sql` / `result` /
`error` shape; an answer that needs no query has `sql_code: ""` and
`sql_response_from_db: null`. SPARQL (`sparql.py`) evaluates against the snapshot's Turtle
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
| `schema_version` | Migration level (v1: base schema, v2: snapshots and workspace-scoped playground, v3: `term_alias_additions.source_id`, v4: `term_legacy_descriptions`, v5: `documents.reader_fingerprint`, v6: `review_decisions` and `alignments.judged_json`, v7: `agent_tokens`, `agent_calls`, v8: `projects`, `store_meta` and `project_id` on the tables below, v9: the account tables and `agent_tokens.created_by`). |
| `projects` | Projects: id, slug, name, description, created and archived times. |
| `sources` | Registered sources (unique per project): name, kind (`files`, `database`, `documents`), locator, catalog database id. |
| `source_leases` | Leases that keep builds, exports and deletes from overlapping. |
| `runs`, `steps` | Build, export and apply runs and their steps, with details, timings and `inputs_sha`. |
| `file_manifests`, `semantic_baselines` | Input manifests and the catalog schema a semantic layer was compiled from. |
| `documents`, `chunks` | Read documents (sha256, reader) and their chunks. |
| `concepts`, `relations`, `evidence` | Extracted and consolidated document knowledge. |
| `term_embeddings` | Cached Term passage embeddings, by text hash and model. |
| `alignments`, `alignment_previews` | Judge verdicts with the identity policy version, and dry-run proposals. |
| `term_alias_additions`, `term_refresh_pending` | Synonyms AutoOntology added to Terms (with `source_id`, the documents source whose alignment added them, since v3; NULL on older rows), and Terms awaiting a refresh. `source rm` of a documents source removes that source's additions from the upstream Terms first. |
| `ontology_snapshots` | One row per revision of a project (surrogate `id`, per-project `revision`): the run, sources, counts and the full model JSON. |
| `review_decisions` | A person's decision per review subject (alignment, alias, concept), who made it, and the revision that first reflected it (`applied_revision`). |
| `playground_index`, `playground_index_meta` | The element vectors and the snapshot they were built from. |
| `playground_conversations`, `playground_messages` | Stored chats, keyed by project. |
| `agent_tokens`, `agent_calls` | Outside-agent tokens (SHA-256 only) of a project, and their calls. `agent_tokens.created_by` (v9) is the user who created or last rotated the token (NULL for older tokens and the CLI's). |
| `users` | Accounts: email (unique, case-insensitive, stored lower-cased), name, PBKDF2 `pw_hash` (NULL until a password is set), `is_admin`, `disabled_at`, `last_login_at`. |
| `sessions` | Sign-in sessions: the SHA-256 of the cookie id, user, created/expires/last-seen times (last seen written at most once a minute), IP and user agent. |
| `login_tokens` | Single-use links (`magic`, `invite`, `reset`): SHA-256 only, expiry and `used_at`. |
| `memberships` | A user's role in a project (`owner`, `editor`, `reviewer`, `viewer`); a project keeps at least one owner. |
| `audit_log` | Who did what: time, user (NULL for the CLI), project, action, target, JSON detail and client IP. No foreign keys. |

The engine keeps its own state in Postgres: the catalog and semantic model tables in the
`autoontology` database, the `vdb.*` vector tables, and each file source's data in
`autoontology_src_<name>` (or `autoontology_src_<slug_>__<name>`).
`config.validate_pg_database_name` refuses every other
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

## Web UI (`ui/`)

A React app (Vite, TanStack Query, shadcn/ui) built into `autoontology/web/static/dist`
and served under `/ui/`; every non-asset `/ui/*` path gets `index.html`. `ui/src/lib/api.ts`
is the only client of `/api`.

- **Navigation** (`components/nav.ts`): the project switcher
  (`components/project-switcher.tsx`), then Overview (`/`), Review (`/review`), Sources,
  Build, Ontology, Graph, Playground, Exports, Connect, Status, Settings; every project page
  lives under `/ui/p/<slug>/...`. An entry with a `permission` is shown only to callers who
  have it (with accounts on, Settings and Users are admin-only); Members and Audit log are
  reached from the project switcher and the projects page. The Review entry carries the
  pending count (`GET /api/review/pending-count`, polled every 30 s and refreshed after a
  decision or a run). The first-run checklist (`pages/home.tsx`) is `/setup`, reachable from
  Settings and the command menu; `/` (`pages/overview.tsx::IndexPage`) renders it while
  `/api/ontology` answers 404 `no_snapshot` and the dashboard once a snapshot exists. The
  last settled answer decides while the snapshot refetches, so the page does not flicker.
- **Overview** (`pages/overview.tsx`): five cards over `/api/ontology`, `/api/ontology/diff`
  (latest against the one before), `/api/ontology/revisions`, `/api/review?size=5`,
  `/api/sources/freshness`, `/api/runs` and `/api/exports`, polled every 30 s; the end of
  an active run (SSE) invalidates them (`hooks/queries.ts::invalidateAfterRun`). The
  notices are computed in `lib/ops.ts::opsNotices` (pure, unit-tested).
- **Review** (`pages/review.tsx`, `components/review-card.tsx`, `hooks/use-review.ts`):
  one category per tab, 20 items per page, category/status/page in the URL. A decision
  patches the cached item in place (badge and Undo stay where the card was) and refreshes
  the counts, not the list, so the list does not jump; the list is fetched again on a tab,
  filter or page change. The apply banner (`components/apply-banner.tsx`) starts
  `POST /api/runs {kind: "apply"}`, polls the run every second and invalidates every view
  when it ends.
- **Ontology** (`pages/ontology.tsx`, `components/revision-changes.tsx`): `?revision=`
  reads an older snapshot (summary, concepts and the concept sheet); `?changed=1` shows the
  diff of the shown revision against the one before it instead of the concept list.

- **Accounts** (accounts mode only): `pages/login.tsx` (`/ui/login`, back to the page
  asked for), `pages/invite.tsx` (`/ui/invite`, reads the token from the fragment and drops
  it from the history), the account menu in the header, `pages/members.tsx` and
  `pages/audit.tsx` (`/ui/p/<slug>/members|audit`, owners), `pages/users.tsx`
  (`/ui/users`, `/ui/users/audit`, admins). `lib/auth.ts` holds `useMe`, `useCan` and the
  role labels; a 401 sends the browser to sign-in and a 403 `forbidden` shows a toast
  naming the permission. With accounts off none of this renders.

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
  Ontology/Graph/Playground/Exports/Status strings, and `en.ops.ts`, the Overview, Review
  and apply strings) and `ko.ts` is typed as
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
