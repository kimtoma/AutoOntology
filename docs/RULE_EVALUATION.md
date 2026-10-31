# Explicit monetary rules and versioned evaluation

This stage adds two related mechanisms: structured, human-reviewed monetary rule
fields on BusinessRule individuals, and immutable evaluation suite versions that
can block publication and restoration. They share the existing review, identity,
publication and recovery boundaries. No rule field grants application authority.

## Record the rule, preserve the source

In Review → Knowledge changes, edit a BusinessRule and choose **Record explicit
monetary rule fields**. Record its business scope, amount measure, comparison
(`gt`, `gte`, `lt`, `lte`, `eq`), decimal threshold, three-letter currency, approval
authority, inclusive start date, exclusive optional end date, explicit priority
and descriptive exceptions. Give a review reason and inspect the original quote.
The same fields can be proposed through native `ops case-create`/`case-update`
JSON `changes[].edits.rule` and the existing exact human case packet.

Thresholds are finite nonnegative decimal strings, limited to 25 integer digits
and six fractional digits. Date and decimal values are canonicalized. Different
authorities with overlapping amount and date intervals at equal priority block
validation. A different explicit priority avoids that particular conflict; it
does not execute a policy or prove the higher priority is correct. Free-text
exceptions remain descriptive and are not executable predicates. Scope/measure
matching is exact; synonyms and semantic equivalence are not inferred.

Typed rules require linked, verified source quotes. This is an explicit provenance
check, not automatic entailment of each amount, authority or exception. People
must verify those facts. Existing text-only rules remain text-only and are counted
as such; the application never invents missing historical structured semantics.
Extraction has not been changed to automatically produce these typed fields.

Rule fields survive model JSON, retrieval text, RDF `ruleFields` JSON literals
and GraphML attributes. A model with typed fields uses snapshot format 2. Models
without them retain format 1 and their existing serialized data. Format 3 remains
unsupported. Previously committed snapshots, original bytes and exports are not
rewritten. Older application code must refuse format 2 rather than discard facts.

## Prepare and confirm an evaluation version

Under History → Versioned evaluation, create a stable suite key, title and reason.
Add representative questions with expected text, matching mode (`contains`,
`exact` after stripping recognized citation tokens, or `all_terms`), forbidden
phrases and a minimum number of known citations. Add deterministic model equality
checks for stable class/individual/property IDs and allowlisted fields, including
typed rule fields. Missing subjects or missing fields fail, including missing
fields compared with a null expected value.

Preparation creates an immutable version without changing active evaluation.
A human with `checks.edit` confirms the exact content hash to activate it. An agent
with `review.suggest` can prepare but cannot activate/deactivate. The prepared base
and permission policy must still match. Retries of the current exact activation
return its existing version; removing a suite only changes the active head and
preserves historical versions and events. At most 100 versioned questions may be
active; each suite has at most 100 questions and 100 assertions.

Evaluation criteria are visible business inputs, not hidden model reasoning.
Question text matching and recognized citation tokens are deterministic lexical
checks, not semantic-support certification. Unsupported citations and forbidden
phrases fail. Structured model checks and rule conflicts run before any provider
call; a deterministic failure avoids those calls and blocks publication/restoration.
At least one successful representative question remains mandatory.

Both normal publication and managed restoration use the same active criteria.
Signatures bind active versions, criteria and validator source hashes. A current
evaluation change invalidates earlier validation. The final content guard also
runs inside the publication write transaction, closing the gap between a prior
check and the atomic head switch. Model/provider files outside SQLite cannot be
atomically locked by this transaction; existing provider identity checks remain.

Validation results are separately preserved as immutable, hashed `quality_runs`.
UI and CLI can inspect previous failed and successful answers and model checks.
Publication/restoration manifests preserve the exact validation used by that
publication. Existing representative questions still work; immutable suite
questions cannot be individually deleted through the legacy question controls.

## Native CLI and API parity

```sh
autoontology ops quality
autoontology ops quality-prepare contracts --input suite.json
autoontology ops quality-version VERSION_ID
autoontology ops quality-activate VERSION_ID --sha256 HASH --confirm
autoontology ops quality-prepare contracts --previous VERSION_ID --input revised.json
autoontology ops quality-deactivate VERSION_ID --sha256 HASH --confirm
autoontology ops quality-runs [--id RUN_ID]
autoontology ops rules [--revision N]
autoontology ops check-remove LEGACY_QUESTION_ID
```

All return schema-versioned JSON and use the shared service behind `/api/quality`.
Inputs are bounded private regular JSON files. `ops rules` inspects the reviewed
proposal at the current candidate, including approved manual edits; an explicit
other revision inspects that stored snapshot. It does not publish, extract or
call a model. Legacy question removal now has the same live authorization and
attributable audit boundary in native CLI and UI.

## Remaining work

This is a monetary approval rule representation, not a general policy language,
legal interpretation or autonomous contract approval. Typed automatic extraction,
semantic evidence judgments, broader rule families, durable concept merge/split,
case-effective-date/question links, consumer impact and AX measurement remain
roadmap work. No actual time-saving percentage is claimed.
