# Knowledge OS implementation and recovery ledger

The complete scope is the lifecycle roadmap dated 2026-10-30, not just the
first publication fix. Implementation stays on `codex/ontology-ops-local`.
The public checkout, service and Cloudflare configuration are out of scope.
Each stage needs a tested local commit and a private data checkpoint. A code
checkout alone cannot undo a database migration or recover an overwritten file.

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
| Recovery | Private stage checkpoint, hashes, restoration instructions and restore drill | In progress |
| P0-01 | Explicit first approval, mandatory representative question suite, failed validation, real initial-install acceptance including missing login/backend | Pending |
| P0-02 | Immutable originals and SourceRevision; PublicationManifest and UsageTrace; managed restore; failure/restart/backup acceptance | Pending |
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

- [ ] Empty installation → validated and approved first publication → operations home.
- [ ] Default graph, answer, SQL and export use the same publication.
- [ ] Old originals, citations, retrieval and questions remain reproducible after edits.
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
