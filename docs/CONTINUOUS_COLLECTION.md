# Persistent collection and candidate preparation

Continuous collection observes an explicitly registered, classified source using a
verified human or delegated agent credential. A human administrator chooses the
source, worker, interval, mode, expiry and retry limit. An agent cannot configure,
expand, resume or authorize its own scope. The default UI scope expires in 24 hours.
Unconfigured legacy workspaces require individual identity enrollment first.

## One flow through UI and CLI

Open Sources → Continuous collection. Choose **Observe changes only** to hash
approved inputs without invoking a model. **Prepare a candidate** may invoke the
configured model; it merges the registered workspace sources. Their target set is
bound to the schedule, so adding or changing a source blocks preparation until a
human explicitly configures the new scope. Collection never publishes knowledge.

A schedule and a running worker are separate. The UI's **Process once as me** runs
only the current principal's assignments. An agent assignment waits for that
agent's own worker. A native worker can keep polling while the UI is closed:

```sh
autoontology --credential-file /private/agent.json ops collection-worker --watch
autoontology ops collection
autoontology ops collection-job JOB_ID
autoontology ops collection-history SCHEDULE_ID
autoontology ops collection-poll SCHEDULE_ID
autoontology ops collection-pause SCHEDULE_ID --reason 'Pause intake' --confirm
autoontology ops collection-resume SCHEDULE_ID --reason 'Reviewed current scope' --confirm
autoontology ops collection-cancel JOB_ID --reason 'Stop preparation' --confirm
autoontology ops collection-retry JOB_ID --reason 'Input access restored' --confirm
autoontology ops collection-ack NOTICE_ID
```

Native `collection-configure --input FILE --confirm` accepts a bounded JSON object:

```json
{
  "source": "policies",
  "worker_id": "32-character individual principal identifier",
  "interval": 60,
  "mode": "observe",
  "reason": "Observe approved business policy changes",
  "max_attempts": 3,
  "ttl": 86400
}
```

Delegated observation needs `collection.read`, `collection.work` and `source.read`.
Candidate preparation also needs `candidate.read` and `candidate.prepare`, with
current authority for the registered workspace. Every effect reloads credential,
policy, source target, schedule version and execution generation. No human
credential is borrowed or restored from caller-supplied identity fields.

## States and recovery

A stable input does not enqueue another job. A→B→A creates three observations,
using monotonic sequences rather than content hash alone. Only one enabled
schedule may own a source. Repeating the identical configuration returns the same
schedule; changing scope requires pausing the existing one first. Pending older
inputs are superseded and remain in history.

Jobs move from `queued` to `running`, then `succeeded`, bounded exponential-backoff
`retry`, terminal `failed`, `blocked`, or `cancelled`. Worker leases last 120 seconds
and heartbeat while preparing. After expiry, the next authenticated assigned
worker recovers the job with a new lease. A late completion cannot write through
the old generation. Manual retry preserves events and increments its generation.
A completed build is found by its job/generation marker if the process died before
recording completion; it is not built again merely to reconstruct its receipt.

Pause, cancellation, expiry and policy changes fence subsequent participating
SQLite writes. They cannot undo a model request or external preparation step that
already happened. Preparation can leave a partial run or unpublished artifacts;
these remain visible for recovery. The queue does not provide a crash-atomic
transaction across the filesystem, upstream engine and SQLite. Publication and
external business actions require separate authorization and recovery contracts.

## Connector contracts

Folder observation records relative paths, SHA-256, sizes, duplicate-content
counts and protected exclusions. Host-root and source/domain authority are checked
at actual reads. A rename, removal or duplicate path is an observation, not an
automatic identity merge or deletion of published knowledge. Limits are 2,000 files
and 256 MiB per observation; protected authentication files are never ingested.

PostgreSQL observation uses a repeatable-read, read-only transaction. Schema and
row digests are separate. Rows are canonically ordered, and values are reduced to
hashes before entering the queue record. Limits are 100 tables, 10,000 rows per
table and a three-second statement timeout. Larger/unavailable sources fail
visibly instead of pretending a sample proves complete freshness. MySQL,
Snowflake, repository webhooks, vendor CDC and remote connector credential
management remain separate connector work.

The local outbox is owner-bound and supports acknowledgement. It records source
changes, completed observations, candidates ready for review and terminal failures.
It sends no email, chat message or other external notification. Operations home
reads a small metadata summary; it never hashes files just to display the page.

## Evidence boundary and remaining work

The stage acceptance uses real authenticated UI/native commands, actual fictitious
Markdown files, and a separate local PostgreSQL instance for row and schema
changes. No model or publication is invoked by that observation acceptance.
Candidate crash recovery and cancelled-write fencing use controlled builders in
tests. Those tests do not establish arbitrary upstream or provider exactly-once
behavior. Whole-workspace checkpoints and configured queue/outbox recovery are
recorded privately with the stage commit and actual light-mode flow captures.

This stage is a collection/preparation loop. Automatic learning from verified human
decisions, policy-authorized publication/action effects, managed worker enrollment
and renewal, document-repository pilots, large-source CDC and the remaining P0–P3
roadmap are not implied by collection completion.
