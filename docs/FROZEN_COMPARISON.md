# Frozen data comparison

The comparison tool compares submitted SQL against explicit reference
hypotheses on the same frozen tables. It is available in the existing History
page's quality area, through native CLI and REST. It does not publish knowledge,
run an LLM, connect to operational PostgreSQL, establish a measured baseline,
or verify business approval. No proposed UI/UX handoff menus are applied.

## Use from the UI or CLI

Open `/ui/history?lang=ko#frozen-comparison`. Load the bundled fictional Hanbit
sample or choose a comparison JSON. The input summary shows tables, row counts
and questions before execution. Inspect/edit JSON and SQL when needed, compare,
then inspect result differences, abstentions, submitted evidence and reproduction
hashes. Editing an input marks the previous result as belonging to the old input;
failed retries preserve that result. Credential or policy changes reset the form.

Download **input + report JSON** to preserve a UI run. Reports are stateless and
are not saved in workspace history by this version. Replaying the saved input
requires no new model call. Compare the report `sha256` after removing the outer
UI download envelope and supplying its `input` object to the CLI.

```bash
uv run autoontology ops comparison-sample
uv run autoontology ops compare --input samples/evaluation/hanbit-billing-v1.json
```

Both commands emit JSON. The sample command emits the full input (`pack` and
`attempts`), not an approved evaluation version. For managed workspaces CLI input
files obey the existing HostFiles approved-root, protected-path and link policy.
The shipped samples are an approved default root. Do not bypass admission by
copying secrets into a comparison. Use normal host-root administration for a
specific additional business folder when authorized.

REST entry points:

- `GET /api/evaluation/sample`: the bundled input, gated by `validation.run`.
- `POST /api/evaluation/compare`: a bounded JSON input, also gated by
  `validation.run`; no server-host file path selector is accepted.

Responses use `Cache-Control: private, no-store`. Both adapters call the same
application operation and current authority checks. Current policy is checked
again before the result is returned. Existing workspace-wide scope remains
explicit; this does not close the full resource-level authority audit.

## Input and report contract

The input has exactly `pack` and `attempts`. The version-1 pack includes an ID,
title, declared data class/roles/source identities, inline tables, frozen evidence
passages and cases. Each case declares SQL or an expected abstention, definition,
required known evidence IDs and an absolute numeric tolerance. Tables are loaded
as text so conversions are explicit in SQL. Do not infer date, money, recognition
or business semantics from an SQL cast. Multirow results are order-sensitive;
use `ORDER BY` for a defined order.

Two or three attempts provide complete answers keyed by case ID. Their origin is
`constructed_example` or `reported_unverified`. Submitted origin, role labels and
source hashes are declarations, not authenticated external execution/provenance.
The tool verifies known evidence IDs, not passage applicability or semantic truth.
A reference query is a hypothesis even if its result matches every attempt.

The report binds normalized pack/attempt hashes, engine name, SQLite version and
validator source digest. Its `sha256` covers the deterministic report core. The
verified current actor context is returned separately. Values beyond JavaScript's
safe integer range use `{ "type": "integer", "value": "9007199254740993" }`, so
browser parsing and downloads preserve digits. Numeric SQL comparisons happen
before that transport encoding. All reports explicitly keep `human_approval`
and `baseline_measured` false and `model_calls` / `live_postgres_queries` zero.

## SQL and resource boundary

SQL executes in a fresh in-memory SQLite database populated only from submitted
tables. The SQLite authorizer permits SELECT, reads of those tables and a small
function allowlist. Writes, attachments, pragmas, extensions, schema reads and
recursive statements are denied. A query-only connection, SQL/expression/string
limits, VM progress limits, shared two-second calculation deadline, 100-row and
128-KiB per-result limits bound execution. Input is at most two MiB, ten tables,
5,000 rows, twenty cases and three attempts. A rejected attempt has an explicit
execution error; no alternate model, live query or publication fallback is used.
See the [SQLite authorizer documentation](https://www.sqlite.org/c3ref/set_authorizer.html)
and [Python connection limits](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.setlimit).

## Sample and remaining acceptance

The frozen Hanbit pack contains 155 rows from three existing structured files,
original hashes and two regulation passages. It demonstrates changed aggregation
grain, billed versus outstanding amount, weighted versus unweighted collection
rate, unsupported recognized revenue and unknown payment-term exceptions.
Constructed bad/reference SQL is not measured model output. The collection-rate
formula is deliberately an unapproved candidate, not a definition inferred as
approved from a regulation that only defines its inputs.

Customer acceptance still needs a named receiving service, business approver,
operator, agreed scope/exclusions/thresholds and a held-out set. Actual matched
model/service A/B runs, approved Definition/KnowledgeContract persistence,
publication binding, saved authorized run history, semantic evidence review and
observed organization-wide outcomes belong to the remaining development work. The
real business baseline is unmeasured. This synthetic harness does not pass customer-readiness gates.
