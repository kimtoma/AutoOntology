# Knowledge OS implementation and recovery ledger

The complete scope is the lifecycle roadmap dated 2026-10-30, not just the
first publication fix. Implementation stays on `codex/ontology-ops-local`.
The public checkout, service and Cloudflare configuration are out of scope.
Each stage needs a tested local commit and a private data checkpoint. A code
checkout alone cannot undo a database migration or recover an overwritten file.

The 2026-10-31 direction adds cross-cutting requirements: all UI work must also
be available through the CLI; human operation is the default; scoped agents
prepare the work and humans confirm consequential decisions. Enterprise AX is
measured primarily by reduced end-to-end lead time and human active effort.
See [the CLI, delegation and time-outcome contract](CLI_AGENT_AX_CONTRACT.md).

## Architecture and invariants

Use the existing SQLite store for operational metadata and a persistent local
queue; retain Postgres, Ollama and the unmodified upstream engine. Keep a single
workspace first, with explicit workspace/domain/principal identifiers that can
later support multiple organizations. Store immutable original files separately
from mutable ingestion rows. A publication binds source revisions, reviewed
changes, evaluation, model configuration, retrieval index and export hashes.
Readers consume a publication; builders and reviewers consume candidates.
Slow preparation happens before a transactional publication switch.

Authorization must cover direct APIs, historical evidence, search, SQL and
downloads as well as navigation. Queue collection must never imply publication.
External writes require an independently authorized, audited action with an
idempotency key and compensation state. No external messages are sent as part of
implementing this roadmap.

This favors a small local persistent system over distributed infrastructure.
Revisit SQLite concurrency, index storage and task isolation after measured load
shows a need; do not assume organization-wide throughput without measurement.

## Stages and acceptance evidence

Status is incomplete unless the acceptance scenario has been executed. Tests
with fake models and browser response fixtures are labeled separately from real
model, source and browser checks.

| Stage | Scope | Status / evidence |
| --- | --- | --- |
| Baseline | Stable initial route; shared operational reads; explicit private preview exports | Completed `a723554`: 297 related Python / 154 UI tests; local r8/r7 contract and browser verification |
| Recovery | Private stage checkpoint, hashes, restoration instructions and restore drill | Tooling `8aa9ee0`, WAL consolidation/immutable verification `a7c4262`; `stage-00-baseline-v2` restored on isolated port 55434: catalog 37 tables/168 rows and source 3 tables/155 rows matched, SQLite integrity and published r8 preserved |
| CLI / Agent / AX | Complete semantic CLI parity; authenticated agent preparation and human confirmation; time-to-approved-result and human-effort baseline/targets | Contract recorded; current `ops` CLI is only partial coverage. Implementation and capability-by-capability acceptance remain required across all stages. |
| P0-01 | Explicit first approval, mandatory representative question suite, failed validation, real initial-install acceptance including missing login/backend | Implemented; 1205 Python tests passed / 66 integration tests skipped; UI 158 passed, lint/build passed. New Postgres 55435 / app 7831 / backend 3132 acceptance used real Codex and qwen3-embedding:4b on Korean policy text, checked first publication r2, graph/index/download and browser operations home. Missing-login/backend cases use controlled route tests without logging the user out. Full Python run used process/port permissions and a verified same-commit read-only mapping to the main checkout's upstream source. |
| P0-02 | Immutable originals and SourceRevision; PublicationManifest and UsageTrace; managed restore; failure/restart/backup acceptance | In progress: original/input-history portion implemented with schema v7, preserved file inputs, catalog schema revisions, API/UI/CLI retrieval and matching-byte legacy recovery. Python 1222 passed / 66 integration skipped; UI 161 passed, lint/build passed. Real r8 originals: all 6 CLI/API/browser downloads matched their hashes; all 29 pre-existing data tables and 421 export files matched the prior checkpoint. PublicationManifest is now implemented with atomic head/run/receipt commit, preserved originals/input hashes, frozen validation vectors and export hashes, shared UI/API/CLI verification, and failure/process-death coverage. Full Python regression: 1236 passed / 66 integration skipped; final related checks: 32 passed. UI: 163 passed, lint/build passed. A fresh Korean-document installation used real Codex and qwen3-embedding:4b for publication and completed chat, verified the same receipt via CLI/API/browser, and restored the committed publication plus originals into a separate workspace with matching receipt and file hashes. UsageTrace: full Python regression 1256 passed / 66 integration skipped, final related checks 56 passed, UI 165 passed with lint/build. Implemented exact request context, query vectors/index fingerprints, publication/model binding, distinct terminal states, CLI/UI history, deletion and process-death/backup coverage. Real Codex/Ollama chat, retrieval, SQL customer count (40), SPARQL and browser receipts passed in an isolated restored cluster; a real manifested-publication query and identical trace backup restore also passed. Managed knowledge restoration now prepares a durable, expiring packet and republishes the target as a new atomic version; UI/CLI confirmation, actual field diffs, frozen source mappings, idempotent retry, process-death, failure and expiry guards are implemented. Real legacy r8 -> amended r9 -> restored r10 acceptance used Codex/Ollama, browser confirmation, completed chat, exact SQL count 40, graph/index/download and idempotent CLI retry; manifested-target r2 -> amended r3 -> restored r4 also passed. Restoration validation: full Python regression 1278 passed / 66 integration skipped; UI 168 passed with lint/build. The final review packet includes actual before/after values and pinned originals; cooperative cancellation is also available through the native execution CLI. Embedding identity now binds the read-only Ollama manifest digest and retrieval-input adapter hash; guards detect same-tag replacement before/after calls, invalidate publication/restore validation, and prevent incompatible or unrecorded passage reuse while preserving legacy rows. Full Python regression: 1295 passed / 66 integration skipped; 17 artifact-focused checks passed. Actual Codex/Ollama publication and completed answer in an isolated copied workspace carried identical model identities in CLI, publication and usage receipts; a controlled read-only proxy simulated digest drift and blocked queries without changing the shared models, then reads resumed when the original digest returned. Browser receipt inspection and backup recovery matched. Document processing receipts now capture original/chunk inputs, reader/chunk configuration, requested and observed model settings, schema/prompt/code hashes, application backend calls and validated outputs. Settings changes invalidate extraction including resume; earlier results survive replacement/deletion and failures remain pending. Snapshots pin records, publication tokens bind the input metadata, manifests inherit frozen parent input bindings and parent publication IDs, and historical readers are never inferred from matching live bytes. Native CLI/API record inspection and a human-readable UI summary are implemented. Real Korean-document Codex/Ollama build -> first publication r2 -> same-byte JSON-mode re-extraction -> reviewed publication r4 verified both modes, old publication integrity, completed answers, CLI/API equality, light-mode browser inspection and identical processing/publication/original/usage backup recovery. Final Python regression: 1313 passed / 66 integration skipped; final processing/publication/restoration checks: 53 passed; UI 169 passed with lint/build. Structured-engine processing lineage, historical unknowns, runtime/weight archive limitations, semantic SQL catalog scope and full-stage acceptance still need further work. |
| P0-03 | Authenticated Principal, roles, workspace/domain scope; evidence/search/history/export/SQL enforcement; audit actors; SQL read-only limits | Pending |
| P1-01 | ChangeSet/ReviewCase, dependencies, base publication, owner/due/priority/reasons, stale-base warning, append-only transitions, connection-review links | Pending |
| P1-02 | Contract/billing rule scope, amount/operator, approval authority, effective dates, exceptions/conflicts; durable identity rename/merge/split; extraction quality and corrections; versioned deterministic evaluation and citation support | Pending |
| P1-03 | Answer → feedback → owner → change → evaluation → resolution publication; reopening; workload/overdue/evidence/failure/impact metrics | Pending |
| P2-01 | Folder/Postgres schedules, durable deduplicated queue, retries/backoff/terminal failure, restart/resume, owner-linked notification outbox | Pending |
| P2-02 | Connector auth/scope/delete/move/duplicate/failure/cursor contract; separate DB schema and row changes; one document-repository pilot | Pending |
| P3-01 | Contract/billing, HR, security domain catalog; ownership, sources/publication, shared/conflicting concept mappings and authorization | Pending |
| P3-02 | Versioned KnowledgeContract, consuming report/training/app/agent registry, compatibility/evaluation rules, question/metric/workflow impact, authorized reads/events | Pending |
| P3-03 | Knowledge-based proposals/drafts/simulation; separate action approval/permission/idempotency/compensation/audit; contract-approval draft pilot | Pending |

## End-to-end completion audit

- [x] Empty installation → validated and approved first publication → operations home (real isolated initial-install acceptance; login/backend error branches tested with fixtures).
- [ ] Default graph, answer, SQL and export use the same publication.
- [ ] Old originals, citations, retrieval and questions remain reproducible after edits. Original bytes, publication manifests, exact question/retrieval traces and managed restore are implemented and tested; extraction/chat configuration, runtime/weight archives and semantic SQL catalog binding still need broader verification.
- [ ] Failed preparation, restart, publication switch and managed restoration keep
      graph, index, answers and downloads consistent.
- [ ] Unprivileged direct API/search/file/history/SQL requests cannot bypass scope.
- [ ] Individual reviewers/publishers/restorers and roles are attributable.
- [ ] One policy amendment arrives as one assigned case, with evidence, effective
      date, questions and resolution publication; conflicting knowledge stays held.
- [ ] Repeated source events deduplicate; failures notify an owner; restart resumes;
      collection never automatically replaces the operational publication.
- [ ] One real consumer names the approved knowledge contract it uses.
- [ ] Contract approval draft action demonstrates permission, approval, duplicate
      suppression, failure compensation and audit without unauthorized external writes.
- [ ] Each stage has a local commit, recoverable data checkpoint and verified drill.
- [ ] Every UI task, including exceptions/recovery/approval, has a tested CLI equivalent with stable machine-readable results.
- [ ] An agent prepares a complete decision packet; an authenticated human confirms the exact effect; revocation/expiry/stale context and retries cannot bypass or duplicate approval.
- [ ] The contract/billing pilot has measured baseline and assisted end-to-end median/p90, human active time, correction burden and quality/permission guardrails; accountable owners set numeric reduction targets from those measurements.

## Recovery procedure

Checkpoint details and restore drills are recorded alongside private artifacts,
outside git. Never commit databases, dumps, environment files or login state.
Create a new recovery worktree at the recorded commit; do not reset the public
checkout. Restore data into a new local runtime first and inspect it before
replacing the working local runtime. Keep the pre-restore copy until the drill
passes. Postgres operations may address only `autoontology` and
`autoontology_src_<name>` databases on the isolated local cluster.

The original Korean lifecycle report remains at the private local runtime's
`ontology-lifecycle.html`; the complete dated roadmap remains at
`knowledge-os-roadmap.md`. This ledger tracks implementation and does not replace
or narrow their acceptance requirements.

### Concrete local checkpoint commands

Run from the isolated worktree, using its shared virtual environment. `create`
requires a clean committed tree, restricts itself to the named local runtime and
ports, checks that the cluster contains no unrelated databases, stops only the
isolated services, copies a cleanly stopped Postgres cluster, consolidates the
SQLite WAL with its backup API, and restarts the isolated services in `finally`.
The private checkpoint includes a source archive, original configuration and
file hashes. Its directory is mode 0700 and files are mode 0600; preserved
original-byte payloads retain owner-read-only mode 0400.

```sh
OPS_RUNTIME=/Users/macmini/Projects/AutoOntology/data/ontology-ops-local
.venv/bin/python scripts/local_ops_checkpoint.py create --runtime "$OPS_RUNTIME" --label stage-NN-name
.venv/bin/python scripts/local_ops_checkpoint.py verify "$OPS_RUNTIME/checkpoints/stage-NN-name"
.venv/bin/python scripts/local_ops_checkpoint.py stage "$OPS_RUNTIME/checkpoints/stage-NN-name" "$OPS_RUNTIME/verification/new-restore"
```

`stage` refuses existing destinations and verifies hashes before creating any
restored file. Do not use the original backup itself as a running cluster. A
Postgres drill must override both port and socket directory, for example
`pg_ctl -D NEW_RESTORE/postgres -o '-p 55434 -h 127.0.0.1 -k /private/tmp' ...`.
Deep workspace paths exceed macOS's Unix socket path limit. Stop the drill server
after checking it; the live local cluster stays on 55433.

To roll back the working preview after a successful drill, stop its app/backend
and Postgres, retain its current directories in a separate private recovery
folder, then copy the verified `workspace`, `exports`, `postgres` and `local.env`
back to the original runtime paths. Switch **only the clean isolated worktree**
to the manifest's commit (a detached checkout preserves later branch history).
Start the local Postgres and `control.py start`, then check health, publication,
graph, retrieval and downloads before deleting any pre-restore copy. This
destructive-in-place step is not performed during checkpoint creation or drills.
Managed publication restoration and failure injection are implemented; broader P0-02 lineage, SQL-scope and recovery acceptance remain incomplete.

The initial `stage-00-baseline` attempt is superseded by `stage-00-baseline-v2`;
use the verified v2 checkpoint. The older attempt exposed the SQLite read-only
WAL-sidecar issue and is not a recovery target.
