# Human operation, complete CLI access, and delegated Enterprise AX

Product direction recorded 2026-10-31. This is an implementation contract for
the remaining roadmap, not a claim that all capabilities below already exist.

## Current outcome and operating direction

The user clarified that organization-wide knowledge-system efficiency is the
primary outcome, with time as its scarce physical resource. Initial human
verification should lead to automatic capture of decisions and a continuously
improving agent loop, then bounded policy-authorized autonomy with human
exceptions. UI/CLI waiting is part of the time cost. See
[the current operating model](KNOWLEDGE_OS_OPERATING_MODEL.md). Historical
measurement definitions below remain supporting instruments; they are not the
whole objective. The user has no measured baseline yet.

## Supporting outcome: time returned to the organization

The primary Enterprise AX objective is to shorten the time from a business
request or detected knowledge change to an **approved, usable result**, while
reducing the human effort spent producing it. Human judgment, correct evidence,
access policy and recoverability remain acceptance gates.

Measure by workflow type and comparable case complexity:

| Measure | Definition | Interpretation |
| --- | --- | --- |
| End-to-end lead time | Accepted-result timestamp minus request/first-detection timestamp; report median and p90 | Includes queueing, failures, rework and confirmation waiting; model latency alone is insufficient |
| Human active time | Sum of explicitly recorded human work intervals, with documented idle/pause behavior | Never infer this from time between assignment and approval |
| Confirmation preparation time | Ready-for-confirmation minus request/first-detection | Measures whether the agent removes preparation work |
| Confirmation waiting / review time | Queue-to-open and active review intervals, separately | Exposes missing owners and poorly prepared decision packets |
| First-pass completion | Approved usable results without correction / submitted cases | A time gain caused by unreviewed errors does not pass |
| Rework and failure burden | Correction cycles, human recovery time, failed/partial/cancelled cases | Keep these in the cohort; do not select only successful fast runs |
| Time returned | Matched manual-baseline human time minus assisted human time | Label estimates and sample size; no savings claim without a measured baseline |

First instrument the contract/billing amendment pilot and capture its manual
baseline. Then the accountable owner sets numeric median/p90 reduction targets
for end-to-end and human active time. No measured percentage improvement or
arbitrary deadline is asserted before that baseline exists. Quality, unauthorized
side effects and recovery failures are visible guardrails. Automation rate,
token count, node count and number of agents are not primary success measures.

Expose a concise task timeline with `requested_at`, `checking_at`,
`ready_for_confirmation_at`, `approved_at`, `execution_started_at`,
`usable_at` and paused/failed/resumed events. These are append-only server events.
Missing historical timestamps remain unknown. User-reported effort is labeled
separately from measured work intervals. Collect work-session timing for this
purpose transparently; do not infer productivity from keystrokes or covert
per-person surveillance. Cross-workflow totals must not mix incomparable cases.

## One capability, two interfaces

Human operation is the default. Every business capability available through the
UI must also be possible through the CLI, including setup, exception handling,
approval, recovery and administration. A workflow is incomplete if its happy
path works in the CLI but recovering or approving it requires undocumented UI
or database access.

UI, CLI and agents call the same application operations with the same validation,
authorization, workspace/domain scope, concurrency controls and audit records.
Do not build separate SQL mutations or weaker publication shortcuts for agents.
Managed remote CLI sessions never silently fall back to a privileged local store.
Direct host/database administration is outside this application authority model;
agent credentials must not share a human administrator's credentials.

The capability catalog drives help, schemas, interface coverage and permission
tests. Each action defines input/output schemas, permission, preconditions,
side effects, dry-run/preview semantics, cancellation/retry/compensation and
receipt. Its coverage matrix must name concrete UI actions, CLI commands, service
operations and tests; a generic arbitrary-HTTP command alone does not prove
usable CLI parity.

| Capability family | Required CLI operations | Current coverage |
| --- | --- | --- |
| Installation and health | initialize, provider/login status and test, configure models, doctor, start/stop/restart | Existing native CLI, including structured `ops model-identity`; audit full parity and recovery results |
| Sources | register/upload/list/inspect/edit/remove, inspect revisions/originals/permissions, freshness, rescan | Registration/removal, original/input-history and native `ops processing/processing-record` inspection exist; complete edit/scope/automation parity remains |
| Build and executions | plan, build/resume, inspect/watch, cancel, retry, recover partial execution | Build/resume/runs and native `ops execution RUN_ID [--watch] [--cancel]` exist; complete durable lifecycle/remote-session parity remains |
| Ontology use | operational/history/preview reads, concepts, graph data, original evidence, compare revisions | Summary/preview, original inspection/download/recovery and source-revision CLI exist; complete graph/concept/diff parity remains |
| Questions and data | chat/retrieval/citations, conversations, SQL and result files, SPARQL | Native `ops ask/retrieve/sql/sparql`, conversation and usage-receipt commands share the local HTTP service with UI. `ops sql --output` and `ops sql-result TRACE --output` download the same receipt-bound CSV as UI; failed saves recover without query/model replay. Complete other result-file, authorization and remote-session parity remain |
| Review | list/filter/assign cases, inspect dependencies, edit/approve/hold/reject/undo, merge duplicate cases, hand off | Connection review and `ops list/decide` exist. Stage 17 adds eleven named case commands with the same service as UI (see BUSINESS_CASES.md); case merge semantics, connection packets and complete coverage remain |
| Quality and publication | version question suites, run/inspect checks, prepare publication, request/inspect/approve confirmation, publish | `ops check/validate/publish` exist. Stage 18 adds typed rule review, versioned suites, immutable runs and eight named quality/legacy-check commands (see RULE_EVALUATION.md); full suites and authenticated confirmation remain |
| History and recovery | receipts, manifests, diff, restore proposal, validate/approve restore, checkpoint verification/drill | `ops publications` and `ops publication --revision N --verify` share receipts and integrity checks with UI/API; private checkpoint tooling and managed `ops restore-prepare/restores/restoration/restore` with UI parity exist; authenticated confirmation and complete recovery/permission coverage remain |
| Feedback and metrics | capture/link/assign/reopen/resolve feedback, resolution publication, timing/quality cohorts | Full CLI and connected workflow remain |
| Organization and security | principals, roles, domains, scope policies, credential lifecycle and audit | Remains P0-03 / P3; model-provider login is not a human/agent identity system |
| Automation and connectors | schedules/cursors/events, queue/watch/cancel/retry, outbox, notification policies, handoff | Remains P2 |
| Knowledge consumers | contracts/versions, consumer registration, compatibility/impact checks, authorized event reads | Remains P3 |
| Business actions | propose/draft/simulate, request approval, execute exact approved action, inspect/cancel/compensate | Remains P3; no external send or write is authorized by this design |

CLI defaults provide readable output. Automation mode provides versioned JSON
results and stable error codes; progress uses NDJSON/watch without an interactive
terminal. Add explicit `--json`, noninteractive mode, schemas/examples and
idempotency keys where appropriate. Errors expose actionable state and retry
timing while keeping credentials out of output. Machine clients must distinguish
blocked/awaiting approval, conflict/stale context, partial failure, authentication,
authorization and retryable provider failure. A noninteractive flag cannot grant
authority or fabricate a human approval. Never require parsing localized prose
to decide whether a consequential action succeeded.

## Delegation and confirmation

Use explicit authority levels: `read → suggest → draft → execute`. An agent acts
as its own authenticated Principal, representing an accountable human/team within
an explicit workspace/domain/action scope and time boundary. Retrieved document
instructions, model output and remembered preferences cannot grant authority.
Revoking a delegation prevents new work and pauses queued work at a safe boundary.

Use a persistent action state machine:

```text
ready → checking_context → awaiting_approval → executing
                         ↘ handed_off        ↙  ↓  ↘
                                      succeeded | partially_succeeded | failed
```

Add explicit cancelled/expired states and transition events. A user can inspect,
correct, pause and safely cancel the task from either interface. Going away from
the UI does not hide execution or turn a waiting task into a completed one.

| Action / trigger | Acting principal and data | Authority / effect | Risk, reversal and human checkpoint |
| --- | --- | --- | --- |
| Detect scheduled source change | Scoped collector; source cursor, permitted file/schema metadata | read; records deduplicated event | No publication; cancel queued processing, preserve original/cursor |
| Analyze and prepare amendment | Assigned agent; authorized originals, current publication and task instructions | suggest/draft; builds candidate, evidence, impact and evaluation packet | Reversible candidate; stale context forces refresh; uncertain/conflicting evidence hands off |
| Recommend review decisions | Agent; source-bound differences and dependencies | suggest; proposed decisions remain labeled recommendations | Human can edit/hold/reject without rebuilding unrelated work |
| Confirm a prepared change set | Authenticated human with review/publication permission; exact content and evaluation | approval; records accountable decision and scope | Cannot be self-approved by the proposing agent or supplied actor-name field |
| Publish or restore after confirmation | Scoped executor; exact approved content hash, publication, policy, index and artifact hashes | execute; switches to a fully prepared publication | Requires still-valid approval; idempotent receipt, old version retained, managed compensation/restore |
| Prepare contract-approval request | Assigned agent; permitted contract/policy and recipient context | draft; produces a preview, no send | Human checks facts, amount, recipient, effective date and consequence |
| Send/apply an external business action | Authorized executor; exact approved target/account/data/amount/service/time | execute; external side effect | Separate approval and idempotency, partial-failure receipt and compensation; implementation does not grant permission to send |
| Hand off provider/permission/evidence failure | Agent/system to assigned human; task history, attempted actions, relevant evidence, unresolved question | handoff; preserves ownership and current state | Human resumes without reconstructing the complete conversation; successful steps are not blindly repeated |

The confirmation packet answers: what changes, why, which evidence supports it,
which cases/consumers are affected, which checks passed/failed, what is uncertain,
what was excluded, how long it took, and how to recover. Separate facts,
estimates and recommendations. Show editable business inputs rather than hidden
model reasoning. Confirm business amendments as coherent dependency-complete
batches, not hundreds of disconnected nodes or a blanket "approve everything".

Bind approval to content hash, base publication, evaluation and permission policy,
targets, represented account, authorized executor, expiry and allowed effect.
Changed data, expanded targets, expired consent or increased risk invalidates
approval. Retrying the same exact approved action returns its receipt and must not
duplicate effects. The human can approve through an authenticated CLI flow as
well as UI; typing `--yes` or claiming `--actor human` is not an authentication
mechanism. The executor may perform the approved operation automatically after
that human decision. Routine permitted reads/preparation need no repeated modal
confirmation; high-impact publication, restore and external writes keep their
specific checkpoint.

## Agentic UX acceptance mapping

All twelve laws from the agentic UX skill apply to this platform direction. The
following are product-specific test requirements. They are **pending** unless
explicitly supported by executed evidence; this document is not certification.

| Laws / bundled cases | Observable product acceptance |
| --- | --- |
| 01, 05 / ambiguous-high-impact-action; prompt-injection-authority-request | Ambiguous account/amount/recipient blocks action; an original document cannot authorize a privileged operation |
| 02, 05 / revoked-memory; expired-consent | Revoked delegation or expired approval cannot execute or silently renew; current context wins |
| 03 / raw-provider-failure | CLI/UI show impact, owner, remedy and retry timing without raw credentials or opaque primary error text |
| 04, 07 / unsupported-confidence; fact-estimate-recommendation-mix | Decision packet labels missing/conflicting evidence and recommendations; unsupported certainty cannot satisfy the publication gate |
| 05 / permission-scope-escalation | Draft does not mean send; changing scope demands a new approval and produces no earlier side effect |
| 06 / hidden-risky-background-action | Waiting/blocked/partial/succeeded states survive navigation/restart and expose safe cancel/recovery |
| 08, 10 / cross-service-partial-failure; duplicate-retry | Per-service identity, completed effects and receipts survive failure; retry occurs once from the failed boundary |
| 09, 10 / undisclosed-agent-outreach; cancel-agent-message | If communications are later authorized, identify agent/principal/purpose/source and human handoff; cancellation prevents delivery and retry resurrection |
| 11 / context-preserving-handoff | Owner receives goal, evidence, actions, current status, unresolved issue and response expectation |
| 12 / automating-human-judgment | Human-only consequential decisions remain attributable; time returned and correct usable outcomes determine success, not automation percentage |

Record setup, actor, exact input/context, allowed effects, expected and actual
state, evidence and pass/fail/blocked per test. Exercise the same cases through
UI and CLI. Existing first-publication gates have unit, browser and real model
evidence, but do not yet prove authenticated human/agent authority separation.

## Roadmap integration

- P0-02 records publication/usage/task trace identifiers and timestamps for
  reproducibility and later timing cohorts.
- P0-03 establishes verified human/agent Principals, delegation, scopes and
  content-bound approval rather than trusting client-supplied actor labels.
- P1-01 makes ChangeSet/ReviewCase the human decision unit and agent handoff unit.
- P1-02/P1-03 prepare evidence/evaluations automatically, connect feedback to
  resolution publications, and show lead time, human effort and rework together.
- P2 provides durable preparation queues and owner notifications with cancel,
  revocation, idempotency and receipts; collection never implies approval.
- P3 consumers and business actions reuse the same contract and authority model.
- Every stage includes semantic CLI parity, machine-readable workflows and the
  corresponding error/recovery/authorization tests. Partial CLI coverage cannot
  be reported as completing the entire goal.

Stage 19 adds thirteen named AX/feedback commands, explicit human timing and
coverage confirmation, immutable context/evaluation links, provenance-separated
baselines and baseline-bound owner targets. See AX_FEEDBACK.md. These are
instrumentation, not evidence of actual organizational time savings.
