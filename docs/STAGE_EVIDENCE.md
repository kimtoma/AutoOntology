# Stage change records and UI flow captures

Every implementation stage records its scope, observed result, remaining work and
local recovery point. This is a standing product-work requirement, including CLI,
agent and Enterprise AX changes. Public deployment is a separate authorization.

Use `KNOWLEDGE_OS_IMPLEMENTATION.md` for the committed implementation ledger. Keep
private runtime data, screenshots and reports outside git under the isolated
`data/ontology-ops-local` runtime. Its `changes/index.html` is the human-facing stage
index; a stage directory contains its detailed report and capture manifest.

For every stage record:

1. Stage identifier, purpose, affected capabilities/files and concrete before/after
   behavior. Name its parent/source commit and final tested local commit.
2. UI operation, native CLI equivalent and shared application operation, including
   cancellation, exceptions and recovery. Do not equate arbitrary HTTP access with
   a usable CLI or claim unimplemented identity/permission guarantees.
3. Tests and real runtime scenarios actually executed, results and private evidence
   paths. Clearly distinguish actual model/data/browser runs from controlled fixtures.
4. State preserved, checkpoint label, integrity evidence and recovery drill. A code
   commit alone is not a data backup. Record failures and their corrected results.
5. Remaining gaps, excluded scope and the next stage. Do not mark a stage or the
   overall roadmap complete while required acceptance remains unexecuted.
6. Time evidence, if collected. Query elapsed time is not human active time or an
   Enterprise AX savings claim; baseline, waiting, review and rework remain visible.

When UI behavior changes, capture the changed flow in light mode:

- Entry and editable inputs, plus an earlier real screen when one is available.
- Work in progress and the available stop/control action.
- The exact human confirmation for consequential effects, when applicable.
- The completed usable result and its evidence/download path.
- A meaningful boundary/failure and its recovery or handoff state.

Only include states relevant to the changed capability. A permitted data read has
no publication confirmation step; do not fabricate one for a screenshot. Capture
actual browser behavior after inspecting the rendered page. Controlled failures or
mocked services must be labeled in both the manifest and report. Never present a
sketch, placeholder or old screenshot as current implementation.

Each capture manifest records sequence, title, route, capture timestamp, actual or
controlled scenario, assertion/result, image SHA-256 and source commit. During an
uncommitted acceptance run record the parent commit plus patch digest; finalize the
tested commit after committing that same patch. Preserve the original images.

Use an isolated copied workspace for examples that write data or alter source
schemas. Keep reports owner-only, omit credentials/authentication state, inspect
captured screens for secrets, and close test browsers/services after verification.
Link the report from the stage index so a reviewer can follow the workflow without
reconstructing a tool log. No external deployment is implied by preparing a report.
