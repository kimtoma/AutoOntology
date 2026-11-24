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
    "help.exports": "The export folders (exports/r<rev>/): list, show, save a file or a zip.",
    "help.exports.list": "List the export folders and their files.",
    "help.exports.show": (
        "List a revision's files, or print one text file (e.g. ossie/validation.json)."
    ),
    "help.exports.rev": "Revision: 3 or r0003.",
    "help.exports.path": "File inside the folder, e.g. report.md or okf/validation.json.",
    "help.exports.get": "Save one export file.",
    "help.exports.out": "Target file (or directory).",
    "help.exports.zip": "Save the whole export folder as one zip.",
    "help.exports.zip.out": "Target .zip file (or directory: autoontology-r<rev>.zip).",
    "help.playground": (
        "The Playground over the running service: chat (RAG with citations), sql, sparql, index."
    ),
    "help.playground.chat": "Ask the ontology a question: streams the answer, then the citations.",
    "help.playground.question": "The question.",
    "help.playground.conversation": "Continue this conversation (its id is printed after an answer).",
    "help.playground.top_k": "Elements to retrieve (1-40).",
    "help.playground.json": "JSON Lines: one SSE event object per line.",
    "help.playground.sql": (
        "Ask the data in natural language (the upstream SQL agent). Rule or definition "
        "questions may be answered without SQL. An English answer to a Korean question is "
        "printed in Korean (AUTOONTOLOGY_SQL_TRANSLATE=0 turns this off; --json has both)."
    ),
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
    "ops.error.no_structured_source": (
        "no_structured_source: this project has no built structured source (CSV, Excel, "
        "Parquet or a database); add one and run `uv run autoontology build` to ask "
        "questions about data"
    ),
    "ops.notice.conversation_store_missing": (
        "asked without the earlier turns: the upstream conversation store "
        "(frontend schema) is missing"
    ),
    "ops.sql.node.react_sql_agent": "Searching the data and writing SQL",
    "ops.sql.node.format_and_respond": "Formatting the answer",
    "ops.sql.node.unconstructable_sql_response": "SQL could not be constructed",
    "ops.sql.translating": "Translating the answer into Korean",
    "ops.sql.no_sql": "Answered without SQL (a rule or definition question).",
    "ops.sql.unconstructable": (
        "The agent could not write SQL for this question from the data it found; "
        "try naming the table or measure you mean."
    ),
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
    "help.agent": (
        "Outside agents (Claude Code, Cursor, scripts): bearer tokens for the MCP endpoint "
        "(/mcp) and the REST API (/agent/v1), and their usage."
    ),
    "help.agent.tokens": "Agent tokens: list, create, rotate, revoke.",
    "help.agent.tokens.list": "List the tokens (never the token itself).",
    "help.agent.tokens.create": "Create a token; it is printed once.",
    "help.agent.tokens.create.name": "A name for the agent, e.g. claude-code.",
    "help.agent.tokens.create.revision": "Serve only this revision (default: the latest).",
    "help.agent.tokens.create.expires": "Expire after 30 or 90 days (default: never).",
    "help.agent.tokens.rotate": "Replace a token: the old one stops at once.",
    "help.agent.tokens.revoke": "Revoke a token (its call history stays).",
    "help.agent.tokens.id": "Token id (tok_..., see `agent tokens list`).",
    "help.agent.usage": "Agent calls: totals by tool, by token and by day.",
    "help.agent.usage.days": "Only the last N days (default: every kept call).",
    "ops.agent_token_once": "This token is shown only once: copy it now.",
    "ops.agent_connect_hint": (
        "connect Claude Code: claude mcp add --transport http autoontology "
        '<origin>/mcp --header "Authorization: Bearer <token>"'
    ),
    "ops.agent_no_tokens": "no agent tokens yet: `uv run autoontology agent tokens create <name>`",
    "ops.agent_no_calls": "no agent calls yet",
    "ops.agent_revoked": "revoked {id} ({name})",
    "ops.agent_bad_expiry": "--expires-days must be 30 or 90, got {value}",
    "ops.agent_usage_total": "{total} call(s), {ok} ok",
    "help.opt.project": "The project to work on (its slug; default: the default project).",
    "project.unknown": "unknown project {project!r}: `uv run autoontology project list` shows the projects",
    "project.archived": "project {project!r} is archived: `uv run autoontology project unarchive {project}` first",
    "help.project": "Projects: each has its own sources, builds, revisions, review, exports, playground and agent tokens.",
    "help.project.list": "List the projects with their latest revision, sources and last build.",
    "help.project.archived": "Include archived projects.",
    "help.project.show": "Show one project.",
    "help.project.ref_default": "Project slug (default: --project, else the default project).",
    "help.project.ref": "Project slug.",
    "help.project.create": "Create a project.",
    "help.project.slug": "Slug: 2-24 lower-case letters, digits and '-', starting with a letter.",
    "help.project.name": "Display name.",
    "help.project.description": "Description.",
    "help.project.rename": "Change a project's name, description or slug (a new slug moves its exports folder).",
    "help.project.new_slug": "New slug (refused while a run of the project is active).",
    "help.project.archive": "Archive a project: read-only and hidden from lists; its data stays.",
    "help.project.unarchive": "Make an archived project writable again.",
    "ops.project_revision": "  latest revision: {revision}",
    "ops.project_sources": "  sources: {sources}",
    "ops.project_last_build": "  last build: {build}",
    "ops.project_archived_at": "  archived at {at}",
    "ops.project_created": "created project {slug} ({name}); use it with `uv run autoontology -p {slug} ...`",
    "ops.project_renamed": "project {slug} ({name}) updated",
    "ops.project_nothing_to_change": "give --name, --slug or --description",
    "ops.project_archived": "archived project {slug} (read-only; `project unarchive {slug}` undoes it)",
    "ops.project_unarchived": "project {slug} is writable again",
    "help.user": "Accounts (server-side admin; used when AUTOONTOLOGY_AUTH=accounts): create, list, disable, enable, sign-in links.",
    "help.user.create": "Create a user; without --password-stdin, print a one-time invitation link to set the password.",
    "help.user.list": "List the users.",
    "help.user.disable": "Disable a user: signed out now and refused from then on.",
    "help.user.enable": "Enable a disabled user again.",
    "help.user.link": "Print a one-time link for a user (when mail is not configured).",
    "help.user.email": "The user's email address.",
    "help.user.name": "Display name.",
    "help.user.admin": "Make the user an admin (every project, users, settings).",
    "help.user.password_stdin": "Read the password from standard input (one line, at least 12 characters).",
    "help.opt.as": "Attribute the command in the audit log to this user (email; default: cli).",
    "help.member": "Project members (server-side admin): list, add, role, remove. The project is --project/-p.",
    "help.member.list": "List the project's members and their roles.",
    "help.member.add": "Add a user to the project (an unknown email becomes a new user with an invitation link).",
    "help.member.set_role": "Change a member's role.",
    "help.member.remove": "Remove a member from the project (a project keeps at least one owner).",
    "help.member.role": "owner, editor, reviewer or viewer.",
    "help.audit": "Show the audit log (sign-ins, users, members, tokens, runs, reviews, settings), newest first.",
    "help.audit.project": "Only this project's entries (default: --project/-p, else every project).",
    "help.audit.user": "Only this user's entries (email).",
    "help.audit.action": "Only this action, e.g. member.add or run.start.",
    "help.audit.limit": "At most this many entries.",
    "ops.member_unknown_project": "unknown project {ref!r}",
    "ops.member_bad_role": "the role must be owner, editor, reviewer or viewer, not {role!r}",
    "ops.member_none": "project {slug} has no members yet",
    "ops.member_added": "added {email} to {slug} as {role}",
    "ops.member_role_set": "{email} is now {role} in {slug}",
    "ops.member_removed": "removed {email} from {slug}",
    "ops.audit_none": "no audit entries",
    "help.user.purpose": "magic (sign in), reset (choose a new password) or invite.",
    "ops.user_created": "created user {email}{admin}",
    "ops.user_invite_hint": "Send this invitation link to the user: it sets the password and signs in.",
    "ops.user_link_once": "The link works once and expires at {expires} (UTC). Anyone with it can sign in as the user: send it privately.",
    "ops.user_accounts_off": "note: accounts are off; the link works once AUTOONTOLOGY_AUTH=accounts is set and the service restarted",
    "ops.user_none": "no users yet: `uv run autoontology user create <email> --admin` creates the first admin",
    "ops.user_unknown": "no user with email {email!r}",
    "ops.user_enabled": "user {email} is enabled",
    "ops.user_disabled": "user {email} is disabled and signed out",
    "ops.user_bad_purpose": "--purpose must be magic, reset or invite, not {purpose!r}",
    "ops.user_is_disabled": "user {email} is disabled: `uv run autoontology user enable {email}` first",
    "help.object": "Objects: the rows of classes bound to a source table (types, bind, list, get).",
    "help.object.types": "List the object types: proposed, active, broken and retired.",
    "help.object.types.status": "Only this status (proposed, active, broken, retired).",
    "help.object.type": "Object type: its name (term-...) or its label.",
    "help.object.bind": "Bind an object type: key and title columns, verified on the source; the type becomes active.",
    "help.object.bind.key": "The key column (unique, never empty; default: keep or infer).",
    "help.object.bind.title": 'The title column ("" for none; default: keep or infer).',
    "help.object.list": "List or search the objects of a type (values include action edits).",
    "help.object.list.q": "Search the key and title.",
    "help.object.list.filter": "column=value (repeatable).",
    "help.object.list.offset": "Skip this many objects.",
    "help.object.get": "One object: values, edits (with conflicts) and links.",
    "help.object.get.key": "The object's key value.",
    "ops.object_no_types": "no object types yet: build the ontology first (`uv run autoontology build`)",
    "ops.object_none": "no objects",
    "ops.object_page": "{shown} of {total} object(s) from offset {offset}",
}
