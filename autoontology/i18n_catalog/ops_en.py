# SPDX-License-Identifier: MIT
"""English texts of the UI-parity commands (``autoontology/cli_ops.py``)."""

MESSAGES: dict[str, str] = {
    "help.playground.sparql.examples": "List the example queries of the Playground page.",
    "ops.no_such_dir": "no such directory: {path}",
    "help.opt.json": "Print the answer as JSON (the web API's body).",
    "help.opt.limit": "At most this many rows.",
    "help.opt.page": "Page number (from 1).",
    "help.opt.size": "Rows per page.",
    "help.opt.revision": "Snapshot revision (default: the latest).",
    "help.review.limit": "Rows per page (same as --size).",
    "help.review.size": "Rows per page (default: --limit).",
    "help.ontology": (
        "The ontology snapshots: without a subcommand, a summary of the latest one "
        "(or --revision N); revisions, diff, concepts, concept, graph read the store "
        "directly (the service may be down)."
    ),
    "help.ontology.revisions": "List the snapshot revisions, newest first.",
    "help.ontology.diff": "What changed between two revisions (default: the previous -> the latest).",
    "help.ontology.diff.from": "Older revision (default: the one before --to).",
    "help.ontology.diff.to": "Newer revision (default: the latest).",
    "help.ontology.concepts": "List or search the concepts (Term classes and document concepts).",
    "help.ontology.concepts.q": "Search text (name, synonyms, definition).",
    "help.ontology.concepts.kind": "Only this kind (term, entity, process, ...).",
    "help.ontology.concepts.origin": "Only this origin: structured | document | aligned.",
    "help.ontology.concepts.source": "Only concepts of this source.",
    "help.ontology.concept": (
        "One concept: definition, synonyms, rejected aliases, alignments with their status, "
        "evidence quotes and review decisions."
    ),
    "help.ontology.concept.id": "Concept id (term-<id> or concept-<id>, see `ontology concepts`).",
    "help.ontology.graph": "The graph the Graph page draws (nodes and edges).",
    "help.ontology.graph.layer": "all | structured | documents | aligned.",
    "help.ontology.graph.evidence": "Also add the evidence nodes.",
    "help.ontology.graph.out": "Write the graph JSON to this file.",
    "help.runs": (
        "Recent runs and their steps (without a subcommand); show, logs --follow and cancel "
        "talk to the running service."
    ),
    "help.runs.id": "Run id (see `uv run autoontology runs`).",
    "help.runs.show": "One run with its steps and progress (needs the service).",
    "help.runs.logs": (
        "Print a run's log (masked). --follow streams the live events from the service "
        "until the run ends."
    ),
    "help.runs.logs.follow": "Stream new lines from the service until the run ends.",
    "help.runs.logs.json": "JSON Lines: one event object per line.",
    "help.runs.cancel": "Cancel a run the service is running.",
    "help.sources.freshness": (
        "Which sources changed since their last build: source, kind, stale, reasons, last built."
    ),
    "help.sources.refresh": "Accepted for UI parity: the CLI always computes it now.",
    "help.samples": "The bundled sample sets: list them, register one (without building).",
    "help.samples.list": "List the sample sets, their sources and whether they are registered.",
    "help.samples.load": (
        "Register a sample set's sources (no build: run `uv run autoontology build` next)."
    ),
    "help.samples.load.set": "Sample set: quickstart | hanbit.",
    "help.samples.load.replace": "Replace registered sources of the same name.",
    "help.exports": "The export folders (exports/r<rev>/): list, show a file, save a file.",
    "help.exports.list": "List the export folders and their files.",
    "help.exports.show": (
        "List a revision's files, or print one text file (e.g. ossie/validation.json)."
    ),
    "help.exports.rev": "Revision: 3 or r0003.",
    "help.exports.path": "File inside the folder, e.g. report.md or okf/validation.json.",
    "help.exports.get": "Save one export file.",
    "help.exports.out": "Target file (or directory).",
    "help.playground": (
        "The Playground over the running service: chat (RAG with citations), sql, sparql, index."
    ),
    "help.playground.chat": "Ask the ontology a question: streams the answer, then the citations.",
    "help.playground.question": "The question.",
    "help.playground.conversation": "Continue this conversation (its id is printed after an answer).",
    "help.playground.top_k": "Elements to retrieve (1-40).",
    "help.playground.json": "JSON Lines: one SSE event object per line.",
    "help.playground.sql": "Ask the data in natural language (the upstream SQL agent).",
    "help.playground.sql.source": "Structured source to query (default: the first one).",
    "help.playground.sql.conversation": "Conversation UUID to continue.",
    "help.playground.sparql": "Run a SPARQL query over the latest snapshot.",
    "help.playground.sparql.query": "The query (or --file).",
    "help.playground.sparql.file": "Read the query from this file.",
    "help.playground.sparql.limit": "Maximum rows.",
    "help.playground.index": "Show the chat index status; --rebuild (re)builds it.",
    "help.playground.index.rebuild": "Build the index for the latest snapshot now.",
    "help.settings": (
        "The Settings page: show, test and save the model provider; restart the service."
    ),
    "help.settings.show": "What .env selects, where each value comes from, logins, vectors (never the key).",
    "help.settings.test": (
        "Test a candidate without saving it: one chat pong and one embedding (dimension "
        "checked against the stored vectors)."
    ),
    "help.settings.set": (
        "Save a candidate to .env (backup, mode 0600). The API key only through --api-key-stdin."
    ),
    "help.settings.chat_provider": "Chat provider: ollama | claude | codex | openai.",
    "help.settings.chat_model": 'Chat model of that provider ("" = its default).',
    "help.settings.embed_provider": "Embedding provider: ollama | openai.",
    "help.settings.embed_model": 'Embedding model of that provider ("" = its default).',
    "help.settings.base_url": 'OpenAI-compatible base URL ("" removes it).',
    "help.settings.api_key_stdin": (
        "Read the API key as one line from stdin (typed without echo on a terminal)."
    ),
    "help.settings.use_saved_key": "Test with the saved API key (default) or without it.",
    "help.settings.confirm_reingest": (
        "Save although the embedding dimension differs from the stored vectors."
    ),
    "help.settings.restart": (
        "Ask the running service to restart itself and the backend (the Settings page's button)."
    ),
    "help.settings.restart.wait": "Wait until the new service answers and report the result.",
    "help.settings.restart.timeout": "Seconds to wait with --wait.",
    "ops.service_down": (
        "the service is not running at {url}: start it with `uv run autoontology up`"
    ),
    "ops.no_snapshot": "no ontology snapshot yet: run `uv run autoontology build`",
    "ops.more": "{count} more (--json lists all)",
    "ops.no_concepts": "no matching concepts",
    "ops.page": "page {page}/{pages} ({total} total, revision {revision}; --page N for more)",
    "ops.written": "written: {path}",
    "ops.saved": "saved {path} ({bytes} bytes)",
    "ops.no_run_log": ("no log file for run {run}: runs started from the CLI write to {log}"),
    "ops.cancel_requested": (
        "cancel requested for {run}: it stops at the next step "
        "(`uv run autoontology runs show {run}`)"
    ),
    "ops.no_sources": "no sources (add one with `uv run autoontology source add`)",
    "ops.stale_count": "{stale} of {total} source(s) stale",
    "ops.sample_replace_hint": (
        "add --replace to replace them (their databases and extracted concepts are deleted)"
    ),
    "ops.samples_next": "next: `uv run autoontology build`",
    "ops.bad_revision": "revision must be a number like 3 or r0003, got {value!r}",
    "ops.no_exports": "no exports yet: run `uv run autoontology export`",
    "ops.conversation": "conversation {id} (continue with --conversation {id})",
    "ops.sparql_one_of": "give the query as an argument or with --file (one of them)",
    "ops.no_index": "no index yet: `uv run autoontology playground index --rebuild`",
    "ops.settings_offline": (
        "the service is not running: 'running' shows the settings this shell would start "
        "with (`uv run autoontology up`)"
    ),
    "ops.restart_required": (
        "the running service uses other settings: `uv run autoontology settings restart --wait`"
    ),
    "ops.restart_in_progress": "a restart is in progress",
    "ops.key_prompt": "API key (not echoed): ",
    "ops.key_empty": "--api-key-stdin read an empty line: no key given",
    "ops.key_and_clear": "--api-key-stdin and --clear-api-key exclude each other",
    "ops.settings_nothing": "nothing to change: give at least one setting",
    "ops.settings_written": "wrote {keys} to {path}",
    "ops.settings_unchanged": "nothing changed",
    "ops.restart_hint": (
        "restart to use them: `uv run autoontology settings restart --wait` "
        "(or `uv run autoontology restart`)"
    ),
    "ops.dimension_mismatch": (
        "the new embedding model has {new} dimensions but the stored vectors have {old}: "
        "saving needs --confirm-reingest and a rebuild of every source"
    ),
    "ops.restart_started": (
        "restart {id} started: `uv run autoontology status` shows when the service is back"
    ),
    "ops.restart_done": "restart {id} done: the service runs again (pid {pid})",
    "ops.restart_not_done": "restart {id}: {state} (logs in {log})",
}
