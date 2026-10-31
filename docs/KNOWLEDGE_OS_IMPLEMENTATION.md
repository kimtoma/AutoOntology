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
| P0-02 | Immutable originals and SourceRevision; PublicationManifest and UsageTrace; managed restore; failure/restart/backup acceptance | In progress: original/input-history portion implemented with schema v7, preserved file inputs, catalog schema revisions, API/UI/CLI retrieval and matching-byte legacy recovery. Python 1222 passed / 66 integration skipped; UI 161 passed, lint/build passed. Real r8 originals: all 6 CLI/API/browser downloads matched their hashes; all 29 pre-existing data tables and 421 export files matched the prior checkpoint. PublicationManifest is now implemented with atomic head/run/receipt commit, preserved originals/input hashes, frozen validation vectors and export hashes, shared UI/API/CLI verification, and failure/process-death coverage. Full Python regression: 1236 passed / 66 integration skipped; final related checks: 32 passed. UI: 163 passed, lint/build passed. A fresh Korean-document installation used real Codex and qwen3-embedding:4b for publication and completed chat, verified the same receipt via CLI/API/browser, and restored the committed publication plus originals into a separate workspace with matching receipt and file hashes. UsageTrace: full Python regression 1256 passed / 66 integration skipped, final related checks 56 passed, UI 165 passed with lint/build. Implemented exact request context, query vectors/index fingerprints, publication/model binding, distinct terminal states, CLI/UI history, deletion and process-death/backup coverage. Real Codex/Ollama chat, retrieval, SQL customer count (40), SPARQL and browser receipts passed in an isolated restored cluster; a real manifested-publication query and identical trace backup restore also passed. Managed knowledge restoration now prepares a durable, expiring packet and republishes the target as a new atomic version; UI/CLI confirmation, actual field diffs, frozen source mappings, idempotent retry, process-death, failure and expiry guards are implemented. Real legacy r8 -> amended r9 -> restored r10 acceptance used Codex/Ollama, browser confirmation, completed chat, exact SQL count 40, graph/index/download and idempotent CLI retry; manifested-target r2 -> amended r3 -> restored r4 also passed. Restoration validation: full Python regression 1278 passed / 66 integration skipped; UI 168 passed with lint/build. The final review packet includes actual before/after values and pinned originals; cooperative cancellation is also available through the native execution CLI. Embedding identity now binds the read-only Ollama manifest digest and retrieval-input adapter hash; guards detect same-tag replacement before/after calls, invalidate publication/restore validation, and prevent incompatible or unrecorded passage reuse while preserving legacy rows. Full Python regression: 1295 passed / 66 integration skipped; 17 artifact-focused checks passed. Actual Codex/Ollama publication and completed answer in an isolated copied workspace carried identical model identities in CLI, publication and usage receipts; a controlled read-only proxy simulated digest drift and blocked queries without changing the shared models, then reads resumed when the original digest returned. Browser receipt inspection and backup recovery matched. Document processing receipts now capture original/chunk inputs, reader/chunk configuration, requested and observed model settings, schema/prompt/code hashes, application backend calls and validated outputs. Settings changes invalidate extraction including resume; earlier results survive replacement/deletion and failures remain pending. Snapshots pin records, publication tokens bind the input metadata, manifests inherit frozen parent input bindings and parent publication IDs, and historical readers are never inferred from matching live bytes. Native CLI/API record inspection and a human-readable UI summary are implemented. Real Korean-document Codex/Ollama build -> first publication r2 -> same-byte JSON-mode re-extraction -> reviewed publication r4 verified both modes, old publication integrity, completed answers, CLI/API equality, light-mode browser inspection and identical processing/publication/original/usage backup recovery. Final Python regression: 1313 passed / 66 integration skipped; final processing/publication/restoration checks: 53 passed; UI 169 passed with lint/build. Structured-engine processing lineage, historical unknowns, runtime/weight archive limitations and full-stage acceptance still need further work. SQL improvements are tracked in the following row. |
| P0-02 / SQL | Operational SQL catalog, managed bounded reads and result recovery through UI/CLI | Implemented locally; full Python regression 1366 passed / 66 integration skipped; final related checks 145 passed. UI 175 passed with build and lint (one pre-existing restoration purity warning). Actual Codex/Postgres count 40, Korean customer/contract aggregation against direct DB rows, candidate/unapproved-column isolation, new publication r12 and managed restoration r13 with identical historical model and matching graph/SQL publication passed in a copied workspace. Manifests hash the approved SQL projection and source target identity; legacy host/account unknowns stay visible. Result CSV uses terminal receipts without query/model replay; UI usage history and native `ops sql-result` recover the same bytes. Read cancellation and unavailable-field paths are tested; final flow captures and checkpoint evidence are recorded in the private stage index. Modern target remapping, authenticated principals, structured-engine processing lineage and complete-stage acceptance remain pending. |
| P0-03 | Authenticated Principal, roles, workspace/domain scope; evidence/search/history/export/SQL enforcement; audit actors; SQL read-only limits | In progress: durable schema-v12 identity foundation plus initial individual HTTP/native CLI/browser sessions, explicit route/command policies, worker context propagation, verified review actors and atomic publication authority checks. Unfiltered resources require whole-workspace scope; normal-workspace enrollment remains off. Final Python regression 1498 passed / 66 integration skipped; UI 180 passed, build/lint passed with the pre-existing restoration purity warning. Actual copied-data browser login/error/progress/graph/logout/role-denial, native login/whoami/original listing and fixed-public-content Codex pong passed. Document retrieval stayed local; document-based external chat remains pending explicit user approval after automatic review rejection. Mixed-domain resource projection, full effects/stream/queue checks, administration UI/CLI completeness, exact approval lifecycle and local-owner enrollment/recovery remain required. See `IDENTITY_AUTHORITY.md` and private stage-10/11 evidence. |
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
- [ ] Old originals, citations, retrieval and questions remain reproducible after edits. Original bytes, publication manifests, exact question/retrieval traces and managed restore are implemented and tested; structured-engine processing/chat configuration and runtime/weight archives still need broader verification. SQL catalogs and terminal result files now bind to operational publications; historical live rows remain unarchived.
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

## Stage change records

Every subsequent stage records its concrete changes, test evidence, remaining work
and local recovery commit. When UI behavior changes, preserve the actual light-mode
entry, progress, result and relevant confirmation/error/recovery flow. Follow
[the stage evidence contract](STAGE_EVIDENCE.md); the private runtime
`changes/index.html` links each stage to its detailed report, images and hashes.
Do not treat a screenshot alone as runtime or authority verification.

### Stage 10: identity-policy foundation

Parent `3265467` (functional application reference `6896be4`). This stage adds the
shared policy and safe credential storage modules; it does not change UI behavior,
publish the app, enroll the normal workspace or introduce a partial anonymous
enrollment endpoint. UI captures are not applicable to this foundation. Native
identity administration, authenticated business commands and the equivalent UI
remain the next integration work, with the activation gates documented in
[the identity contract](IDENTITY_AUTHORITY.md).

Executed focused checks: 86 identity/credential tests passed. A private copy of
the actual local schema-v11 SQLite store migrated to v12, enrolled a fixture
administrator, exercised agent scope/spoofing/revocation, verified its append-only
audit and restored the same identity state from a backup. Existing 39 application
tables/rows, published r8 and all 421 exports were preserved; the 11 public app
assets remained unchanged. This acceptance called no model and did not test HTTP
or UI authorization. Private evidence: `verification/identity-core-acceptance.json`.

The first full regression found a pre-existing AGENTS/CLAUDE instruction-copy drift
from the stage-09 evidence rule: 1452 tests passed, 66 skipped and one failed.
The instruction files were synchronized; final full regression passed **1453 tests**
with **66 integration tests skipped**. Ruff check/format passed. The stage-10
checkpoint/drill is recorded with the tested local commit in the private stage
index. Restoration must use that data checkpoint as well as its source commit.
Current identity is not activated on the normal workspace; no full authenticated
product or Enterprise AX savings is claimed by this partial P0-03 stage.

## Recovery procedure

### Stage 11: initial individual-session adapters

Parent `819fa40`; data schema remains v12. Browser login uses HttpOnly cookies and
verified session state; CLI login writes a private origin/workspace-bound file and
never prints its token. Unknown business APIs/commands deny access when configured;
the inventory tests cover 85 OpenAPI operations and 59 guarded native business
commands plus the explicit version/identity boundaries. Worker identity is copied
and common review/check/feedback/publication effects recheck inside their write
transaction. Policy/role/credential drift during publication preparation prevents
the operational switch. A separate loopback model key grants no business authority.

Final full Python regression: **1498 passed / 66 skipped**. UI: **180 passed**,
build/lint passed (one existing `restoration.tsx` purity warning). Controlled model
tests cover attributable publication and revocation; they are not real document
model acceptance. Actual private browser/native acceptance used the existing r8
data copy and real credentials. The real Codex call contained only the fixed public
provider-probe sentence. Local Ollama prepared a document-based input for review;
it was frozen and not sent after automatic approval review rejected that specific
transmission. Its approval request is separate from the completed tests.

Eight final light-mode captures record entry, invalid login, in-progress login,
neutral operational loading, completed operations home, graph, confirmed logout and
role denial. The earlier seven captures and loading issue remain as labeled baseline
evidence. First-install guidance now appears only after reads establish that no
operational publication exists. Private reports and source/capture hashes are linked
from `changes/stage-11-authority-adapters/index.html`; its tested commit and private
checkpoint/drill are recorded in the stage index.

The normal workspace remains individually unconfigured. This milestone does not
complete P0-03, activate organization-wide domain access or finish the P0-P3 roadmap.
Remaining gates are in `IDENTITY_AUTHORITY.md`. No Enterprise AX savings measurement
or public deployment is claimed.

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
Managed publication restoration and failure injection are implemented; broader P0-02 structured lineage, target-remapping, authenticated SQL scope and recovery acceptance remain incomplete.

The initial `stage-00-baseline` attempt is superseded by `stage-00-baseline-v2`;
use the verified v2 checkpoint. The older attempt exposed the SQLite read-only
WAL-sidecar issue and is not a recovery target.
