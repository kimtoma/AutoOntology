# Ontology Ops

After the first successful build, the home page becomes an operations dashboard.
Initial setup remains available at `/ui/setup`, without occupying a primary
navigation item. The primary navigation is Home, Review, Sources, Ontology,
Questions and History; settings remain in the navigation footer.

## Operating workflow

1. Inspect source freshness on Home or Sources. Unknown freshness is reported as
   unknown, rather than presented as unchanged.
2. Analyze changed sources to create a candidate. The published ontology remains
   available to graph browsing, grounded chat, SPARQL and default downloads.
3. In Review, compare current and proposed knowledge with its original evidence.
   Approve, hold or exclude individual proposals. Exclusion requires a reason.
   Names and descriptions can be edited before approval, with a required reason;
   source quotes and identity fields cannot be edited. Connection decisions can
   be held, resumed and materialized into a candidate separately.
4. Configure representative questions in History, then validate the approved
   subset. Validation builds its actual retrieval index and calls the configured
   chat provider. Each configured question must return the expected phrase and
   valid citations. This is a repeatable smoke check, not a general semantic
   correctness assessment. With no questions configured, the UI explicitly says
   that question validation was not performed.
5. Publish the validated subset. All version-specific search vectors and export
   files must be ready before the published pointer changes. A failed preparation
   leaves the previous published version active. Pending, held and excluded
   proposals stay out of the published model.
6. Review answer feedback in Review. The question, completed answer and requested
   correction remain available until a reviewer marks the request resolved.

## Version and data behavior

The first Operations API request pins the existing completed snapshot and creates
additive `ops_*` tables in the workspace SQLite store. No existing source data or
snapshot is rewritten. Workspaces that have never used Operations retain the
legacy latest-snapshot behavior.

The default UI, CLI and exporter share the same operational snapshot selection.
Managed workspaces use the pinned published version, including when a newer
candidate exists. A missing managed head does not fall back to a candidate.
New CLI/web workspaces start with no published head. The first successful build
is a candidate: review its changes, configure at least one representative question
and expected answer, validate, and explicitly publish. Existing operational
workspaces keep their published version; legacy workspaces with completed
snapshots retain their migration baseline when they opt in to Operations.

The initial home screen links to the review inbox and publication checks until
the first publication exists. Empty question suites block publication before any
model call. Changing a question, decision, source, candidate or model profile
invalidates its validation token. Successful text-and-citation checks are a
minimum gate, not proof of full business correctness; expanded deterministic
evaluations and citation-support checks are subsequent roadmap work.

The same workflow is available from the CLI:

```sh
uv run autoontology ops list
uv run autoontology ops decide CHANGE_ID --revision 1 --decision accept --note 'Original checked'
uv run autoontology ops check 'Who approves this contract?' --expected 'Director'
uv run autoontology ops validate --revision 1
uv run autoontology ops publish --revision 1 --token VALIDATION_TOKEN
```

`ops validate` prints JSON and exits 1 for a failed/blocked suite. The token binds
the validation to content and is not an authentication credential. CLI mutations
and validation use the same workspace lease as web edits and publication. A new
`demo` build opens the UI for review instead of creating operational downloads
before its first approval.

Use an explicit revision and preview flag to inspect an unpublished snapshot:

```sh
uv run autoontology ontology
uv run autoontology ontology --revision 7 --preview
uv run autoontology export --revision 7 --preview --out ./private-preview --format okf
```

Preview exports require a separate output parent outside the configured exports
directory (including through symlinks). Their manifest and run parameters record
`preview: true`; they never change the published head or appear in operational
downloads. Historical published versions remain readable without the preview
flag. Explicit web revision reads remain available for history and read-only
candidate inspection; web export preparation only accepts operational versions.

Decisions and validation tokens are bound to content, source evidence, configured
checks and the model profile. Changed content invalidates earlier validation.
Newly published quotes receive revision-qualified identifiers, so a reused source
evidence identifier cannot overwrite another version's quote. Human text edits
and their reasons are recorded in the published decision journal; reanalyzing
identical source content does not revert those edits automatically.

Publication freezes the merged ontology, its retrieval index and exports. SQL
queries still read the connected source database live; publication does not make
a database snapshot. Structured Ossie downloads retain the upstream mappings and
record their fetch time in the export manifest.

The old graph, build, downloads and service-status routes remain accessible, now
grouped into local tabs under their relevant main section.

## Interface

The workspace console takes layout cues from [Vercel Geist](https://vercel.com/geist/introduction)
and its [Observability product](https://vercel.com/products/observability): a workspace
header, a collapsible left navigation rail, restrained surfaces, thin rules, and compact rows.
The desktop rail can fold to icons and remembers its state across reloads; on
mobile it opens as a drawer and closes after navigation.
Review uses a split workbench with source evidence beside the decision, while the
home page shows actual pending proposals, source freshness and the published
version. No activity charts or synthetic metrics are introduced. Latin text uses
Geist, metadata uses Geist Mono, and Korean uses Pretendard. Fonts and their OFL
licenses are served locally. Light and dark themes use the same layout.

## Originals and source revisions

SQLite migration v7 adds content-addressed file metadata, immutable source-input
manifests, separate capture/read receipts and last-read source references. It
does not rewrite old snapshots or delete original source files. Source deletion
does not cascade into these history tables or the original-byte archive under
`<home>/originals/`. Archive files are installed without overwriting an existing
content identity and are owner-read-only; file and directory synchronization
precedes recording successful installation. A hash mismatch or symlink is an
integrity error, not a reason to silently replace history.

The document reader and structured-file loader consume the captured copy. The
document's displayed locator remains the original locator, while its hash names
the bytes actually read. A source changing during capture fails before active
document rows are removed. Build snapshot source entries record `source_id` and
`source_revision`; legacy entries remain unchanged and may lack that reference.
Database source revisions retain ingested columns/types and declared FK-column
flags, excluding transient catalog/term IDs and connection credentials. This is
not a database-row snapshot or a complete original DDL backup.

Concept evidence and both sides of a knowledge review expose revision-qualified
original-file actions. Downloads verify the complete SHA-256, use attachment and
`nosniff` headers, and are never served by an arbitrary client-supplied file path.
Source history and CLI output contain logical filenames, not private absolute
archive locators. An explicit web revision supports read-only candidate/history
inspection; the CLI requires `--revision N --preview` for a candidate.

| Capability | API | CLI |
| --- | --- | --- |
| Inspect document originals | `GET /api/ontology/documents?revision=N` | `ops documents [--revision N --preview]` |
| Inspect input revisions and capture/read counts | `GET /api/sources/NAME/revisions` | `ops source-revisions NAME` |
| Download verified original bytes | `GET /api/ontology/documents/ID/original?revision=N` | `ops original ID --out NEW_FILE [--revision N --preview]` |
| Preserve a matching legacy original | `POST /api/ontology/documents/ID/archive?revision=N` | `ops preserve-original ID [--revision N --preview]` |

Recovery reads only the registered documents source and compares complete hashes,
so moving/renaming a file need not lose its original bytes. Changed or missing
bytes remain explicitly unavailable. Recovery records a capture receipt with
`purpose: original_recovery`; it does not claim a new build/read, change the
published ontology or mark knowledge approved. The API and CLI use the same
workspace lease for this action. CLI downloads require a new output filename and
use an atomic no-overwrite install. Provenance failures use stable error codes
such as `original_unavailable`, `original_changed` and `original_corrupt`.

This implements the original/input-history portion of P0-02. PublicationManifest,
UsageTrace, authenticated source scopes and managed publication restoration
remain separate required roadmap work. A source revision identifies preserved
inputs; it does not by itself prove which subset a mixed approved publication
contains or recreate past live SQL row values.

## Isolated local preview on this Mac

The preview checkout is `data/ontology-ops-local/app`, on
`codex/ontology-ops-local`. It uses a separate copy of the workspace, exports and
Postgres cluster. Its UI is <http://127.0.0.1:7830/ui/?lang=ko>, its backend uses
port 3131, and its Postgres uses port 55433. Existing Ollama remains shared.
Nothing in this preview changes the public checkout or Cloudflare routing.

From `/Users/macmini/Projects/AutoOntology`:

```sh
.venv/bin/python data/ontology-ops-local/control.py start
.venv/bin/python data/ontology-ops-local/control.py status
.venv/bin/python data/ontology-ops-local/control.py stop
```

The controller manages only the preview app and backend. The dedicated Postgres
cluster is managed separately using Homebrew PostgreSQL 17 `pg_ctl` and
`data/ontology-ops-local/postgres` as its data directory. Runtime configuration,
verification artifacts and secrets remain in ignored local files.

## Publication receipts

New reviewed publications record a `PublicationManifest` in schema v8. It binds
candidate and previous operational revisions, the approved model hash, decision
records, representative-question results, preserved originals and source-input
revision hashes, a dedicated frozen retrieval scope, and export hashes. The
embedding endpoint is hashed; credentials and raw endpoints are not copied into
receipts. Known reader fingerprints are recorded. Historical extractor/chat
profiles were not saved and are explicitly marked unknown rather than inferred
from the current settings. Connected database rows remain live at execution;
source revisions preserve input files or catalog schemas, not historical rows.

Publication copies the exact validated vectors without embedding passages again.
Missing/corrupt originals, changed validation vectors, nonfinite/mixed vectors,
changed decisions or failed preparation block the switch. Files and directories
are flushed before the SQLite transaction commits the operational pointer,
applied decisions, receipt and successful run/step together. A process killed
before that commit leaves only a private prepared record; one killed after it
leaves a committed successful publication. Prepared folders are not operational
downloads and are never selected as a fallback.

Operational retrieval uses and verifies the dedicated frozen index. A different
embedding model or endpoint requires validation and another publication. Export
commands reuse verified publication files without querying the current backend.
`--out` outside operational exports copies the selected format with a
`view_of_publication` marker; the full original receipt travels with that view
and its original artifact list describes the complete publication. Running an
export without a separate output returns the existing full publication folder;
it does not remove other formats. Downloads verify model, originals, source
inputs, vectors and files, and refuse damaged publications.

In **Change history**, select **Verify publication** to inspect the same record
and integrity result available through the API and CLI:

```sh
uv run autoontology ops publications
uv run autoontology ops publication --revision 12 --verify
uv run autoontology export --revision 12 --format okf --out ./verified-copy
```

`GET /api/ops/publications` lists committed receipts;
`GET /api/ops/publications/12?verify=true` verifies a version. A candidate without
a receipt is not a legacy publication. Operational versions created before this
feature remain readable, with explicit `legacy: true` and no invented historical
validation proof. Receipt inspection is separate from managed restore; restore
proposals and usage traces are still planned. Principal-backed human/agent
confirmation and complete CLI parity remain tracked in the implementation ledger.

## Query usage receipts

Schema v9 records `UsageTrace` receipts for RAG chat, standalone retrieval, SQL
and SPARQL. A request binds the selected snapshot hash and publication ID (or an
explicit legacy marker). RAG receipts preserve the query vector, actual normalized
index fingerprint, full retrieved texts/scores, exact model messages, prompt hash,
provider/model and output/citations. These are execution inputs and evidence, not
hidden model reasoning. SQL receipts preserve the actual SQL and returned values
with `live_at_execution`; they do not reconstruct historical database rows or
retain upstream internal reasoning fields. SPARQL records its query, Turtle hash
and result. Reading a receipt never re-executes a query or provider call.

Receipts start as `running`. Completion, failure, cancellation and interruption
are distinct terminal states, hashed with their saved context and output.
Cleanup cannot overwrite a terminal outcome. A killed owner's unfinished request
becomes `interrupted` on startup/listing and is not silently retried. A stream
without its completion marker or with a provider error cannot produce a successful
receipt. SQL additionally needs actual executed rows (including an empty result),
not merely a final transport message or an explanation that no SQL ran.

The UI's **Usage history** tab and answer-level **Usage record** actions show
saved results, context and elapsed request time. Recovered interruptions use wall
time through restart, including downtime; other durations use a monotonic clock.
This duration is not measured
human active time or a claim of time saved; Enterprise AX baseline/cohort
instrumentation remains separate work. Actor identity is currently explicitly
`local-unverified`; these receipts do not establish authenticated human/agent
approval. Access-policy enforcement and delegated Principals remain P0-03 work.

The CLI uses named operations against the same local service as the UI. Start
`autoontology ui` first; these commands never fall back to another store or a
privileged in-process query when the service is unavailable:

```sh
uv run autoontology ops ask 'Who approves this contract?'
uv run autoontology ops ask 'Who approves this contract?' --watch
uv run autoontology ops retrieve 'contract approval' --top-k 8
uv run autoontology ops sql 'How many customers are registered?' --source sales
uv run autoontology ops sparql 'ASK { ?s a owl:Class }'
uv run autoontology ops traces
uv run autoontology ops trace TRACE_ID
uv run autoontology ops conversations
uv run autoontology ops conversation CONVERSATION_ID
uv run autoontology ops forget-trace TRACE_ID
uv run autoontology ops forget-conversation CONVERSATION_ID
```

Normal results are JSON; `--watch` emits NDJSON stream events and a final receipt.
Failed/partial requests exit nonzero. A transport failure is not evidence of
completion: inspect the returned trace ID before deciding to retry. SQL requires
a structured source included in the operational version; it never lets the
backend choose an arbitrary unpublished database.

Deleting a conversation (including automatic pruning) removes its linked usage
receipts. Standalone receipts can be deleted explicitly, with a second action in
the UI; running receipts must first reach a terminal state. Deleting a usage
receipt does not delete a conversation's messages or alter ontology knowledge.
Historical conversations are preserved during migration with no fabricated
trace ID. Receipt responses use `Cache-Control: private, no-store`.

Coverage includes provider/preflight failures, truncated streams, disconnect
before body iteration, invalid query vectors, process death, immutable terminal
states, corruption, deletion, legacy and manifested publications, exact backup
restoration and UI/CLI inspection without repeated model calls. Complete query
file-download parity, authenticated CLI identities and managed restore remain
tracked in the broader implementation contract.
