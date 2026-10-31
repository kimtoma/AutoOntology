# Ontology Ops handoff and continuation plan

Recorded on 2026-10-31 (Asia/Seoul) for the requested wrap-up and GitHub sync.
This is an alpha development checkpoint. The full Knowledge OS objective is not
complete. Do not resume feature development until the user resumes the work.

## Product direction to preserve

- Build a living organizational knowledge system across structured data and
  unstructured documents, rather than finishing after the initial ontology build.
- Make initial setup contextual. After first publication, center the operations
  dashboard, incoming knowledge, assigned decisions, evidence and changes.
- Keep the collapsible left navigation, restrained light-mode interface and fast
  access to useful data. UI/CLI waiting is part of organizational efficiency.
- Provide semantic UI/CLI parity. Agents prepare work under their own identities;
  humans confirm exact evidence, scope and effects. Progress toward decision
  learning and bounded policy-authorized agent loops, with human exceptions.
- Measure freshness, consistency, reuse and decision quality alongside waiting,
  human effort and request-to-usable-result time. A real business baseline has
  not been measured; never invent savings or substitute task timers for this goal.
- Keep this iteration local. GitHub source synchronization does not authorize
  deploying the new application to the public Cloudflare workspace.

## Authoritative state at wrap-up

| Surface | State |
| --- | --- |
| Development branch | `codex/ontology-ops-local`; application boundary commit `27499d1`, final checkpoint-tool commit `7038ef4` |
| Definitive data checkpoint | `stage-26-enrollment-state`, source `7038ef4`, schema 19, published r8 |
| Frozen review application | Source `b6d31ac`; separate identity/data copy, UI 7840, backend 3141, PostgreSQL 55436 |
| Normal local preview | UI 7830, backend 3131, PostgreSQL 55433; individual identity intentionally remains unconfigured |
| Existing public checkout | `codex/cloudflare-demo-macmini` at `42efdba`; application 7820, backend 3121, PostgreSQL 55432; no new deployment |
| Models | Codex chat provider and existing Ollama `qwen3-embedding:4b`; no model replacement in the last stages |
| Independent owner marker | Design/acceptance work only; no marker, new enrollment command or recovery ceremony has been implemented |

The development checkout and private evidence live under the main host checkout's
ignored `data/ontology-ops-local/` directory. Runtime data, credentials, database
archives, screenshots and host-specific launch controls are not published by git.
The final documentation/version commit may follow `7038ef4`; that does not change
the runtime/database evidence reference above.

## Completed work and limits

1. Operational reads share a published version. First publication requires
   review and representative evaluation; rebuilds create candidates.
2. Originals, source/processing bindings, publication manifests, query/SQL
   receipts, saved downloads and managed knowledge restoration are implemented
   with scoped failure/restart and actual local acceptance evidence.
3. Human/agent identities, roles/domains, resource projections, exact human
   review packets, expiry/revocation and effect-time checks are implemented.
   This is not a complete authority/recovery audit.
4. Business cases link owner, context, evidence, typed monetary rules, effective
   dates, evaluation versions, feedback and outcomes. Durable concept identity,
   consumer impact and semantic automatic judgment remain incomplete.
5. Folder/Postgres observation, deduplication, durable jobs, leases/generations,
   retries, local owner outbox and scoped preparation are implemented. Managed
   worker lifecycle and real-provider queue recovery remain incomplete.
6. UI progressive loading and native startup were measured locally. No real
   organization-wide efficiency percentage is claimed.
7. Stage 24 (`56af1c0`) fixes native `init` saved-key origin rebinding and settings
   writes after policy/credential drift, including authenticated `--env` changes.
8. Stage 25 (`6996f51`) records partial-identity-loss reproductions and the owner
   recovery acceptance gate. It is documentation-only; its source archive was
   extracted and matched, reusing stage-24 data recovery evidence.
9. Stage 26 (`27499d1` / `7038ef4`) shares enrollment classification across native,
   HTTP and effects; surviving identity history prevents anonymous local fallback
   and replacement enrollment. UI JSON/SSE errors hide work and clear protected
   caches immediately; retries cannot downgrade a previously managed session.
10. Checkpoints support explicit APFS clone mode. Actual restore matched every
    allowed PostgreSQL row, six originals, schema 19 and r8. Original checkpoint
    hashes still matched after the restored PostgreSQL was stopped. Existing
    73 SQLite data tables, 421 exports and 11 public assets were preserved.

The stage-26 first attempt stopped on ephemeral tool/provider symlinks and was
preserved as `.stage-26-enrollment-state.failed-ephemeral-links`. It is not a
restore point. The final `stage-26-enrollment-state` is definitive. New checkpoints
exclude only the regenerable `workspace/kordoc` and `workspace/run/codex-home`
caches; they do not dereference the host Codex login into a new backup. Ordinary
run profiles, originals, exports, processing receipts and identity data remain.
Earlier private checkpoints were preserved unchanged.

## Verified tests and review evidence

- Full application Python regression: **1662 passed, 66 integration excluded**,
  before the final isolated checkpoint-tool additions.
- Final checkpoint/enrollment focused regression after those tool additions:
  **38 passed**. These overlap with the full regression; do not sum the counts.
- UI full regression: **205 passed**; the final test-fixture cleanup was followed
  by **12 related passes**. UI lint/build passed; the existing restoration purity
  warning remains. The committed bundle is `index-DYKATAX9.js`.
- Stage 26 has five actual built light-mode UI captures: login, metadata loss,
  closed retry, backup followed by login, authenticated work restored. The
  intentionally independent fixture does not run its engine backend; its source
  readiness warning is not an application-wide acceptance result.
- Actual browser acceptance invoked no model and wrote no PostgreSQL data. Its
  controlled SQLite backup restoration is not a product recovery command.
- Frozen review acceptance includes real r8 graph, originals, exact pending case,
  versioned quality, real human/agent observations and existing downloads; its
  separate initial backup matched 74 SQLite tables, seven originals and three
  PostgreSQL databases. No publication was performed by review preparation.

Private entry points, relative to the main checkout:

- `data/ontology-ops-local/review-20261031/index.html`: whole-system review guide.
- `data/ontology-ops-local/changes/index.html`: stages 00-26 and actual UI flows.
- `data/ontology-ops-local/RESTORE_POINTS.md`: definitive recovery references.
- `data/ontology-ops-local/verification/owner-recovery-audit/`: before/after
  reproductions, test logs, browser evidence and clone validation.

## Remaining plan, in delivery order

### P0: finish authority, enrollment and recovery

- Independent host-owned enrollment marker, recoverable first-enrollment journal,
  supported local owner enrollment/adoption/recovery and marker-aware backups.
- Full route/CLI/effect/stream/queue inventory and semantic machine-readable CLI
  parity, including remote sessions, renewal, exceptions and recovery paths.
- Crash-consistent effect journal across settings/files, upstream engine and
  database effects; producer recovery and idempotent compensation.
- Structured-engine processing provenance, target-remapping contracts and complete
  full-stage acceptance. Preserve historical unknowns and archived evidence.

### P1: decisions that improve subsequent agent work

- Integrate connection review into exact case packets and complete real consumer
  impact, phase/rework attribution and correction adjudication.
- Automatic typed extraction and semantic citation/support evaluation; durable
  concept rename/merge/split with historical identity and dependency handling.
- Turn verified decisions, reasons, evidence and outcomes into versioned decision
  memory; evaluate proposals in shadow mode before granting bounded autonomy.
- Explicit autonomy policy with limits, expiry, revocation/kill switch, audit and
  human exception routing. Agents must not impersonate a confirming human.

### P2: managed continuous operation

- Install/start/stop/inspect/renew managed workers through UI and CLI; expose the
  actual principal, assignment, scope and failure owner.
- Verify provider-backed preparation restart/recovery, not only controlled
  builders. Keep observation separate from publication authority.
- Document-repository pilot; cursor/delete/move/duplicate/failure semantics,
  credential lifecycle, large-source/CDC behavior and schema/data distinction.

### P3: domains, consumers and business actions

- Contract/billing, HR and security domain catalog with ownership, sources,
  publication, shared/conflicting mappings and authorization.
- Versioned KnowledgeContract, consuming report/app/training/agent registry,
  compatibility/evaluation checks, impact and authorized reads/events.
- Separate business-action schemas, approval and permission, duplicate suppression,
  compensation and audit; contract-approval draft pilot. No external sending or
  irreversible business action has been authorized by this development request.
- Complete UI/CLI acceptance, current visual/statechart documentation, recoverable
  local commits, actual workflow review and observed organization-wide outcomes.

The last estimate was 20-30 hours of continuous Codex implementation/technical
verification. It is an estimate rather than a completion promise. Recompute the
calendar finish from the actual resume time. Real organizational outcomes require
operational observations; the missing measured baseline is already known.

## Exact next unit when the user resumes

Read [OWNER_RECOVERY_GATE.md](OWNER_RECOVERY_GATE.md) and
[ENROLLMENT_STATE.md](ENROLLMENT_STATE.md), then design and implement the independent
marker/enrollment unit with tests first. No partially written implementation was
left at wrap-up; the following are design candidates, not accepted invariants:

- A marker inside the database directory may disappear with an old whole-directory
  restore. Consider a private host registry outside the replaceable workspace,
  bound to the canonical home and workspace identity, with explicit adoption of
  pre-marker configured workspaces.
- Reserve host intent before the identity commit, finalize after it, and fail
  closed on every pending/crash state. Serialize competing owner operations;
  resume the same operation rather than creating another owner.
- Bind the marker to durable database evidence, possibly an immutable identity
  audit event containing an operation ID/nonce. Verify the full trust model before
  choosing this instead of a new schema table.
- Keep passwords/credentials out of arguments/logs/tracked files. Use existing
  private credential-file primitives, filesystem checks, fsync and atomic writes.
- Distinguish fresh enrollment, interrupted enrollment, legacy adoption, forgotten
  credentials and corrupt metadata. Do not synthesize lost approval history.
- Update checkpoint/restore pairing and raw SQLite-copy fixtures deliberately;
  do not silently auto-adopt a restored identity or weaken the corruption guard.
- Exercise concurrent enrollment, each process-death boundary, missing/incorrect
  marker, pre-enrollment rollback, symlink/mode failures, old-session invalidation
  and agent/remote denial. Add actual UI/CLI captures before declaring completion.

## Resume and verification procedure

1. Read current `AGENTS.md`, this handoff and the complete implementation ledger.
   Inspect branch, HEAD, dirty files and current runtime state; preserve new work.
2. Keep the public checkout and frozen review copy separate from development.
   Review UI: `http://127.0.0.1:7840/ui/?lang=ko`; normal preview: port 7830.
   Private login files remain under each runtime's `security/` directory.
3. Use the main host checkout's Python to run each private `control.py`
   (`status`, `start`, `stop`). The frozen review has its own control script.
   Leave currently running user-facing services running unless asked otherwise.
4. Run Python from the development checkout. Its `.venv` is shared; avoid replacing
   the main environment's editable install. A bare `PYTHONPATH` does not override
   the current working directory's package.
5. Full regression on this isolated checkout uses the private
   `verification/full_suite.py` helper: it verifies the upstream gitlink equals
   the host's pinned read-only vendor checkout and maps it without editing vendor.
   Use the normal documented commands in a fresh clone with its submodule present.
6. After each verified implementation unit, commit source and rebuilt UI bundle,
   create a private checkpoint, restore into a new directory/unused port, compare
   data and originals, reverify the checkpoint and stop the drill cluster.
7. Prefer explicit `--copy-mode clone` for this APFS host under space pressure.
   Never remove existing data/backups to make a stage pass. Failed partial
   checkpoints are not valid recovery targets.
8. Keep the complete remaining objective active on resume. Do not equate stage
   counts, passing tests, screenshots or this alpha version with all P0-P3 gates
   being complete. Require scoped authoritative evidence for each requirement.
