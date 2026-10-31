# A continuously improving organizational knowledge system

User direction clarified on 2026-10-31. The primary outcome is the efficiency of
the organization's whole knowledge system: current, consistent, reusable knowledge
that supports decisions and improves from their outcomes. Time is the scarce
physical resource across that system, including UI waiting, CLI startup, finding
evidence, repeated questions, review and correction—not only task duration.
No baseline has been measured yet. Retain that unknown; do not invent savings or
ask again for a number that does not exist.

## The operating loop

Observe changes and decisions → detect gaps/conflicts/impact → prepare proposals
→ evaluate against evidence and contracts → execute within authorized policy or
request a human decision → observe outcomes → improve the next proposal.

Workflow automation establishes this loop. Its purpose is persistent organizational
learning and reliable knowledge circulation. The operating dashboard should expose
new knowledge, important gaps, stale/conflicting facts, repeated unmet requests,
reuse and decisions needing attention. Explicit time observations support those
outcomes; they do not become a per-person productivity score or the whole product.
Use observable counts and provenance, not an invented composite efficiency score.

## Progressive autonomy

Initial commissioning requires authenticated human verification. Automatically
capture the decision context, evidence references, alternatives actually recorded,
corrections, affected scope and observed outcome. Keep unknown alternatives/results
unknown. Do not collect private hidden reasoning or infer human authority from a
sentence inside a document. Imported/reported decisions are distinguished from
verified decisions made through the service.

Use those records in an agent loop with explicit operating modes:

1. Observe and learn from verified decisions and real outcomes.
2. Prepare/shadow proposals and demonstrate validation without changing operations.
3. Apply routine changes within a human-authorized bounded policy, with current
   source/domain/consumer scope, tests, expiry, impact limits and rollback receipts.
4. Route ambiguity, conflicting evidence, expanded authority, high-impact changes
   and policy failures to humans. Observe corrections and feed them back into the
   next loop. Humans can revoke or reduce autonomy; agents cannot expand it.

A policy-authorized agent remains identified as an agent. It never borrows a human
credential or pretends a human made that action. The exact policy, human policy
approver, input/evaluation/target hashes and execution receipt must explain why an
automatic effect was permitted. Model-generated confidence or a document's claim
of approval cannot create that permission. The existing human-only operations
remain in place until an explicitly tested bounded execution path is implemented.

## Interaction latency is an acceptance criterion

Measure first useful authorized data, route changes, cached revisits, request
waterfalls and native CLI startup/end-to-end duration. Show available sections
without waiting for unrelated slow requests. Retain authorized prior data during
background refresh with clear freshness/error state. Policy/account changes must
still clear caches; faster rendering cannot reuse another authority's results.
Record local lab and controlled-delay measurements separately from production
real-user performance. Do not claim a performance improvement without a before/
after measurement on the same fixture.

These principles refine the complete P0–P3 roadmap; they do not claim the pending
continuous collection, decision-learning, bounded autonomy and consumer/action
contracts are already released. Development and review remain local-only.
