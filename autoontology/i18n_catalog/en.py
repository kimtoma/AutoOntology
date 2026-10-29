# SPDX-License-Identifier: MIT
"""English CLI messages (the reference catalogue: every key lives here)."""

MESSAGES: dict[str, str] = {
    # language switch hint (printed at the top of init, in the *other* language)
    "lang.hint": "한국어: AUTOONTOLOGY_LANG=ko",
    # shared words
    "word.yes": "yes",
    "word.no": "no",
    # --- help texts -------------------------------------------------------------------
    "help.doctor": "Check Postgres, pgvector, migrations, model endpoints and ports.",
    "help.doctor.skip_models": "Skip chat/embedding calls.",
    "help.provider_app": "Show, test and switch the chat provider.",
    "help.init": (
        "Set everything up step by step: Postgres, chat provider, embeddings, documents, "
        ".env, then `setup`. Safe to run again."
    ),
    "help.init.provider": "Chat provider: ollama | claude | codex | openai (default: detected).",
    "help.init.chat_model": "Chat model for that provider.",
    "help.init.docs": (
        "Install the kordoc document engine and OCR models (default: yes with Node.js 20+)."
    ),
    "help.init.vision_model": "Optional Ollama vision model that describes images.",
    "help.init.yes": "Ask nothing: take the detected defaults (scripts, CI).",
    "help.init.env": "The .env file to write (default: <repo>/.env).",
    "help.demo": (
        "Run the whole pipeline on a sample set: start the services, register the sample "
        "sources, build, export and open the web UI."
    ),
    "help.demo.set": "Sample set: quickstart (small) | hanbit (full company).",
    "help.demo.no_open": "Do not open the web UI.",
    "help.demo.resume": "Continue the latest build, keeping its succeeded steps.",
    "help.demo.replace": (
        "Replace sources that use a sample name with other data (deletes their database "
        "and extracted concepts) without asking."
    ),
    "help.provider.status": (
        "Every chat provider: available, logged in, model, last test, and what to do."
    ),
    "help.provider.test": 'One structured test call ("pong") through the chat router.',
    "help.provider.test.provider": "ollama | openai | claude | codex (default: the active one).",
    "help.provider.use": "Switch the chat provider in .env (comments and other lines are kept).",
    "help.provider.use.chat_model": "Chat model for that provider.",
    # --- setup ------------------------------------------------------------------------
    "setup.postgres_failed": "postgres: {error}\nstart Postgres first, e.g. `docker compose up -d`",
    "setup.migrations": "migrations: database at {current} (head {head})",
    "setup.models_skipped": "models: skipped (--skip-models)",
    "setup.roles_openai": "chat and embeddings use the openai provider",
    "setup.roles_mixed": "chat uses the {provider} provider, embeddings the openai one",
    "setup.models_skipped_roles": (
        "models: skipped ({roles}; `uv run autoontology doctor` checks the endpoint)"
    ),
    "setup.models_present": "models: {models} present",
    "setup.models_pulled": " (pulled {models})",
    "setup.models_alias": (
        "models: {alias} answers {answer} with completion_tokens {tokens} (thinking off); "
        "embeddings {embed}"
    ),
    "setup.vision_present": "vision model: {model} present",
    "setup.vision_pulled": " (pulled)",
    "setup.vision_skipped": "vision model: {model} skipped (--skip-models)",
    "setup.kordoc_skipped_init": (
        "kordoc: skipped (answer yes to document support or run "
        "`uv run autoontology tools install-kordoc` later)"
    ),
    "setup.kordoc_skipped": "kordoc: skipped (pass --docs for HWP/PDF/DOCX/PPTX documents)",
    "setup.next": "next: uv run autoontology up",
    # --- source add / rm ----------------------------------------------------------------
    "source.no_such_path": "source add failed: no such file or directory: {path}",
    "source.exists": (
        "source add failed: source {name!r} already exists (remove it with `source rm {name}`)"
    ),
    "source.add_failed": "source add failed: {error}",
    "source.added_documents": "added source {name} (documents)",
    "source.added": "added source {name} ({kind}) -> {database}",
    "source.locator": "  locator: {locator}",
    "source.files": "  files ({count}): {names}",
    "source.unknown_kind": "unknown --kind {kind!r} (structured | documents)",
    "source.next_build": "next: uv run autoontology build",
    "source.unknown": "unknown source {name!r}",
    "notice.removed_source": "removed source {name}",
    "notice.removed_documents_source": (
        "removed documents source {name} (documents, chunks, concepts, relations, evidence)"
    ),
    "notice.dropped_database": "dropped database {name}",
    "notice.graph_deleted": "ingested graph of {name} deleted",
    "notice.no_connection": "no upstream connection {name} (already gone)",
    "notice.connection_deleted": (
        "deleted upstream connection {name}; waiting for the service to delete its "
        "ingested graph ..."
    ),
    "notice.drop_db_ignored": "--drop-db ignored: only files sources own a local database",
    # --- doctor -------------------------------------------------------------------------
    "doctor.header.check": "CHECK",
    "doctor.header.status": "STATUS",
    "doctor.header.detail": "DETAIL",
    "doctor.header.remedy": "REMEDY",
    # remedy descriptions: the English values are exactly the texts doctor.py produces
    # (split on "; "); commands in backticks stay as they are in every language.
    "remedy.openai_base_url": "set AUTOONTOLOGY_OPENAI_BASE_URL in .env",
    "remedy.claude_login": "run `claude auth login` (or `claude` then /login) as this user",
    "remedy.codex_login": "run `codex login` as this user (ChatGPT account)",
    "remedy.install_cli": "install the {bin} CLI and log in ({login})",
    "remedy.env_missing": "uv run autoontology init (writes .env; or cp .env.example .env)",
    "remedy.start_postgres": "start Postgres, e.g. `docker compose up -d`",
    "remedy.pgvector": (
        "uv run autoontology setup (the server needs pgvector, e.g. the docker-compose.yml image)"
    ),
    "remedy.port": "stop that process or change the port in .env",
    "remedy.start_postgres_row": "start Postgres (`docker compose up -d`)",
    "remedy.see_postgres_row": "see the postgres row",
    "remedy.prune_unowned": (
        "if no other checkout uses them: uv run autoontology tools prune-connections "
        "--include-unowned"
    ),
    "remedy.node": "install Node.js 20+ (only needed for HWP/PDF/DOCX/PPTX/XLSX documents)",
    "remedy.ocr": "run `uv run autoontology tools install-kordoc`",
    "remedy.start_ollama": "start Ollama (`ollama serve`)",
    "remedy.vision": ("start Ollama (`ollama serve`) and check AUTOONTOLOGY_VISION_MODEL in .env"),
    "remedy.profile_values": (
        "set the AUTOONTOLOGY_OPENAI_* / _CLAUDE_* / _CODEX_* values in .env (see .env.example)"
    ),
    "remedy.profile_alias": (
        "set AUTOONTOLOGY_OLLAMA_CHAT_MODEL to an openai/<name> Ollama alias "
        "(`uv run autoontology setup` creates openai/qwen3:8b)"
    ),
    "remedy.profile_pick": "pick a listed model (see .env.example)",
    "remedy.embed_provider": (
        "check AUTOONTOLOGY_EMBED_PROVIDER and its model in .env (see .env.example)"
    ),
    "remedy.no_think": "keep AUTOONTOLOGY_OLLAMA_NO_THINK on / upgrade-check Ollama",
    "remedy.embedder_changed": (
        "embedder changed after ingest: restore the old EMBED model, or "
        "`autoontology source rm` every source and empty the vdb tables"
    ),
    "remedy.none_needed": "none needed: the next build recreates the empty table",
    "remedy.check_embed_pg": "check EMBED_* and Postgres",
    "remedy.openai_env": "check AUTOONTOLOGY_OPENAI_BASE_URL/API_KEY/CHAT_MODEL in .env",
    "remedy.cli_chat": "uv run autoontology doctor (chat provider row)",
    "remedy.check_embed": "check EMBED_* in .env",
    "remedy.check_embed_up": "check EMBED_* in .env / that the model server is up",
    # --- init ---------------------------------------------------------------------------
    "init.step.postgres": "Postgres",
    "init.step.provider": "Chat provider",
    "init.step.embeddings": "Embeddings",
    "init.step.documents": "Documents (kordoc: HWP/PDF/DOCX/PPTX/XLSX and image OCR)",
    "init.step.env": "Write {path}",
    "init.step.setup": "Setup (database, migrations, models)",
    "init.step.setup_docs": "Setup (database, migrations, models, kordoc)",
    "init.step.summary": "Summary",
    "init.bad_provider": "--provider must be one of {choices}, got {value!r}",
    "init.bad_model": "{flag} {value!r} is not a valid model name",
    "init.pg_fix_docker": "  fix:  start the bundled Postgres in this folder: `docker compose up -d`",
    "init.pg_fix_install": (
        "  fix:  install Docker (https://docs.docker.com/get-docker/) and run "
        "`docker compose up -d` in this folder"
    ),
    "init.pg_fix_own": (
        "        or set POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD in "
        "{env_path} to your own Postgres 17 with pgvector"
    ),
    "init.pg_retry": (
        "  Postgres is not reachable at {where}. Start it (docker compose up -d) and "
        "press Enter to retry, or n to stop"
    ),
    "init.pg_stopped": (
        "init stopped: Postgres is not reachable. Apply the fix above, then run "
        "`uv run autoontology init` again."
    ),
    "init.not_ready": "not ready",
    "init.fix": "  fix:  {remedy}",
    "init.login_stopped_yes": (
        "init stopped: the {name} CLI is not logged in. Run `{command}` in a terminal, then "
        "run init again (or pick another provider with --provider)."
    ),
    "init.login_retry": "  Retry after `{command}`?",
    "init.still_not_ready": "  still not ready: {note}",
    "init.login_stopped": (
        "init stopped: {name} is not logged in. Run `{command}`, then "
        "`uv run autoontology init` again (or choose another provider)."
    ),
    "init.col.provider": "PROVIDER",
    "init.col.found": "FOUND",
    "init.col.logged_in": "LOGGED IN",
    "init.col.note": "NOTE",
    "init.current_vs_recommended": "  note  current: {current}; recommended: {recommended}",
    "init.keeping": "  ok    keeping the current provider {current}",
    "init.keeping_switch": " (recommended: {recommended}; pass --provider {recommended} to switch)",
    "init.prompt_provider": "  Chat provider",
    "init.prompt_provider_current": (
        "  Chat provider (current: {current}; recommended: {recommended})"
    ),
    "init.ollama_missing": (
        "  warn  Ollama is not answering ({note}): install it from https://ollama.com "
        "and start it (`ollama serve`); setup pulls the models"
    ),
    "init.prompt_base_url": "  Base URL (including /v1)",
    "init.openai_base_needed": (
        "init stopped: the openai provider needs a base URL such as "
        "https://api.openai.com/v1 (set AUTOONTOLOGY_OPENAI_BASE_URL in .env, or run "
        "init without --yes)"
    ),
    "init.prompt_chat_model": "  Chat model{hint}",
    "init.model_needed": (
        "init stopped: the {provider} provider needs a chat model name "
        "(pass --chat-model, or run init without --yes)"
    ),
    "init.chat_ok": "  ok    chat: {provider} {model}",
    "init.openai_key_note": (
        "  note  Add AUTOONTOLOGY_OPENAI_API_KEY=… to .env yourself "
        "({env_path}; never paste keys into chats or shells with history)"
    ),
    "init.prompt_embed_provider": "  Embeddings provider",
    "init.embed_kept": "  ok    embeddings: openai {model} (current, kept)",
    "init.model_not_set": "(model not set)",
    "init.prompt_embed_model": "  Embedding model",
    "init.embed_model_needed": (
        "init stopped: openai embeddings need a model name "
        "(set AUTOONTOLOGY_OPENAI_EMBED_MODEL in .env, or run init without --yes)"
    ),
    "init.embed_openai": "  ok    embeddings: openai {model} at {base}",
    "init.embed_present": "present",
    "init.embed_pulled_by_setup": "pulled by setup",
    "init.embed_needs_ollama": "needs Ollama running (https://ollama.com, `ollama serve`)",
    "init.embed_ollama": "  ok    embeddings: ollama {model} ({state})",
    "init.docs_need_node": "  warn  --docs needs Node.js 20 or newer",
    "init.node_not_found": "Node.js 20+ not found",
    "init.docs_skip": (
        "  skip  {detail}. Markdown, text, HTML, CSV and .doc documents still work. For the "
        "others install Node.js 20+ (https://nodejs.org), then run "
        "`uv run autoontology tools install-kordoc`"
    ),
    "init.prompt_docs": "  Install kordoc and its OCR models (~18 MB) now?",
    "init.docs_yes": "  ok    documents: kordoc + OCR models via setup",
    "init.docs_no": (
        "  ok    documents: skipped (later: `uv run autoontology tools install-kordoc`)"
    ),
    "init.env_created": "  ok    created {path} from .env.example",
    "init.env_set": "  set   {change}",
    "init.env_unchanged": "  ok    no changes (already set)",
    "init.env_backup": "  ok    backup of the previous file: {path}",
    "init.warn": "  warn  {warning}",
    "init.shell_override": (
        "your shell sets {key}={value}, which wins over .env: run `unset {key}` "
        "(and remove it from your shell profile)"
    ),
    "init.setup_stopped": (
        "init stopped during setup (see the line above): fix it, then run "
        "`uv run autoontology init` again (it is safe to repeat)"
    ),
    "init.col.item": "ITEM",
    "init.col.value": "VALUE",
    "init.sum.documents_kordoc": "kordoc + OCR",
    "init.sum.documents_native": "native formats only",
    "init.sum.vision_off": "off (images: OCR text only)",
    "init.next": "\nnext: {command}",
    # --- demo ---------------------------------------------------------------------------
    "demo.bad_set": "demo: {error}",
    "demo.step.services": "Services",
    "demo.step.samples": "Sample sources ({set})",
    "demo.step.build": "Build",
    "demo.step.export": "Export",
    "demo.step.ui": "Web UI",
    "demo.live_connections": (
        "  note  upstream connections with no source in this checkout (kept: another "
        "checkout may use them): {names}"
    ),
    "demo.pruning": (
        "  note  pruning upstream connections whose database is gone (they would make "
        "every ingest time out): {names}"
    ),
    "demo.prune_backend": (
        "  warn  could not check the upstream connections ({error}); if the build times "
        "out, run `{command}`"
    ),
    "demo.prune_postgres": (
        "  warn  could not check the upstream connections: Postgres is not reachable "
        "({error}); start it (`docker compose up -d`), then `uv run autoontology doctor`"
    ),
    "demo.prune_other": (
        "  warn  could not check the upstream connections ({error}); see "
        "`uv run autoontology doctor`"
    ),
    "demo.conflict": "  {name}: registered for {registered} ({kind}), the sample is {sample}",
    "demo.confirm_replace": (
        "  Replace source {name} (its database and extracted concepts will be deleted)?"
    ),
    "demo.kept_stop": (
        "demo stopped: source {name!r} was kept. Re-run with --replace to replace it, or "
        "remove it yourself (`uv run autoontology source list`)"
    ),
    "demo.register_failed": (
        "demo: could not register the sample sources: {error}\n"
        "see `uv run autoontology source list`; remove a conflicting source with "
        "`uv run autoontology source rm <name>`"
    ),
    "demo.expected": ("  expected time for the first build of {set} (later builds reuse results):"),
    "demo.col.provider": "PROVIDER",
    "demo.col.time": "TIME",
    "demo.col.note": "NOTE",
    "demo.col.step": "STEP",
    "demo.col.status": "STATUS",
    "demo.chat_provider": (
        "  chat provider: {provider} {model} (switch with `uv run autoontology provider use "
        "<name>`)"
    ),
    "demo.resume": "demo stopped: continue with `uv run autoontology demo --resume`",
    "demo.build_total": "  build total: {seconds:.1f}s",
    "demo.finished": "\ndemo finished in {seconds:.1f}s",
    "demo.look_next": "What to look at next: Ontology → Graph → Playground",
    # --- provider -----------------------------------------------------------------------
    "provider.col.provider": "PROVIDER",
    "provider.col.available": "AVAILABLE",
    "provider.col.logged_in": "LOGGED IN",
    "provider.col.model": "MODEL",
    "provider.col.last_test": "LAST TEST",
    "provider.col.remedy": "REMEDY",
    "provider.seconds": "{seconds}s",
    "provider.last_ok": "ok",
    "provider.last_failed": "failed",
    "provider.active": "* = active chat provider ({provider}, from {origin})",
    "provider.test_ok": "{name} ok: {model} answered {answer!r} in {seconds}s (via {via})",
    "provider.test_failed": "{name} failed: {error} ({seconds}s)\nfix: {remedy}",
    "provider.no_answer": "no answer",
    "provider.see_status": "see `uv run autoontology provider status`",
    "provider.unknown": "unknown provider {name!r}: choose one of {choices}",
    "provider.set": "set {change} in {path}",
    "provider.unchanged": "{path} already selects {name}; nothing changed",
    "provider.backup": "backup: {path}",
    "provider.warning": "warning: {warning}",
    "provider.openai_warning": (
        "warning: set AUTOONTOLOGY_OPENAI_BASE_URL, AUTOONTOLOGY_OPENAI_CHAT_MODEL and "
        "AUTOONTOLOGY_OPENAI_API_KEY in {path} (type the key yourself)"
    ),
    "provider.not_ready": "warning: {name} is not ready ({note}): {remedy}",
    "provider.restart": "restart: {command}",
    "help.provider.use.embed": "Embedding provider: ollama | openai.",
    "help.provider.use.embed_model": "Embedding model of that embedding provider.",
    "help.provider.use.base_url": (
        "Base URL of the OpenAI-compatible endpoint (AUTOONTOLOGY_OPENAI_BASE_URL)."
    ),
    "help.provider.use.openai_chat_model": (
        "Chat model of the OpenAI-compatible endpoint (AUTOONTOLOGY_OPENAI_CHAT_MODEL)."
    ),
    "help.restart": (
        "Restart the service and the backend so both load the current .env (after a "
        "provider change)."
    ),
    "help.restart.only": "Restart only this process: backend.",
    "provider.unknown_embed": "unknown embedding provider {name!r}: choose one of {choices}",
    "provider.bad_url": "{flag} {value!r} must be an http:// or https:// URL",
    "provider.key_hint": (
        "the API key is never taken on the command line: enter it on the Settings page "
        "({url}) or type AUTOONTOLOGY_OPENAI_API_KEY into {path} yourself"
    ),
    "provider.restart_short": "or: `uv run autoontology restart`",
    "restart.failed": "restart failed (exit {code}); log: {log}",
    "restart.bad_only": "--only must be one of {choices}, got {value!r}",
}
