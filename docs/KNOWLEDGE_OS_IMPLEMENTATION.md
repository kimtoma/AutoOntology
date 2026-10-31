# Knowledge OS implementation and recovery ledger

The complete scope is the lifecycle roadmap dated 2026-10-30, not just the
first publication fix. Implementation stays on `codex/ontology-ops-local`.
The public checkout, service and Cloudflare configuration are out of scope.
Each stage needs a tested local commit and a private data checkpoint. A code
checkout alone cannot undo a database migration or recover an overwritten file.

The 2026-10-31 direction adds cross-cutting requirements: all UI work must also
be available through the CLI; human operation is the default; scoped agents
prepare the work and humans confirm consequential decisions, with progressive
policy-authorized autonomy after evidence accumulates. Enterprise AX improves the
whole knowledge system; end-to-end lead time and human active effort support
that outcome alongside freshness, consistency, reuse and decision quality.
See [the CLI, delegation and time-outcome contract](CLI_AGENT_AX_CONTRACT.md).

The user set a target of **2026-10-31 20:00 Asia/Seoul** for the active complete
roadmap goal. This is a delivery constraint, not permission to omit acceptance,
collapse scope, fabricate measured time savings or mark pending stages complete.
Prioritize implementation, meaningful verification and recoverable local commits;
record any remaining scope explicitly at the deadline. Public deployment remains
out of scope.

## Clarified operating goal

Organizational knowledge-system efficiency is the primary outcome; time includes
UI/CLI waiting and the knowledge/decision/reuse cycle. Initial human validation
should develop into automatically recorded decisions, a continuous agent loop and
explicit bounded autonomy with human exceptions. The user has no measured
baseline yet. See [the current operating model](KNOWLEDGE_OS_OPERATING_MODEL.md).
Supporting timer instrumentation must not dominate the operations UI or imply
actual savings before measurement. These directions refine the remaining full
roadmap rather than a completed feature claim.

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
| P0-03 | Authenticated Principal, roles, workspace/domain scope; evidence/search/history/export/SQL enforcement; audit actors; SQL read-only limits | In progress: durable schema-v12 identity foundation plus initial individual HTTP/native CLI/browser sessions, explicit route/command policies, worker context propagation, verified review actors and atomic publication authority checks. Unfiltered resources require whole-workspace scope; normal-workspace enrollment remains off. Final Python regression 1498 passed / 66 integration skipped; UI 180 passed, build/lint passed with the pre-existing restoration purity warning. Actual copied-data browser login/error/progress/graph/logout/role-denial, native login/whoami/original listing and fixed-public-content Codex pong passed. The subsequent stage adds administration UI/CLI and verified usage actors, with Python 1504 / UI 183 passing. After explicit approval, exact-message document chat completed through actual Codex with a verified actor and matching E1 citation; UI history recovered it without a new model call. Mixed-domain resource projection, full effects/stream/queue checks, remote-session/renewal support, exact approval lifecycle and local-owner enrollment/recovery remain required. See `IDENTITY_AUTHORITY.md` and private stage-10/11/12 evidence. |
| P1-01 | ChangeSet/ReviewCase, dependencies, base publication, owner/due/priority/reasons, stale-base warning, append-only transitions, connection-review links | In progress: Stage 17 adds shared UI/native business cases, structural dependency-complete exact human review packets, atomic review batches, immutable receipts, stale/expiry/policy guards, case relations and publication-bound resolution/reopen. Connection-review packet integration, effective dates, linked evaluations, consumer impact and full-stage acceptance remain. See BUSINESS_CASES.md and private stage evidence. |
| P1-02 | Contract/billing rule scope, amount/operator, approval authority, effective dates, exceptions/conflicts; durable identity rename/merge/split; extraction quality and corrections; versioned deterministic evaluation and citation support | In progress: Stage 18 adds explicit human-reviewed monetary rule fields, amount/date/authority conflicts, immutable evaluation suite versions, deterministic model/answer criteria, shared publication/restoration checks, preserved runs and UI/native parity. Automatic typed extraction, semantic support judgments, merge/split and remaining acceptance stay pending. See RULE_EVALUATION.md. |
| P1-03 | Answer → feedback → owner → change → evaluation → resolution publication; reopening; workload/overdue/evidence/failure/impact metrics | In progress: Stage 19 links feedback/owner/business context/effective dates/exact required suites to case confirmation and outcomes, adds explicit no-change dispositions and atomic linked-request reopening, plus transparent human intervals, coverage attestation, provenance-separated baselines, owner targets and cohort median/p90. Real business measurements, failure/recovery phase attribution, correction adjudication and consumer impact remain required. See AX_FEEDBACK.md. |
| P2-01 | Folder/Postgres schedules, durable deduplicated queue, retries/backoff/terminal failure, restart/resume, owner-linked notification outbox | Pending |
| P2-02 | Connector auth/scope/delete/move/duplicate/failure/cursor contract; separate DB schema and row changes; one document-repository pilot | Pending |
| P3-01 | Contract/billing, HR, security domain catalog; ownership, sources/publication, shared/conflicting concept mappings and authorization | Pending |
| P3-02 | Versioned KnowledgeContract, consuming report/training/app/agent registry, compatibility/evaluation rules, question/metric/workflow impact, authorized reads/events | Pending |
| P3-03 | Knowledge-based proposals/drafts/simulation; separate action approval/permission/idempotency/compensation/audit; contract-approval draft pilot | Pending |

Stage 12 extends P0-03 with shared administration UI/CLI and request attribution.
Python regression: 1504 passed / 66 integration skipped. UI: 183 passed with build
and lint (the existing restoration purity warning remains). Actual private-copy
UI/native checks cover current role changes, password reset, disable/re-enable
without session resurrection, source classification, preparation-only delegation,
revocation and audit verification. The user approved the frozen 2,292-character
document input; exact-message Codex calls completed, and the final receipt has a
verified individual actor and matching E1 evidence. A prior unverified-actor receipt
is preserved as pre-fix evidence. The completed answer can be opened from UI history
without a new model request. Normal-workspace enrollment stays off; domain-level
projections, remaining effect/stream/queue and approval/recovery gates are incomplete.

Stage 13 implements shared saved-request ownership, explicit administrator
inspection, owner-scoped retention/deletion and asynchronous provider/SQL revocation.
Schema v13 preserves historical receipt bytes/hashes and leaves unknown owners
unknown. Actual copied-data UI/CLI isolation, Postgres count 40/CSV recovery and
SSE session locking passed with local Ollama and controlled chat/planning. The UI
retains correct navigation when roles hide settings and when the sidebar collapses.
Synchronous-worker cancellation, per-domain knowledge projection and remaining
P0-P3 gates stay incomplete. Final regression and recovery evidence are recorded
in the private stage-13 index; no normal enrollment or public deployment is implied.

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

The [illustrated field-guide source](illustrated-guide/README.md) provides a
separate offline HTML explanation of the whole operating loop and technical
boundaries, using 27 MIT-licensed Hairline SVG figures. It connects the existing
lifecycle, statechart, roadmap and actual change records. Current, partial and
planned behavior is labeled; this documentation is not a new application release.

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

### Stage 12: administration and verified request attribution

Parent `b5ceac8`. `/ui/access` provides own-session management plus human-admin
people/roles/password/domain/source/delegation/session/audit operations. Native
administration, domain listing, session listing/revocation, password reset and
human re-enable complete the corresponding initial management operations. Both
interfaces share current service authorization and secret-free read models.
Re-enabling a person preserves old credential invalidation. Query receipts bind
verified execution identity at creation; existing receipts remain immutable.

Actual copied-data scenarios, stable light-mode captures, exact approved model
request evidence, full regression results and preservation/recovery checks are
linked from `changes/stage-12-identity-administration/index.html`. Captures taken
during earlier dialog/panel entry animations are retained separately and are not
the final visual evidence. This stage does not activate normal identity or complete
the organization-wide authority, exact approval lifecycle, full CLI contract or
P0-P3 roadmap. Its final commit and data checkpoint/drill are in the private index.

### Stage 13: private saved requests and asynchronous revocation

Parent `3141c37`. Common trace/conversation stores enforce current principal and
workspace for list/read/write/delete/continuation, with explicit administration
inspection and per-owner retention. The additive v13 migration does not rewrite
old receipt JSON or hashes or invent historical conversation ownership. Originating
producer leases permit failure cleanup after revocation without authorizing success.
HTTP streams monitor current authority while awaiting asynchronous provider/SQL
results; UI and native CLI preserve actionable revocation/recovery behavior.

Actual copied-data checks use real local Ollama retrieval, real copied Postgres
rows and controlled local chat/planning, clearly separated from previous actual
Codex semantic acceptance. Frozen source/model/vector fingerprint verification
allows reuse of the prior actual index, while legacy unverified vectors remain
preserved. Light-mode captures, initial attempts, final tests, source commit and
data checkpoint/drill are linked from `changes/stage-13-result-authority/index.html`.
All remaining scope in `IDENTITY_AUTHORITY.md` and the full roadmap still applies.
Final Python regression: **1515 passed / 66 skipped**. UI: **186 passed** with
build and lint; the existing restoration purity warning remains. The native
SSE error contract retains the terminal trace ID so a revoked session receives
an actionable failure without an unauthorized follow-up result read.

## Recovery procedure

### Stage 16: source and settings effect boundaries

Parent `e94648d`; schema remains v14. Source registration, locator changes, catalog
binding and local removal recheck current authority inside their write transaction
and append safe actor-attributed metadata atomically. Human source editing remains
separate from delegated catalog preparation. Structured registration pins its
preparation policy; source removal checks policy before teardown steps, polling,
database DROP and local deletion. A started backend teardown is not claimed to be
reversed after later revocation. Partial cross-service recovery remains required.

HTTP settings saves and native provider selection share one current-authority,
prepared-policy and stale-file guard. Participating same-workspace writers serialize
through SQLite; values and secret-bearing locators never enter audit metadata.
Private atomic file replacement and backups remain in use. This is not a crash-atomic
SQLite/filesystem transaction; durable cross-resource effect/consent recovery remains
an explicit activation gate. Other native configuration/setup paths still need the
final inventory. Effect-time CLI denials now use the stable entry-denial JSON contract.
HTTP source errors preserve authorization status instead of rewriting it as bad input.
Upload staging rechecks authority between chunks and cleans its own failed staging.

Loss of a modern identity table fails closed in both common effects and the internal
router probe. Genuine pre-identity stores remain explicitly legacy without being
migrated by the probe. Test isolation resets environment-origin metadata together
with its environment variables, preventing cross-test shell-origin contamination.

Copied-data native and HTTP acceptance registers/removes new private document sources,
saves settings without changing embeddings, verifies six secret-free attributable
effects and restores every table/audit plus six read-only originals. Actual browser
capture uses a controlled source-validation wait and real fixture-session revocation;
no external model is called. The normal workspace remains unenrolled and unchanged.
Private stage-16 evidence records final regression, captures and checkpoint/drill.
Host-path allowlisting, exact human consent, owner recovery, remaining source/job/effect
inventory, structured lineage and the rest of P0-P3 remain in scope.
Final Python regression: **1558 passed / 66 integration tests skipped**; final
authority-focused checks: **24 passed**. The copied native/HTTP and four-frame
actual browser acceptance passed. Ruff and format checks passed. The UI source and
committed bundle remain the tested Stage 15 version; no new UI build is required.

### Stage 15: query and durable saved-result knowledge scope

Parent `118ee63`; additive schema v14 stores immutable message-scope companions
without rewriting historical messages or receipt hashes. Shared query selection
covers retrieval/context, frozen-vector subset reuse, frozen SQL catalog projection,
SPARQL serialization/worker keys, receipt/conversation reads and CSV recovery.
Knowledge dependencies and current ownership are independent requirements. Scope
changes cancel asynchronous waits and prevent success; trace deletion cannot erase
message dependencies. UI/native domain query paths share these boundaries.

Actual copied-data acceptance uses local Ollama and copied PostgreSQL, with controlled
r9 source/SQL bindings and operational pointer plus fixed local chat/planning.
This separates access/lifecycle verification from semantic publication acceptance;
the legacy r8 source identities remain unknown. No external model input or normal
identity enrollment is added. Final test, capture, preservation and recovery evidence
is recorded in the private stage-15 index. Remaining source/effect/queue checks,
exact human approval, owner recovery, full CLI parity and P1-P3 remain in scope.
Python regression: **1541 passed / 66 integration tests skipped**. UI: **192 passed**,
lint/build passed with the existing restoration purity warning. The actual copied
workspace passed scoped local retrieval, completed controlled answer, PostgreSQL
count 40, matching browser/native CSV, SPARQL exclusion and live domain-move
cancellation/history denial. Six final light-mode captures include the explicit
authority-change notice; earlier attempts remain separately labeled.

### Stage 14: source-complete domain views

Parent `abab7cc`; schema remains v13. Shared read selection covers graph, concept,
original and generated view-download paths through HTTP and native CLI. Complete
mixed-source components require all contributing sources; old identity and domain
unknowns remain unknown. Configured publication receipts pin source authority,
while current classifications can further restrict historical reads. Projected
cache keys and browser policy epochs prevent reuse of a broader prior view.

The copied actual model/originals are exercised in a real browser and native CLI.
Its test-only r9 source binding and operational pointer are controlled fixtures,
not historical adoption, a new semantic build or publication-approval acceptance.
Normal r8 remains unchanged. The final report, source commit, light-mode flows,
preservation evidence and checkpoint/drill are in the private stage-14 index.
Query/index/search/SQL/SPARQL scope, broader history/source/queue/effect checks,
exact approval/recovery and P1-P3 remain implementation work. No AX saving or
complete organization isolation is claimed by this stage.
Final checks: **1531 Python tests passed / 66 integration tests skipped**; the
final related projection/publication checks passed 31 tests. **190 UI tests**,
lint and build passed; the existing restoration purity warning remains. Nine
final actual light-mode captures cover legacy unknowns, known scoped ontology,
evidence/original download, graph, generated files, mobile, collapsed navigation
and live policy cache removal. Configured-fixture backup preserves all 49 SQLite
tables, domains/roles/audit, owner/reader projections and six read-only originals.

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

### Stage 17: business cases and exact human review packets

The shared case service connects the operations home, review UI and eleven native
commands. Agents prepare coherent changes; a named authenticated human confirms
exact effects. Schema v15 adds cases, hash-chained events, immutable packets and
receipts, plus append-only direct review/undo events. See [the case workflow and
limits](BUSINESS_CASES.md). Real browser/native checks use a private copy with an
explicit synthetic semantic candidate, no model calls and no public deployment.
Final regression, captures and checkpoint/drill evidence belong in the private
stage-17 record. The full P0–P3 goal remains active; this is not an AX saving claim.

### Stage 18: typed rules and versioned evaluation

Human review records explicit monetary approval scope, threshold/operator,
authority, dates, exceptions and priority without changing source evidence. Typed
rule facts survive JSON format 2, retrieval, RDF and GraphML; untouched legacy
models retain format 1. Immutable suite versions have exact human activation,
agent-only preparation, stale-policy/base guards and immutable execution history.
Deterministic conflicts/assertions block before provider calls, and normal
publication plus managed restoration share the criteria. A final content guard
runs inside the atomic publication transaction. See [criteria and explicit
limits](RULE_EVALUATION.md). Private real browser/native acceptance uses an
archived fictitious Korean regulation and clearly controlled model adapters;
no private material is sent to an external model and no public deployment occurs.
The final stage record holds regression, images, checkpoint and recovery proof.

### Stage 19: feedback handoff, case context and AX records

Schema v17 adds immutable business-context versions, original feedback links,
human-owned timers and append-only timing events, effort coverage attestations,
observed/reported and business/simulation baselines, retained corrections and
owner target versions. Exact case packets bind context/evaluation versions and
accepted outcomes require those versions in their publication manifest. Explicit
no-change cases apply no knowledge decisions/publication; linked feedback closes
only against a usable outcome and reopens with the case. UI/native actions share
one service. See [measurement provenance, commands and remaining pilot work](AX_FEEDBACK.md).
Private browser acceptance uses a fictitious request, a clearly controlled snapshot
clone and simulated/reported data; it makes no real business-savings claim.
Regression, captures and data recovery proof belong in the private stage record.

## Stage 20 — approved host inputs and provider origin binding

Authenticated ingestion now checks explicit business roots and live source/domain authority before registration, hashing, extraction and original capture. UI and native administrator actions approve/revoke specific paths; protected secret paths and unsafe live links are refused. Revocation preserves archived evidence. The shared provider settings boundary binds saved keys to URL origins, with owner-only key-file replacement or explicit clearing in native CLI. See [host input boundaries](HOST_INPUT_BOUNDARIES.md). Whole-workspace recovery and configured acceptance evidence are recorded privately. Cross-resource recovery, durable agent collection and bounded automation policies remain pending.

## Stage 21 — time to useful UI data and native startup

The operations screen renders independent regions progressively, retains authorized
cache on refresh errors and defers detailed timing queries until expanded.
Unrelated slow responses no longer hide available case data. CLI metadata avoids
initializing ingestion dependencies. See [measurement protocol and observed
results](INTERACTION_PERFORMANCE.md); private flow captures and raw samples are
kept alongside the local checkpoint. Existing identity/policy cache disposal
remains mandatory. These are local interaction measurements, not measured
organizational time savings.
