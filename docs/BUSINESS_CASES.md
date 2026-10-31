# Business cases and exact review confirmation

A business case groups one business event, its owner, due date, priority, reason,
knowledge proposals and related cases. It records the operational base, candidate
and base publication. Its append-only, hash-chained timeline preserves prior
selections across recompare, resolution and reopen. Related cases can depend on,
duplicate or supersede another case; dependency cycles are rejected. Duplicate
and supersession links are references, not destructive record merges.

## Human and agent workflow

1. Open **Review → Business cases**, or create a case with native `ops case-create`.
   Select changes, proposed decisions and reasons. This changes no review decision.
2. Prepare a packet as a human or an agent with `review.suggest`. Name an active
   human with whole-workspace `review.decide`. The packet contains the business
   context, exact before/after changes, edits, evidence, required structural
   concept dependencies, source/model/review hashes and expiry. Dependency closure
   covers explicit model references, not inferred semantic or business causality.
3. The named human reviews the entire packet and explicitly confirms through UI
   or `ops case-confirm --sha256 HASH --confirm`. Agents cannot confirm. Current
   credentials, policy, candidate, source/model inputs, decisions, case version
   and expiry are rechecked. All review decisions and the receipt commit in one
   SQLite transaction. Failure or expiry before completion rolls back the batch.
   An exact retry returns the original receipt without repeating effects.
4. Publish through the existing, separate validation/publication workflow. Review
   confirmation alone is not an operational publication.
5. Link the case to a committed publication containing the exact confirmed
   decisions. Held decisions cannot resolve. An all-excluded packet may resolve
   without a publication, provided the matching exclusion decisions still exist.
6. Reopen explicitly for later work. Matching unpublished decisions are undone;
   published history and prior case events survive. Recompare explicitly selects
   the new candidate changes and invalidates the earlier packet.

The operations home shows active, awaiting-confirmation, overdue and unassigned
counts. The case detail shows its immutable packet, actor timeline and linked
outcome. Packet inspection and confirmation do not run a model.

## Native CLI

All commands return schema-versioned JSON. Use private regular UTF-8 JSON input
files of at most one MiB. Use personal or scoped delegated credentials; do not
pass a human credential to a preparing agent.

```sh
autoontology ops cases --status awaiting_review
autoontology ops case-summary
autoontology ops case-members
autoontology ops case-create --input case.json
autoontology ops case CASE_ID
autoontology ops case-update CASE_ID --version 1 --input patch.json
autoontology ops case-prepare CASE_ID --version 2 --reviewer HUMAN_ID --ttl 1800
autoontology ops case-confirm PACKET_ID --sha256 HASH --confirm
autoontology ops case-resolve CASE_ID --version 4 --reason "Operational" --publication 10
autoontology ops case-rebase CASE_ID --version 4 --reason "Changed policy" --proposal proposals.json
autoontology ops case-reopen CASE_ID --version 5 --reason "Follow-up event"
```

`case.json` has `title`, `reason`, optional `owner_id`, `priority`, timezone-qualified
`due_at`, `request_key`, `changes` and `links`. Each change has its current `id`,
`decision` (`accept`, `reject`, `hold`), `note` and optional existing review-schema
`edits`. A link has `relation` and `case_id`. A patch contains these editable fields;
`status` may be `open`, `preparing`, `held` or `cancelled`. Reviewed/resolved cases
require explicit recompare/reopen instead of silently rewriting confirmation.

## Current limits

Case operations currently require whole-workspace candidate/review access.
Partial-domain case projection, connection-review decisions inside the same packet,
semantic conflict detection, effective-date/rule semantics, linked question suites,
consumer impact and a durable background executor remain subsequent roadmap work.
A duplicate link does not merge cases. Other individually authorized review flows
remain available; a case is not yet a universal publication gate for all changes.
The timeline records elapsed waiting and actions. It is not measured human active
time, a manual baseline or evidence of AX time savings.

Schema v15 is additive. Stage recovery must restore SQLite, originals, exports
and the isolated PostgreSQL cluster together, using the private checkpoint tool;
a git checkout alone does not undo schema or data changes.
