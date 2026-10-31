# Feedback cases and transparent AX measurement

The objective is time from a business request to an approved usable outcome,
and the human effort needed to reach it. The application does not substitute
model latency or agent count for that objective. No actual organization-wide
saving is claimed without a measured comparable baseline and complete effort
records. The normal workspace has no invented baseline.

## Follow a request to its outcome

In Review → Improvement requests, prepare a business case from a question,
answer and request. The original feedback remains linked and hashed. Preparation
does not resolve it. Repeating the handoff returns the same case; a crash after
draft creation can be retried with its stable feedback request key.

Set the case workflow key, complexity key, effective date, exact required active
evaluation versions and measurement scope. Use `simulation` for acceptance tests
and fictitious work. Context versions are immutable; changing them invalidates
a previously prepared human packet. The whole context appears in confirmation.
A final reviewed/resolved case must be reopened before changing its context.

For a knowledge change, review the dependency-complete changes, validate and
publish normally. An accepted case can only link a committed publication that
contains its exact required evaluation versions as well as its confirmed changes.
Required versions that have ceased to be active must be replaced explicitly
before another packet is prepared.

For a request that needs no knowledge change, explicitly choose **No-change
closure** and select no knowledge proposals. A named human confirms a packet
whose effect is only that disposition. It applies no review decisions and creates
no publication. Link that no-change result separately, then confirm resolution
of the original feedback. A linked request cannot bypass this outcome through
the legacy feedback-resolution endpoint. Reopening its case reopens linked
requests in the same transaction, preserving their original answers and history.

## Record human work without monitoring people

A personally authenticated human starts a timer only while actively working,
and stops it for waiting or breaks. Timers persist across navigation/restart.
Only one timer per person runs at a time; retries return the same interval.
Another person or a delegated agent cannot create/stop that person's intervals.
A negative clock interval or one longer than four hours must be explicitly
discarded with a reason. Start/stop/discard events remain append-only.

These are explicit user-controlled intervals, not presence detection, keyboard
tracking or proof that a person was continuously active. Case elapsed time is
never inferred to be human effort. An owner (or AX administrator) must confirm
that all human work on the completed case is recorded; missing work cannot be
presented as time saved. An attestation binds the exact interval set, so later
work invalidates it. Discarded intervals stay visible.

## Establish a comparable baseline and accountable targets

Record each manual baseline observation with workflow/complexity, lead seconds,
human active seconds, reference/method and source:

- `observed`: a human attests that the observation was measured. This is not an
  independent audit of the referenced evidence.
- `reported`: a recalled/reported estimate. It never becomes an observed baseline.
- `business` and `simulation` are separate cohorts. Simulated observations never
  become measured business savings.

Use a stable `request_key` for each observation. Exact retries do not add samples;
reusing a key for different data fails. Without a key, identical canonical data
is deduplicated, so distinct genuine observations should use distinct keys.
Corrections exclude a sample with a reason while preserving its original values.

AX administrators may set numeric reduction targets only against the exact
current observed business-baseline hash. Targets are immutable owner decisions,
not measured improvements. A corrected/expanded baseline makes an earlier target
stale. No arbitrary target is created when a measured baseline is absent.

The operations dashboard compares only equal workflow/complexity/scope cohorts.
It shows median and p90, sample counts, completed/active/cancelled cases, retained
corrections and recorded effort coverage. p90 uses nearest rank `ceil(0.9*n)`.
Lead-time distributions contain completed usable outcomes; incomplete/cancelled
cases remain visible with their state. Assisted effort enters comparisons only
for completed cases with exact coverage attestation and no running timer.
A business effort difference appears only if every completed case has that
coverage and an observed business baseline exists. A negative difference means
more effort. Cohort differences are observational and do not prove causation.

## UI and native CLI

Every action has a semantic native JSON command under `ops`:

```sh
autoontology ops ax
autoontology ops ax-context CASE_ID
autoontology ops ax-context-set CASE_ID --input context.json --reason "Classify" [--previous VERSION_ID]
autoontology ops ax-efforts
autoontology ops ax-effort-start CASE_ID --note "Review original"
autoontology ops ax-effort-stop TIMER_ID --note "Pause" [--discard]
autoontology ops ax-effort-attest CASE_ID --reason "All human work recorded" --confirm
autoontology ops ax-baselines
autoontology ops ax-baseline-add --input baseline.json --confirm
autoontology ops ax-baseline-void SAMPLE_ID --reason "Wrong cohort" --confirm
autoontology ops ax-target-set --input target.json --confirm
autoontology ops feedback-case FEEDBACK_ID --title "Amendment" --reason "Evidence changed" [--owner HUMAN_ID]
autoontology ops feedback-resolve FEEDBACK_ID --case CASE_ID --confirm
```

The shared `/api/ax` operations enforce the same authority, stale-context,
provenance and retry rules. Whole-workspace `ax.read` is available to editors and
reviewers. Human-only `ax.record` covers owned intervals; human-only
`ax.configure` is an administrator capability for baselines and targets. Scoped
agents can read authorized aggregates and prepare business context, but cannot
claim human effort or measured baseline observations. The normal legacy local
workspace must enroll individual users before human timing/configuration writes.

Context input fields: `workflow`, `complexity`, `effective_at`, `required_suites`
(array of `{id, sha256}`), `feedback_ids`, `measurement_scope`, optional
`resolution_intent` (`knowledge_change` or `no_change`). Baseline fields:
`request_key`, `workflow`, `complexity`, `lead_seconds`, `active_seconds`, `source`,
`reference`, `measurement_scope`. Target fields: `workflow`, `complexity`,
`baseline_sha256`, `lead_reduction_percent`, `effort_reduction_percent`, `reason`.

## Remaining acceptance

This stage instruments cases and makes provenance explicit; it is not a real
contract/billing pilot outcome. A real manual measurement, representative assisted
work and accountable numerical targets remain necessary. Queue/failure/recovery
phase attribution, active-time categories, first-pass/correction adjudication,
consumer-impact metrics and full P1–P3 acceptance remain subsequent work. Partial
recorded effort and unknown historical timestamps must stay unknown.
