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
