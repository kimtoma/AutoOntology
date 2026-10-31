# SPDX-License-Identifier: MIT
"""AutoOntology command-line interface."""

from __future__ import annotations

import typer

from autoontology import __version__
from autoontology.i18n import msg
from autoontology.i18n import remedy as localized_remedy
from autoontology.ops_cli import commands as ops_commands

app = typer.Typer(
    name="autoontology",
    help="AutoOntology: build one ontology from structured and unstructured enterprise data.",
    no_args_is_help=True,
    add_completion=False,
)
source_app = typer.Typer(help="Register structured and document sources.", no_args_is_help=True)
app.add_typer(source_app, name="source")
tools_app = typer.Typer(help="Optional tooling (kordoc, web UI build).", no_args_is_help=True)
app.add_typer(tools_app, name="tools")
review_app = typer.Typer(
    help="The review inbox: list items, record decisions (then `autoontology apply`).",
    no_args_is_help=True,
)
app.add_typer(review_app, name="review")
app.add_typer(
    ops_commands(
        lambda: _settings(), lambda settings: _store(settings), lambda error: _fail(error)
    ),
    name="ops",
)


def _settings():
    from autoontology.config import Settings

    try:
        return Settings.from_env()
    except ValueError as exc:  # bad .env/shell value: show the message, not a traceback
        _fail(f"config error: {exc}", code=2)


def _unexpected(command: str, exc: Exception, log_path) -> None:
    """Unexpected error: one line plus the log path (set AUTOONTOLOGY_DEBUG=1 for the traceback)."""
    import logging
    import os

    if os.environ.get("AUTOONTOLOGY_DEBUG", "").strip() in ("1", "true", "yes"):
        raise exc
    if logging.getLogger().handlers:  # jobs.log is set up; never fall back to stderr
        logging.getLogger("autoontology.cli").error("%s failed", command, exc_info=exc)
    from autoontology.engine.jobs import short_error

    _fail(
        f"{command} failed: {short_error(exc)}\nlog: {log_path} "
        "(AUTOONTOLOGY_DEBUG=1 shows the traceback)"
    )


def _fail(message: str, code: int = 1) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


@app.command()
def version() -> None:
    """Print the AutoOntology version."""
    typer.echo(__version__)


@app.command(help=msg("help.doctor"))
def doctor(
    skip_models: bool = typer.Option(False, "--skip-models", help=msg("help.doctor.skip_models")),
) -> None:
    """Check Postgres, pgvector, migrations, model endpoints and ports."""
    from autoontology.doctor import exit_code, format_checks, run_checks

    checks = run_checks(_settings(), skip_models=skip_models)
    typer.echo(format_checks(checks))
    raise typer.Exit(code=exit_code(checks))


@app.command()
def setup(
    docs: bool = typer.Option(
        False,
        "--docs",
        help="Also install the kordoc document engine (HWP/PDF/DOCX/PPTX; needs Node.js 20+).",
    ),
    skip_models: bool = typer.Option(
        False, "--skip-models", help="Skip the Ollama model pull/alias/probe."
    ),
    force_alias: bool = typer.Option(
        False, "--force-alias", help="Rebuild the Ollama chat alias even if it exists."
    ),
    skip_ocr_models: bool = typer.Option(
        False,
        "--skip-ocr-models",
        help="With --docs: do not download kordoc's OCR models (images/scans then skipped).",
    ),
    in_init: bool = typer.Option(
        False, "--in-init", hidden=True, help="Called by init: init prints the next step."
    ),
) -> None:
    """Prepare everything (idempotent): database + extensions -> migrations -> models -> kordoc."""
    from autoontology.engine.migrate import (
        MigrationError,
        current_revision,
        head_revision,
        run_migrations,
    )
    from autoontology.engine.pg import SetupError, setup_database

    settings = _settings()
    try:
        typer.echo(f"postgres: {setup_database(settings)}")
    except SetupError as exc:
        _fail(msg("setup.postgres_failed", error=exc))
    try:
        run_migrations(settings)
    except MigrationError as exc:
        _fail(f"migrations: {exc}")
    typer.echo(msg("setup.migrations", current=current_revision(settings), head=head_revision()))
    if skip_models:
        typer.echo(msg("setup.models_skipped"))
    elif settings.chat_provider != "ollama" and settings.embed_provider == "openai":
        roles = (
            msg("setup.roles_openai")
            if settings.chat_provider == "openai"
            else msg("setup.roles_mixed", provider=settings.chat_provider)
        )
        typer.echo(msg("setup.models_skipped_roles", roles=roles))
    else:
        from autoontology.config import OLLAMA_DEFAULT_CHAT_MODEL
        from autoontology.engine.ollama import (
            OllamaSetupError,
            TemplatePatchError,
            pull_models,
            setup_ollama,
        )

        if (
            settings.chat_provider != "ollama"
            or settings.ollama_chat_model != OLLAMA_DEFAULT_CHAT_MODEL
        ):
            # only the default alias is built here; any other model is pulled as-is
            wanted = [
                settings.ollama_embed_model,
                *([settings.ollama_chat_model] if settings.chat_provider == "ollama" else []),
            ]
            try:
                pulled = pull_models(settings.ollama_endpoint, wanted, echo=typer.echo)
            except OllamaSetupError as exc:
                _fail(f"models: {exc}")
            typer.echo(
                msg("setup.models_present", models=", ".join(wanted))
                + (msg("setup.models_pulled", models=", ".join(pulled)) if pulled else "")
            )
        else:
            try:
                result = setup_ollama(
                    endpoint=settings.ollama_endpoint,
                    alias=settings.ollama_chat_model,
                    embed=settings.ollama_embed_model,
                    force=force_alias,
                    echo=typer.echo,
                )
            except (OllamaSetupError, TemplatePatchError) as exc:
                _fail(f"models: {exc}")
            typer.echo(
                msg(
                    "setup.models_alias",
                    alias=result.alias,
                    answer=repr(result.answer),
                    tokens=result.completion_tokens,
                    embed=settings.ollama_embed_model,
                )
            )
    if settings.vision_model and not skip_models:
        from autoontology.engine.ollama import OllamaSetupError, pull_models

        try:
            pulled = pull_models(settings.ollama_endpoint, [settings.vision_model], echo=typer.echo)
        except OllamaSetupError as exc:
            _fail(f"vision model: {exc}")
        typer.echo(
            msg("setup.vision_present", model=settings.vision_model)
            + (msg("setup.vision_pulled") if pulled else "")
        )
    elif settings.vision_model:
        typer.echo(msg("setup.vision_skipped", model=settings.vision_model))
    if docs:
        _install_kordoc(
            settings,
            version=None,
            omit_optional=False,
            force=False,
            skip_ocr_models=skip_ocr_models,
        )
    elif in_init:
        typer.echo(msg("setup.kordoc_skipped_init"))
    else:
        typer.echo(msg("setup.kordoc_skipped"))
    if not in_init:
        typer.echo(msg("setup.next"))


def _install_kordoc(
    settings,
    *,
    version: str | None,
    omit_optional: bool,
    force: bool,
    skip_ocr_models: bool = False,
) -> None:
    from autoontology.unstructured import kordoc

    try:
        result = kordoc.install_kordoc(
            settings.home,
            version=version or kordoc.DEFAULT_VERSION,
            omit_optional=omit_optional,
            force=force,
            echo=typer.echo,
            ocr_models=False,
        )
        engine = kordoc.KordocEngine(node=kordoc.require_node()[0], cli=result.cli, origin="home")
        reported = kordoc.engine_version(engine)
    except kordoc.KordocError as exc:
        _fail(f"kordoc: install failed: {exc.message}")
    if reported != result.version:
        _fail(f"kordoc: {result.cli} reports {reported}, expected {result.version}")
    typer.echo(
        f"kordoc: {result.action}: kordoc {reported} at {result.cli}"
        + (" (optional deps omitted: PDF/OCR unavailable)" if result.omit_optional else "")
    )
    if result.omit_optional:
        return
    if skip_ocr_models:
        typer.echo(
            "ocr models: skipped (--skip-ocr-models; images and scanned PDFs are skipped "
            f"until `{kordoc.INSTALL_COMMAND}` runs without it)"
        )
        return
    try:
        status = kordoc.download_ocr_models(settings.home, engine=engine, echo=typer.echo)
    except kordoc.KordocError as exc:
        _fail(
            f"ocr models: {exc.message}\n"
            f"retry `{kordoc.INSTALL_COMMAND}`, or pass --skip-ocr-models to go on without OCR"
        )
    typer.echo(f"ocr models: {status['detail']}")


@tools_app.command("install-kordoc")
def tools_install_kordoc(
    version: str = typer.Option(None, "--version", help="kordoc version (default: pinned)."),
    omit_optional: bool = typer.Option(
        False, "--omit-optional", help="Skip optional deps (PDF/OCR then unavailable)."
    ),
    force: bool = typer.Option(False, "--force", help="Reinstall even if present."),
    skip_ocr_models: bool = typer.Option(
        False,
        "--skip-ocr-models",
        help="Do not download the OCR models (~18 MB; images and scanned PDFs need them).",
    ),
) -> None:
    """Install the kordoc document engine into <home>/kordoc (npm, Node >= 20), then its
    OCR models."""
    _install_kordoc(
        _settings(),
        version=version,
        omit_optional=omit_optional,
        force=force,
        skip_ocr_models=skip_ocr_models,
    )


@tools_app.command("build-ui")
def tools_build_ui() -> None:
    """Rebuild the web UI (ui/ -> autoontology/web/static/dist; needs Node.js and pnpm)."""
    from autoontology.web.ui_build import BuildUiError, build_ui

    try:
        dist = build_ui(echo=typer.echo)
    except BuildUiError as exc:
        _fail(f"build-ui failed: {exc}")
    typer.echo(f"built: {dist} (served at /ui/; restart not needed)")


@tools_app.command("status")
def tools_status() -> None:
    """Show the document engine (kordoc) and Node in use."""
    from autoontology.unstructured import kordoc

    settings = _settings()
    try:
        node, nv = kordoc.require_node()
        typer.echo(f"node    {'.'.join(map(str, nv))}  {node}")
    except kordoc.KordocUnavailable as exc:
        typer.echo(f"node    missing  {exc.message}")
    try:
        engine = kordoc.resolve_engine(settings.home)
        recorded = kordoc.read_version_file(settings.home) if engine.origin == "home" else None
        note = (
            " (optional deps omitted: PDF/OCR unavailable)"
            if recorded and recorded["omit_optional"]
            else ""
        )
        typer.echo(
            f"kordoc  {kordoc.engine_version(engine)}  {engine.cli} (from {engine.origin}){note}"
        )
    except kordoc.KordocError as exc:
        typer.echo(f"kordoc  missing  {exc.message}")
        raise typer.Exit(code=1) from None


@app.command()
def ui(
    no_browser: bool = typer.Option(False, "--no-browser", help="Only print the URL."),
) -> None:
    """Print the web UI URL and open it in the browser (the service must be up)."""
    import webbrowser

    from autoontology.engine.jobs import service_alive

    settings = _settings()
    url = f"{settings.service_url}/ui/"
    typer.echo(url)
    if not service_alive(settings):
        typer.echo("the service is not running: start it with `uv run autoontology up`", err=True)
    if not no_browser:
        webbrowser.open(url)


@app.command()
def up() -> None:
    """Start the upstream backend and the AutoOntology service in the background."""
    from autoontology.engine.supervisor import Supervisor, SupervisorError

    settings = _settings()
    try:
        Supervisor.from_settings(settings, echo=typer.echo).up()
    except SupervisorError as exc:
        _fail(str(exc))


@app.command()
def down() -> None:
    """Stop the upstream backend and the AutoOntology service."""
    from autoontology.engine.supervisor import Supervisor

    Supervisor.from_settings(_settings(), echo=typer.echo).down()


@app.command()
def status() -> None:
    """Show the state of managed processes."""
    from autoontology.engine.supervisor import Supervisor, format_status

    typer.echo(format_status(Supervisor.from_settings(_settings()).status()))


# --- structured sources / builds -----------------------------------------------------


def _store(settings):
    from autoontology.store.db import Store
    from autoontology.store.operations import Operations

    store = Store.for_home(settings.home)
    if store.latest_revision() is None:
        Operations(store)
    return store


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    from autoontology.i18n import ljust, width

    widths = [max(width(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    return "\n".join(
        "  ".join(ljust(str(cell), widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in [header, *rows]
    )


@source_app.command("add")
def source_add(
    target: str = typer.Argument(..., help="File, directory, or database URL."),
    name: str = typer.Option(
        ..., "--name", help="Source name: lowercase letters, digits, _ (documents may also use -)."
    ),
    kind: str = typer.Option("structured", "--kind", help="structured | documents."),
    recursive: bool = typer.Option(False, "--recursive", help="Scan directories recursively."),
) -> None:
    """Register a source (nothing is loaded until `autoontology build`)."""
    from pathlib import Path

    from autoontology.structured import pipeline
    from autoontology.structured.loaders import LoadError

    settings = _settings()
    store = _store(settings)
    if kind == "documents":
        from autoontology.engine.extract import ensure_documents_source
        from autoontology.engine.jobs import JobError

        path = Path(target).expanduser()
        if not path.exists():
            _fail(msg("source.no_such_path", path=path))
        if store.get_source(name):
            _fail(msg("source.exists", name=name))
        try:
            src = ensure_documents_source(store, name, path, recursive)
        except (JobError, ValueError) as exc:
            _fail(msg("source.add_failed", error=exc))
        typer.echo(msg("source.added_documents", name=src.name))
        typer.echo(msg("source.locator", locator=pipeline.display_locator(src)))
    elif kind == "structured":
        try:
            src = pipeline.add_source(store, target, name=name, recursive=recursive)
        except (LoadError, ValueError) as exc:
            _fail(msg("source.add_failed", error=exc))
        typer.echo(
            msg(
                "source.added",
                name=src.name,
                kind=src.kind,
                database=pipeline.catalog_database_name(src),
            )
        )
        typer.echo(msg("source.locator", locator=pipeline.display_locator(src)))
        if src.kind == "files":
            files = pipeline.files_locator(src).files()
            typer.echo(
                msg("source.files", count=len(files), names=", ".join(f.name for f in files))
            )
    else:
        _fail(msg("source.unknown_kind", kind=kind), code=2)
    typer.echo(msg("source.next_build"))


@source_app.command("list")
def source_list() -> None:
    """List registered sources."""
    from autoontology.structured import pipeline

    store = _store(_settings())
    rows = []
    for src in store.list_sources():
        last = store.latest_run(src.id)
        rows.append(
            (
                src.name,
                src.kind,
                pipeline.catalog_database_name(src) if src.kind != "documents" else "-",
                src.catalog_database_id or "-",
                f"{last.kind} {last.status}" if last else "-",
                pipeline.display_locator(src),
            )
        )
    if not rows:
        typer.echo("no sources (add one with `autoontology source add <path> --name <name>`)")
        return
    typer.echo(_table(("NAME", "KIND", "DATABASE", "CATALOG_ID", "LAST_RUN", "LOCATOR"), rows))


@source_app.command("rm")
def source_rm(
    name: str = typer.Argument(...),
    drop_db: bool = typer.Option(False, "--drop-db", help="Also drop autoontology_src_<name>."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Remove locally even if the backend is down or graph teardown does not finish.",
    ),
) -> None:
    """Remove a source: its upstream connection (and ingested graph) and its run history.

    Waits for the ingested graph to be deleted before dropping the database, so the
    teardown never runs against a database that is already gone.
    """
    from autoontology.structured.remove import RemoveError, remove_source

    settings = _settings()
    store = _store(settings)
    src = store.get_source(name)
    if src is None:
        _fail(msg("source.unknown", name=name))
    from autoontology.i18n import notice

    try:
        remove_source(
            settings,
            store,
            src,
            drop_db=drop_db,
            force=force,
            notify=lambda message, warning: typer.echo(notice(message), err=warning),
        )
    except RemoveError as exc:
        _fail(str(exc))


@app.command()
def build(
    source: list[str] = typer.Option(
        None,
        "--source",
        help="Rebuild only this source (repeatable); align/merge/snapshot "
        "still cover every source.",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Continue the latest build run, keeping its succeeded steps."
    ),
) -> None:
    """Build every registered source into one ontology snapshot: structured (load ->
    register -> ingest -> compile), documents (read -> extract -> consolidate), align to
    the structured Terms, merge, snapshot."""
    import time

    from autoontology.engine import jobs
    from autoontology.engine.build import run_build
    from autoontology.engine.client import BackendError

    settings = _settings()
    store = _store(settings)
    started = time.monotonic()
    log_path = jobs.jobs_log_path(settings)
    jobs.setup_job_logging(settings, console=True)
    try:
        result = run_build(store, settings, sources=source or None, resume=resume)
    except (jobs.JobError, BackendError) as exc:
        hint = f"\nlog: {exc.log_path}" if getattr(exc, "log_path", None) else ""
        retry = (
            "\nretry: uv run autoontology build --resume" if getattr(exc, "run_id", None) else ""
        )
        _fail(f"build failed: {exc}{hint}{retry}")
    except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
        _unexpected("build", exc, log_path)
    elapsed = time.monotonic() - started
    if result.revision is not None:
        counts = result.counts
        typer.echo(
            f"revision {result.revision}: {counts.get('classes', 0)} classes "
            f"({counts.get('classes_structured', 0)} Term, {counts.get('classes_document', 0)} "
            f"document), {counts.get('individuals', 0)} individuals, "
            f"{counts.get('merged_concepts', 0)} concepts folded into Terms"
        )
    for warning in result.warnings:
        typer.echo(f"  warning: {warning}", err=True)
    if result.status != "succeeded":
        for problem in result.problems:
            typer.echo(f"  partial: {problem}", err=True)
        _fail(
            f"build {result.run_id}: partial in {elapsed:.1f}s (the snapshot holds what "
            f"succeeded)\nlog: {log_path}\nretry: uv run autoontology build --resume"
        )
    typer.echo(
        f"build {result.run_id}: succeeded in {elapsed:.1f}s; next: uv run autoontology export"
    )


@app.command("apply")
def apply_decisions(
    force: bool = typer.Option(
        False, "--force", help="Write a new revision even when no decision is pending."
    ),
) -> None:
    """Reflect the review decisions in a new ontology snapshot: decisions -> merge ->
    snapshot, without any model call (needs `autoontology up` for the structured Terms).
    With nothing pending it writes no revision (exit 0), so a scheduled
    `autoontology build --resume && autoontology apply` adds no empty revisions."""
    import time

    from autoontology.engine import jobs
    from autoontology.engine.apply import run_apply
    from autoontology.engine.client import BackendError

    settings = _settings()
    store = _store(settings)
    started = time.monotonic()
    log_path = jobs.jobs_log_path(settings)
    jobs.setup_job_logging(settings, console=True)
    try:
        result = run_apply(store, settings, force=force)
    except (jobs.JobError, BackendError) as exc:
        hint = f"\nlog: {exc.log_path}" if getattr(exc, "log_path", None) else ""
        _fail(f"apply failed: {exc}{hint}")
    except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
        _unexpected("apply", exc, log_path)
    run = store.get_run(result.run_id)
    applied = (run.params.get("decisions_applied") if run else 0) or 0
    if run is not None and run.params.get("skipped"):
        typer.echo(f"apply {result.run_id}: nothing to apply (revision {result.revision})")
        return
    typer.echo(
        f"apply {result.run_id}: revision {result.revision} in "
        f"{time.monotonic() - started:.1f}s; {applied} decision(s) applied"
    )


def _inbox(settings, store, *, with_sources: bool = True):
    from autoontology.ontology.model import MergedOntology
    from autoontology.web.freshness import freshness
    from autoontology.web.review import Inbox

    snaps = store.list_snapshots(2)
    models = [MergedOntology.from_dict(store.get_snapshot(s.revision).model) for s in snaps]
    rows = freshness(store, settings) if with_sources else []
    return Inbox(store, models[0] if models else None, models[1] if len(models) > 1 else None, rows)


@review_app.command("list")
def review_list(
    category: str = typer.Option(
        None,
        "--category",
        help="proposals | axioms | unaligned | aliases | stale_sources | folds (folds: only "
        "when asked for)",
    ),
    status: str = typer.Option("pending", "--status", help="pending | decided | all"),
    limit: int = typer.Option(50, "--limit", min=1, max=1000),
    as_json: bool = typer.Option(False, "--json", help="Print the items as JSON."),
) -> None:
    """List the review items (pending ones by default)."""
    import json

    from autoontology.web.review import CATEGORIES, STATUSES

    if category and category not in CATEGORIES:
        _fail(f"--category must be one of {', '.join(CATEGORIES)}", code=2)
    if status not in STATUSES:
        _fail(f"--status must be one of {', '.join(STATUSES)}", code=2)
    settings = _settings()
    listing = _inbox(settings, _store(settings)).listing(
        category=category, status=status, page=1, size=limit
    )
    if as_json:
        typer.echo(json.dumps(listing, ensure_ascii=False, indent=2, default=str))
        return
    counts = listing["counts"]
    typer.echo(
        " ".join(f"{c}={counts[c]}" for c in CATEGORIES)
        + f"  unapplied decisions={listing['unapplied']}"
    )
    rows = []
    for item in listing["items"]:
        concept = (item.get("concept") or {}).get("name") or item.get("alias") or "-"
        term = (item.get("term") or {}).get("name") or "-"
        conf = item.get("confidence")
        decision = item.get("decision")
        rows.append(
            (
                item["id"],
                concept,
                item.get("relation") or "-",
                term,
                f"{conf:.2f}" if isinstance(conf, (int, float)) else "-",
                decision["decision"] if decision else item["suggested_action"] + "?",
            )
        )
    if rows:
        typer.echo(_table(("ID", "CONCEPT", "RELATION", "TERM", "CONF", "DECISION"), rows))
    else:
        typer.echo("nothing to review")


def _decide(item_id: str, decision: str, relation, term, note: str) -> None:
    from autoontology.web.review import ReviewError

    settings = _settings()
    store = _store(settings)
    inbox = _inbox(settings, store, with_sources=False)
    try:
        d = inbox.decide(
            item_id,
            decision=decision,
            relation=relation,
            term_id=term,
            note=(note or "").strip(),
            decided_by="local",
        )
    except ReviewError as exc:
        _fail(f"{exc.error}: {exc.detail}")
    rel = f" ({d.relation})" if d.relation else ""
    typer.echo(f"{item_id}: {d.decision}{rel}; run `uv run autoontology apply` to snapshot it")


@review_app.command("accept")
def review_accept(
    item_id: str = typer.Argument(..., help="al:<id>, ax:<id> or alias:<term id>|<alias>"),
    relation: str = typer.Option(None, "--relation", help="same | related | narrower | broader"),
    term: str = typer.Option(None, "--term", help="Term id (default: the item's)."),
    note: str = typer.Option("", "--note"),
) -> None:
    """Accept an alignment proposal, an axiom or a rejected alias."""
    _decide(item_id, "accept", relation, term, note)


@review_app.command("reject")
def review_reject(
    item_id: str = typer.Argument(
        ..., help="al:<id>, ax:<id>, fold:<id> or alias:<term id>|<alias>"
    ),
    note: str = typer.Option("", "--note"),
) -> None:
    """Reject an alignment or a fold (the concept stays or becomes its own class; apply takes
    its synonyms off the Term) or keep an alias off its Term."""
    _decide(item_id, "reject", None, None, note)


@review_app.command("acknowledge")
def review_acknowledge(
    item_id: str = typer.Argument(..., help="un:<concept id>"),
    note: str = typer.Option("", "--note"),
) -> None:
    """Acknowledge an unaligned concept (it stays its own class)."""
    _decide(item_id, "acknowledge", None, None, note)


@review_app.command("undo")
def review_undo(item_id: str = typer.Argument(...)) -> None:
    """Remove the decision on an item (the align judge decides again)."""
    from autoontology.web.review import ReviewError

    settings = _settings()
    try:
        removed = _inbox(settings, _store(settings), with_sources=False).undo(item_id)
    except ReviewError as exc:
        _fail(f"{exc.error}: {exc.detail}")
    if not removed:
        _fail(f"no decision on {item_id}")
    typer.echo(f"{item_id}: decision removed; run `uv run autoontology apply` to snapshot it")


@app.command()
def runs(limit: int = typer.Option(20, "--limit")) -> None:
    """Show recent runs and their steps."""
    from autoontology.engine.build import is_top_step

    settings = _settings()
    store = _store(settings)
    names = {s.id: s.name for s in store.list_sources()}
    rows = []
    for run in store.list_runs(None, limit=limit):
        all_steps = store.get_steps(run.id)
        steps = " ".join(
            f"{s.name}={s.status}" + (f"({s.detail['seconds']}s)" if "seconds" in s.detail else "")
            for s in all_steps
            if is_top_step(s.name)
        )
        for prefix, label in (("chunk:", "chunks"), ("concept:", "concepts")):
            by_status: dict[str, int] = {}
            for s in all_steps:
                if s.name.startswith(prefix):
                    by_status[s.status] = by_status.get(s.status, 0) + 1
            if by_status:
                steps += (
                    f" {label}[" + " ".join(f"{k}={v}" for k, v in sorted(by_status.items())) + "]"
                )
        error = f" error={run.error[:80]}" if run.error else ""
        rows.append(
            (
                run.id,
                run.kind,
                names.get(run.source_id or "", "-"),
                run.status,
                (run.started_at or "-")[:19],
                steps + error,
            )
        )
    if not rows:
        typer.echo("no runs")
        return
    typer.echo(_table(("RUN", "KIND", "SOURCE", "STATUS", "STARTED", "STEPS"), rows))


@app.command()
def ontology(
    revision: int = typer.Option(
        None, "--revision", min=1, help="A published historical revision."
    ),
    preview: bool = typer.Option(
        False, "--preview", help="Inspect an explicit unpublished revision."
    ),
) -> None:
    """Show the operational ontology: revision, sources and counts."""
    from autoontology.store.operations import OperationsError, read_snapshot

    settings = _settings()
    try:
        snap = read_snapshot(_store(settings), revision, preview=preview, with_model=False)
    except OperationsError as exc:
        _fail(str(exc))
    if snap is None:
        if revision is not None:
            _fail(f"unknown revision {revision}")
        _fail("no ontology snapshot yet: build or publish an ontology first")
    if preview:
        typer.echo("PREVIEW: explicitly selected snapshot; the operational version is unchanged")
    c = snap.counts
    typer.echo(f"revision {snap.revision}  ({snap.created_at}, run {snap.run_id})")
    typer.echo(f"workspace {settings.workspace}  base IRI {settings.base_iri}")
    typer.echo(
        _table(
            ("SOURCE", "KIND", "INCLUDED", "COUNTS"),
            [
                (
                    s["name"],
                    s.get("source_kind") or s["kind"],
                    "yes" if s.get("included") else "no",
                    " ".join(f"{k}={v}" for k, v in sorted((s.get("counts") or {}).items())) or "-",
                )
                for s in snap.sources
            ],
        )
    )
    keys = (
        ("classes", "classes"),
        ("classes_structured", "Term classes"),
        ("classes_document", "document classes"),
        ("individuals", "individuals"),
        ("object_properties", "object properties"),
        ("datatype_properties", "datatype properties"),
        ("merged_concepts", "concepts folded into Terms"),
        ("documents", "documents"),
        ("evidence", "evidence"),
    )
    typer.echo(_table(("COUNT", "VALUE"), [(label, str(c.get(k, 0))) for k, label in keys]))


@app.command()
def export(
    revision: int = typer.Option(
        None, "--revision", min=1, help="Snapshot revision (default: operational)."
    ),
    out: str = typer.Option(
        None, "--out", help="Parent directory: writes <DIR>/r<rev>/ (default: exports/r<rev>/)."
    ),
    format: str = typer.Option(
        "all",
        "--format",
        help="all | rdf (ttl, jsonld, graphml, upstream/) | ossie (ossie/, upstream/) | okf. "
        "The other formats' files of the previous export of the revision are kept.",
    ),
    preview: bool = typer.Option(
        False, "--preview", help="Export an explicit revision to --out outside operational exports."
    ),
) -> None:
    """Write an ontology snapshot (default: operational) as ontology.ttl, ontology.jsonld,
    ontology.graphml, an Apache Ossie bundle (ossie/) and an OKF bundle (okf/), plus
    report.md and the structured sources' upstream exports."""
    import time
    from pathlib import Path

    from autoontology.engine import export_job, jobs
    from autoontology.engine.client import BackendError

    settings = _settings()
    started = time.monotonic()
    log_path = jobs.jobs_log_path(settings)
    store = _store(settings)
    try:
        jobs.setup_job_logging(settings, console=True)
        options = {"preview": True} if preview else {}
        result = export_job.run_export(
            store,
            settings,
            revision=revision,
            out=Path(out) if out else None,
            format=format,
            **options,
        )
    except (jobs.JobError, BackendError) as exc:
        hint = f"\nlog: {exc.log_path}" if getattr(exc, "log_path", None) else ""
        _fail(f"export failed: {exc}{hint}")
    except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
        _unexpected("export", exc, log_path)
    if preview:
        typer.echo("PREVIEW: separate export; the operational version is unchanged")
    collect = result.details.get("collect", {})
    counts = collect.get("counts", {})
    if counts:
        typer.echo(
            _table(
                (
                    "REVISION",
                    "CLASSES",
                    "TERM",
                    "DOCUMENT",
                    "OBJECT PROPS",
                    "DATA PROPS",
                    "INDIVIDUALS",
                    "DOCUMENTS",
                    "MERGED",
                ),
                [
                    (
                        str(collect.get("revision", "-")),
                        *(
                            str(counts.get(k, 0))
                            for k in (
                                "classes",
                                "classes_structured",
                                "classes_document",
                                "object_properties",
                                "datatype_properties",
                                "individuals",
                                "documents",
                                "merged_concepts",
                            )
                        ),
                    )
                ],
            )
        )
    entries = result.details.get("report", {}).get("files", [])
    for entry in entries:
        if not entry["name"].startswith("okf/"):
            typer.echo(f"  {entry['name']}  {entry['bytes']} bytes")
    bundle = [e for e in entries if e["name"].startswith("okf/")]
    if bundle:
        typer.echo(f"  okf/  {len(bundle)} files, {sum(e['bytes'] for e in bundle)} bytes")
    kept = result.details.get("report", {}).get("kept", [])
    if kept:
        typer.echo(f"  kept from the previous export of this revision: {', '.join(kept)}")
    checks = result.details.get("ossie", {}).get("validation")
    if checks:
        typer.echo(
            "ossie: "
            + ", ".join(
                f"{k} {v if isinstance(v, str) else 'errors'}"
                for k, v in checks.items()
                if k != "warnings"
            )
            + f"; {len(checks.get('warnings', []))} warning(s) (ossie/validation.json)"
        )
    okf_check = result.details.get("okf", {}).get("validation")
    if okf_check:
        typer.echo(
            f"okf: {'ok' if okf_check.get('ok') else 'errors'} "
            f"({okf_check.get('files', 0)} markdown files; okf/validation.json)"
        )
    typer.echo(f"export {result.run_id}: {result.status} in {time.monotonic() - started:.1f}s")
    if result.export_dir:
        typer.echo(f"dir: {result.export_dir}")


# --- onboarding: init / demo / provider ---------------------------------------------------

provider_app = typer.Typer(help=msg("help.provider_app"), no_args_is_help=True)
app.add_typer(provider_app, name="provider")

INIT_STEPS = 7
POSTGRES_RETRIES = 3
LOGIN_RETRIES = 3


def _step(n: int, total: int, title: str) -> None:
    typer.echo(f"\n[{n}/{total}] {title}")


def _yn(value: bool) -> str:
    return msg("word.yes") if value else msg("word.no")


def _model_for(settings, provider: str) -> str:
    import dataclasses

    return dataclasses.replace(settings, chat_provider=provider).effective_chat_model


def _shell_override_warnings(keys) -> list[str]:
    from autoontology.config import shell_value

    out = []
    for key, value in keys:
        shell = shell_value(key)
        if shell is not None and shell != value:
            out.append(msg("init.shell_override", key=key, value=shell))
    return out


def _init_postgres(yes: bool, docker: bool, env_path) -> str:
    from autoontology.engine import pg

    tries = 0
    while True:
        settings = _settings()
        try:
            return pg.setup_database(settings)
        except pg.SetupError as exc:
            typer.echo(f"  FAIL  {exc}")
        typer.echo(msg("init.pg_fix_docker" if docker else "init.pg_fix_install"))
        typer.echo(msg("init.pg_fix_own", env_path=env_path))
        tries += 1
        where = f"{settings.postgres_host}:{settings.postgres_port}"
        if (
            yes
            or tries > POSTGRES_RETRIES
            or not typer.confirm(msg("init.pg_retry", where=where), default=True)
        ):
            _fail(msg("init.pg_stopped"), code=2)
        if not yes:
            from autoontology.config import unload_env

            unload_env()  # the user may have edited POSTGRES_* in .env meanwhile


def _init_login(settings, name: str, info, yes: bool):
    from autoontology import onboarding

    command = onboarding.LOGIN_COMMANDS[name]
    typer.echo(f"  {name}: {info.note or msg('init.not_ready')}")
    typer.echo(msg("init.fix", remedy=info.remedy or command))
    if yes:
        _fail(msg("init.login_stopped_yes", name=name, command=command), code=2)
    for _ in range(LOGIN_RETRIES):
        if not typer.confirm(msg("init.login_retry", command=command), default=True):
            break
        info = onboarding.provider_info(settings, name)
        if info.ready:
            typer.echo(f"  ok    {name}: {info.note}")
            return info
        typer.echo(msg("init.still_not_ready", note=info.note))
    _fail(msg("init.login_stopped", name=name, command=command), code=2)


def _init_setup(docs: bool) -> None:
    """Step 6: the ``setup`` command itself (same code path)."""
    setup(docs=docs, skip_models=False, force_alias=False, skip_ocr_models=False, in_init=True)


@app.command(help=msg("help.init"))
def init(
    provider: str = typer.Option(None, "--provider", help=msg("help.init.provider")),
    chat_model: str = typer.Option(None, "--chat-model", help=msg("help.init.chat_model")),
    docs: bool = typer.Option(None, "--docs/--no-docs", help=msg("help.init.docs")),
    vision_model: str = typer.Option(None, "--vision-model", help=msg("help.init.vision_model")),
    yes: bool = typer.Option(False, "--yes", "-y", help=msg("help.init.yes")),
    env: str = typer.Option(None, "--env", help=msg("help.init.env")),
) -> None:
    """Set everything up step by step: Postgres, chat provider, embeddings, documents,
    .env, then `setup`. Safe to run again."""
    import os
    from pathlib import Path

    import click

    from autoontology import onboarding
    from autoontology.config import (
        CHAT_MODEL_RE,
        CHAT_PROVIDERS,
        CLI_KNOWN_MODELS,
        CLI_PROVIDERS,
        REPO_ROOT,
        env_file_path,
        unload_env,
    )
    from autoontology.i18n import other_lang_hint

    typer.echo(other_lang_hint())
    if provider is not None and provider not in CHAT_PROVIDERS:
        _fail(msg("init.bad_provider", choices=", ".join(CHAT_PROVIDERS), value=provider), code=2)
    for flag, value in (("--chat-model", chat_model), ("--vision-model", vision_model)):
        if value is not None and not CHAT_MODEL_RE.fullmatch(value):
            _fail(msg("init.bad_model", flag=flag, value=value), code=2)
    if env:
        os.environ["AUTOONTOLOGY_ENV_FILE"] = str(Path(env).expanduser().resolve())
        unload_env()
    env_path = env_file_path()
    import shutil

    _step(1, INIT_STEPS, msg("init.step.postgres"))
    typer.echo(f"  ok    {_init_postgres(yes, shutil.which('docker') is not None, env_path)}")
    settings = _settings()
    det = onboarding.detect_environment(settings, env_path)

    # 2. chat provider
    _step(2, INIT_STEPS, msg("init.step.provider"))
    rows = []
    for name in CHAT_PROVIDERS:
        info = det.providers[name]
        rows.append(
            (
                name,
                _yn(info.found),
                _yn(info.logged_in),
                info.note if info.ready else (info.remedy or info.note),
            )
        )
    typer.echo(
        "\n".join(
            "  " + line
            for line in _table(
                tuple(msg(f"init.col.{c}") for c in ("provider", "found", "logged_in", "note")),
                rows,
            ).splitlines()
        )
    )
    recommended = onboarding.recommend_provider(det)
    # an explicit choice in .env (or the shell) is kept; the recommendation is a hint
    current = (
        settings.chat_provider
        if settings.selection_origin.get("chat_provider") in (".env", "shell")
        else None
    )
    default_choice = current or recommended
    if current and current != recommended:
        typer.echo(msg("init.current_vs_recommended", current=current, recommended=recommended))
    if provider:
        choice = provider
    elif yes:
        choice = default_choice
        if current:
            typer.echo(
                msg("init.keeping", current=current)
                + (
                    msg("init.keeping_switch", recommended=recommended)
                    if current != recommended
                    else ""
                )
            )
    else:
        label = (
            msg("init.prompt_provider_current", current=current, recommended=recommended)
            if current and current != recommended
            else msg("init.prompt_provider")
        )
        choice = typer.prompt(
            label, default=default_choice, type=click.Choice(list(CHAT_PROVIDERS))
        )
    info = det.providers[choice]
    if choice in CLI_PROVIDERS and not info.ready:
        info = _init_login(settings, choice, info, yes)
    if choice == "ollama" and not info.found:
        typer.echo(msg("init.ollama_missing", note=info.note))
    keys: dict[str, str] = {"AUTOONTOLOGY_CHAT_PROVIDER": choice}
    default_model = _model_for(settings, choice)
    if choice == "openai":
        base = settings.openai_base_url
        if not yes:
            base = typer.prompt(msg("init.prompt_base_url"), default=base or None)
        base = (base or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            _fail(msg("init.openai_base_needed"), code=2)
        keys["AUTOONTOLOGY_OPENAI_BASE_URL"] = base
    model = chat_model
    if model is None:
        if yes:
            model = default_model
        else:
            hint = f" ({', '.join(CLI_KNOWN_MODELS[choice])})" if choice in CLI_KNOWN_MODELS else ""
            model = typer.prompt(
                msg("init.prompt_chat_model", hint=hint), default=default_model or None
            )
    model = (model or "").strip()
    if not model or not CHAT_MODEL_RE.fullmatch(model):
        _fail(msg("init.model_needed", provider=choice), code=2)
    keys[onboarding.CHAT_MODEL_VARS[choice]] = model
    typer.echo(msg("init.chat_ok", provider=choice, model=model))
    if choice == "openai":
        typer.echo(msg("init.openai_key_note", env_path=env_path))

    # 3. embeddings
    _step(3, INIT_STEPS, msg("init.step.embeddings"))
    current_embed = (
        settings.embed_provider
        if settings.selection_origin.get("embed_provider") in (".env", "shell")
        else None
    )
    embed = current_embed or "ollama"
    if choice == "openai":
        default_embed = current_embed or ("ollama" if det.ollama_reachable else "openai")
        embed = (
            default_embed
            if yes
            else typer.prompt(
                msg("init.prompt_embed_provider"),
                default=default_embed,
                type=click.Choice(["ollama", "openai"]),
            )
        )
    # written when chosen here or not set yet; an existing choice is kept as it is
    keep_embed = current_embed is not None and choice != "openai"
    if not keep_embed:
        keys["AUTOONTOLOGY_EMBED_PROVIDER"] = embed
    if embed == "openai" and keep_embed:
        typer.echo(
            msg("init.embed_kept", model=settings.openai_embed_model or msg("init.model_not_set"))
        )
    elif embed == "openai":
        embed_model = settings.openai_embed_model
        if not yes:
            embed_model = typer.prompt(msg("init.prompt_embed_model"), default=embed_model or None)
        embed_model = (embed_model or "").strip()
        if not embed_model or not CHAT_MODEL_RE.fullmatch(embed_model):
            _fail(msg("init.embed_model_needed"), code=2)
        keys["AUTOONTOLOGY_OPENAI_EMBED_MODEL"] = embed_model
        typer.echo(
            msg(
                "init.embed_openai",
                model=embed_model,
                base=keys.get("AUTOONTOLOGY_OPENAI_BASE_URL"),
            )
        )
    else:
        state = msg(
            "init.embed_present"
            if det.ollama_models
            else "init.embed_pulled_by_setup"
            if det.ollama_reachable
            else "init.embed_needs_ollama"
        )
        typer.echo(msg("init.embed_ollama", model=settings.ollama_embed_model, state=state))

    # 4. documents
    _step(4, INIT_STEPS, msg("init.step.documents"))
    if not det.node_ok:
        if docs:
            typer.echo(msg("init.docs_need_node"))
        typer.echo(msg("init.docs_skip", detail=det.node_detail or msg("init.node_not_found")))
        want_docs = False
    elif docs is not None:
        want_docs = docs
    elif yes:
        want_docs = True
    else:
        want_docs = typer.confirm(msg("init.prompt_docs"), default=True)
    if det.node_ok:
        typer.echo(msg("init.docs_yes" if want_docs else "init.docs_no"))
    if vision_model:
        keys["AUTOONTOLOGY_VISION_MODEL"] = vision_model

    # 5. .env
    _step(5, INIT_STEPS, msg("init.step.env", path=env_path))
    env_file = onboarding.EnvFile.load(env_path, template=REPO_ROOT / ".env.example")
    created = not env_file.existed
    for key, value in keys.items():
        env_file.set(key, value)
    changed = [c for c in env_file.changes() if c[0] in keys]
    # before the write: afterwards a value loaded from the old .env looks like a shell one
    overrides = _shell_override_warnings(keys.items())
    backup = env_file.write(env_path)
    if created:
        typer.echo(msg("init.env_created", path=env_path))
    for key, old, new in changed:
        typer.echo(msg("init.env_set", change=onboarding.describe_change(key, old, new)))
    if not changed:
        typer.echo(msg("init.env_unchanged"))
    if backup:
        typer.echo(msg("init.env_backup", path=backup))
    for warning in overrides:
        typer.echo(msg("init.warn", warning=warning))
    unload_env()

    # 6. setup
    _step(6, INIT_STEPS, msg("init.step.setup_docs" if want_docs else "init.step.setup"))
    try:
        _init_setup(want_docs)
    except typer.Exit as exc:
        if exc.exit_code:
            typer.echo(msg("init.setup_stopped"), err=True)
        raise

    # 7. summary
    _step(7, INIT_STEPS, msg("init.step.summary"))
    settings = _settings()
    _store(settings)  # first builds in this workspace require explicit publication
    summary = [
        (
            "postgres",
            f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}",
        ),
        ("chat", f"{settings.chat_provider} {settings.effective_chat_model}"),
        ("embeddings", f"{settings.embed_provider} {settings.effective_embed_model}"),
        (
            "documents",
            msg("init.sum.documents_kordoc" if want_docs else "init.sum.documents_native"),
        ),
        ("vision", settings.vision_model or msg("init.sum.vision_off")),
        (".env", str(env_path)),
    ]
    header = (msg("init.col.item"), msg("init.col.value"))
    typer.echo("\n".join("  " + line for line in _table(header, summary).splitlines()))
    nxt = "uv run autoontology demo" + (" --set hanbit" if choice != "ollama" else "")
    typer.echo(msg("init.next", command=nxt))


def _demo_prune_orphans(settings, store) -> None:
    """Right after ``up``: delete upstream connections whose per-source database is gone
    (one of them makes every ingest time out), with a notice. Connections whose database
    exists but that no source in this checkout owns are only reported: another checkout
    sharing the catalog (or a reset ``data/``) may still need them."""
    import psycopg

    from autoontology.engine.client import BackendClient, BackendError
    from autoontology.structured import remove

    if not remove.backend_is_ours(settings):
        return
    try:
        with BackendClient(settings.backend_url, timeout=30.0) as client:
            orphans = remove.find_orphan_connections(settings, store, client)
            dead = [o for o in orphans if o.database_missing]
            live = [o for o in orphans if not o.database_missing]
            if live:
                typer.echo(msg("demo.live_connections", names=", ".join(o.name for o in live)))
            if not dead:
                return
            typer.echo(msg("demo.pruning", names=", ".join(o.name for o in dead)))
            remove.prune_orphan_connections(
                settings,
                client,
                dead,
                say=lambda message, warning=False: typer.echo(
                    f"  {'warn' if warning else 'ok'}    {message}"
                ),
            )
    except BackendError as exc:
        typer.echo(msg("demo.prune_backend", error=exc, command=remove.PRUNE_COMMAND))
    except psycopg.Error as exc:
        first = (str(exc).splitlines() or [type(exc).__name__])[0][:160]
        typer.echo(msg("demo.prune_postgres", error=first))
    except Exception as exc:  # noqa: BLE001 - a check must never crash demo
        first = (str(exc).splitlines() or [""])[0][:160]
        typer.echo(msg("demo.prune_other", error=f"{type(exc).__name__}: {first}"))


@tools_app.command("prune-connections")
def tools_prune_connections(
    names: list[str] = typer.Argument(
        None, help="Only these connections (default: every orphan found)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only list the orphaned connections."),
    include_unowned: bool = typer.Option(
        False,
        "--include-unowned",
        help="Also delete connections whose database exists but that no source in this "
        "checkout owns (another checkout sharing the catalog may still use them).",
    ),
    drop_db: bool = typer.Option(
        False,
        "--drop-db",
        help="With --include-unowned: also drop those connections' autoontology_src_* databases.",
    ),
) -> None:
    """Delete upstream connections whose autoontology_src_* database is gone (one of them
    makes every ingest time out). Live connections no source owns need --include-unowned."""
    import psycopg

    from autoontology.engine.client import BackendClient, BackendError, BackendUnavailable
    from autoontology.structured import remove

    if drop_db and not include_unowned:
        _fail("--drop-db only applies with --include-unowned", code=2)
    settings = _settings()
    store = _store(settings)
    try:
        with BackendClient(settings.backend_url) as client:
            orphans = remove.find_orphan_connections(settings, store, client)
            if names:
                orphans = [o for o in orphans if o.name in names]
            if not orphans:
                typer.echo("no orphaned upstream connections")
                return
            chosen = []
            for orphan in orphans:
                if orphan.database_missing or include_unowned:
                    typer.echo(f"orphan: {orphan.name} ({orphan.reason})")
                    chosen.append(orphan)
                else:
                    typer.echo(
                        f"unowned: {orphan.name} (database exists; kept, another checkout may "
                        "use it: pass --include-unowned to delete it)"
                    )
            if dry_run or not chosen:
                typer.echo("nothing deleted" + (" (dry run)" if dry_run else ""))
                return
            remove.prune_orphan_connections(
                settings,
                client,
                chosen,
                drop_db=drop_db,
                say=lambda message, warning=False: typer.echo(message, err=warning),
            )
    except psycopg.Error as exc:
        first = (str(exc).splitlines() or [type(exc).__name__])[0][:160]
        _fail(
            f"prune-connections: Postgres is not reachable ({first}); start it "
            "(`docker compose up -d`) and try again"
        )
    except BackendUnavailable as exc:
        _fail(f"prune-connections: {exc}; start the services with `uv run autoontology up`")
    except BackendError as exc:
        _fail(f"prune-connections: {exc}")


def _print_step_times(store, since: str) -> None:
    """Elapsed time per top-level step of the build run started at/after *since*."""
    from autoontology.engine.build import is_top_step

    run = store.latest_workspace_run("build")
    if run is None or (run.started_at or "") < since:
        return
    rows = [
        (s.name, s.status, f"{s.detail['seconds']}s" if "seconds" in s.detail else "-")
        for s in store.get_steps(run.id)
        if is_top_step(s.name)
    ]
    if rows:
        header = tuple(msg(f"demo.col.{c}") for c in ("step", "status", "time"))
        typer.echo("\n".join("  " + line for line in _table(header, rows).splitlines()))


@app.command(help=msg("help.demo"))
def demo(
    set_name: str = typer.Option("quickstart", "--set", help=msg("help.demo.set")),
    no_open: bool = typer.Option(False, "--no-open", help=msg("help.demo.no_open")),
    resume: bool = typer.Option(False, "--resume", help=msg("help.demo.resume")),
    replace: bool = typer.Option(False, "--replace", help=msg("help.demo.replace")),
) -> None:
    """Run the whole pipeline on a sample set: start the services, register the sample
    sources, build, export and open the web UI."""
    import time

    import click

    from autoontology import onboarding
    from autoontology.engine.jobs import JobError
    from autoontology.store.db import now_iso
    from autoontology.structured.loaders import LoadError
    from autoontology.structured.remove import RemoveError

    settings = _settings()
    try:
        samples = onboarding.sample_sources(settings, set_name)
    except onboarding.SampleSetError as exc:
        _fail(msg("demo.bad_set", error=exc), code=2)
    started = time.monotonic()
    total = 5

    _step(1, total, msg("demo.step.services"))
    up()
    store = _store(settings)
    _demo_prune_orphans(settings, store)

    _step(2, total, msg("demo.step.samples", set=set_name))
    for conflict in onboarding.sample_conflicts(settings, store, set_name):
        typer.echo(msg("demo.conflict", **conflict))
        if replace:
            continue
        try:
            ok = typer.confirm(msg("demo.confirm_replace", name=conflict["name"]), default=False)
        except (typer.Abort, click.exceptions.Abort):  # no stdin: a script
            ok = False
        if not ok:
            _fail(msg("demo.kept_stop", name=conflict["name"]), code=2)
    try:
        onboarding.register_sample_set(
            settings, store, set_name, notify=lambda m: typer.echo(f"  {m}"), replace=True
        )
    except (onboarding.SampleSetError, RemoveError, LoadError, JobError, ValueError) as exc:
        _fail(msg("demo.register_failed", error=exc))

    _step(3, total, msg("demo.step.build"))
    rows = onboarding.duration_rows(set_name, settings.chat_provider)
    typer.echo(msg("demo.expected", set=set_name))
    header = tuple(msg(f"demo.col.{c}") for c in ("provider", "time", "note"))
    typer.echo("\n".join("    " + line for line in _table(header, rows).splitlines()))
    typer.echo(
        msg(
            "demo.chat_provider",
            provider=settings.chat_provider,
            model=settings.effective_chat_model,
        )
    )
    build_since = now_iso()
    build_started = time.monotonic()
    try:
        build(source=[s.name for s in samples], resume=resume)
    except typer.Exit as exc:
        _print_step_times(store, build_since)
        if exc.exit_code:
            typer.echo(msg("demo.resume"), err=True)
        raise
    _print_step_times(store, build_since)
    typer.echo(msg("demo.build_total", seconds=time.monotonic() - build_started))

    _step(4, total, msg("demo.step.export"))
    from autoontology.store.operations import read_snapshot

    if read_snapshot(store) is None:
        typer.echo(msg("demo.review_required"))
    else:
        export(revision=None, out=None, format="all", preview=False)

    _step(5, total, msg("demo.step.ui"))
    ui(no_browser=no_open)
    typer.echo(msg("demo.finished", seconds=time.monotonic() - started))
    typer.echo(msg("demo.look_next"))


@provider_app.command("status", help=msg("help.provider.status"))
def provider_status() -> None:
    """Every chat provider: available, logged in, model, last test, and what to do."""
    from autoontology import onboarding
    from autoontology.config import CHAT_PROVIDERS
    from autoontology.web.providers import probe

    settings = _settings()
    rows = []
    for name in CHAT_PROVIDERS:
        info = onboarding.provider_info(settings, name)
        last = probe.last_test(settings, name)
        last_text = (
            f"{msg('provider.last_ok') if last.get('ok') else msg('provider.last_failed')} "
            f"{msg('provider.seconds', seconds=last.get('seconds', '?'))} "
            f"({str(last.get('at', ''))[:16]})"
            if last
            else "-"
        )
        rows.append(
            (
                ("* " if name == settings.chat_provider else "  ") + name,
                _yn(info.found),
                _yn(info.logged_in),
                _model_for(settings, name) or "-",
                last_text,
                localized_remedy(info.remedy) if info.remedy else "-",
            )
        )
    columns = ("provider", "available", "logged_in", "model", "last_test", "remedy")
    typer.echo(_table(tuple(msg(f"provider.col.{c}") for c in columns), rows))
    origin = settings.selection_origin.get("chat_provider", "?")
    typer.echo(msg("provider.active", provider=settings.chat_provider, origin=origin))


@provider_app.command("test", help=msg("help.provider.test"))
def provider_test(
    provider: str = typer.Option(None, "--provider", help=msg("help.provider.test.provider")),
) -> None:
    """One structured test call ("pong") through the chat router."""
    from autoontology.web.providers import probe

    settings = _settings()
    try:
        result = probe.run_test(settings, provider)
    except ValueError as exc:
        _fail(str(exc), code=2)
    name = provider or settings.chat_provider
    if result.get("ok"):
        typer.echo(
            msg(
                "provider.test_ok",
                name=name,
                model=result.get("model"),
                answer=result.get("answer"),
                seconds=result.get("seconds"),
                via=result.get("via"),
            )
        )
        return
    remedy = result.get("remedy") or msg("provider.see_status")
    error = result.get("error") or result.get("answer") or msg("provider.no_answer")
    _fail(
        msg(
            "provider.test_failed",
            name=name,
            error=error,
            seconds=result.get("seconds"),
            remedy=remedy,
        )
    )


@provider_app.command("use", help=msg("help.provider.use"))
def provider_use(
    name: str = typer.Argument(..., help="ollama | claude | codex | openai"),
    chat_model: str = typer.Option(None, "--chat-model", help=msg("help.provider.use.chat_model")),
    embed: str = typer.Option(None, "--embed", help=msg("help.provider.use.embed")),
    embed_model: str = typer.Option(
        None, "--embed-model", help=msg("help.provider.use.embed_model")
    ),
    base_url: str = typer.Option(None, "--base-url", help=msg("help.provider.use.base_url")),
    openai_chat_model: str = typer.Option(
        None, "--openai-chat-model", help=msg("help.provider.use.openai_chat_model")
    ),
) -> None:
    """Switch the chat provider in .env (comments and other lines are kept)."""
    import re

    from autoontology import onboarding
    from autoontology.config import (
        CHAT_MODEL_RE,
        CHAT_PROVIDERS,
        CLI_PROVIDERS,
        EMBED_PROVIDERS,
        env_file_path,
    )

    if name not in CHAT_PROVIDERS:
        _fail(msg("provider.unknown", name=name, choices=", ".join(CHAT_PROVIDERS)), code=2)
    if embed is not None and embed not in EMBED_PROVIDERS:
        _fail(msg("provider.unknown_embed", name=embed, choices=", ".join(EMBED_PROVIDERS)), code=2)
    for flag, value in (
        ("--chat-model", chat_model),
        ("--embed-model", embed_model),
        ("--openai-chat-model", openai_chat_model),
    ):
        if value is not None and not CHAT_MODEL_RE.fullmatch(value):
            _fail(msg("init.bad_model", flag=flag, value=value), code=2)
    if base_url is not None and not re.fullmatch(r"https?://[^\s\"'<>]{1,290}", base_url):
        _fail(msg("provider.bad_url", flag="--base-url", value=base_url), code=2)
    settings = _settings()
    path = env_file_path()
    env_file = onboarding.EnvFile.load(path)
    keys = {"AUTOONTOLOGY_CHAT_PROVIDER": name}
    if chat_model:
        keys[onboarding.CHAT_MODEL_VARS[name]] = chat_model
    if openai_chat_model:
        keys["AUTOONTOLOGY_OPENAI_CHAT_MODEL"] = openai_chat_model
    if embed:
        keys["AUTOONTOLOGY_EMBED_PROVIDER"] = embed
    if embed_model:
        embed_name = embed or settings.embed_provider
        var = "AUTOONTOLOGY_OPENAI_EMBED_MODEL" if embed_name == "openai" else None
        keys[var or "AUTOONTOLOGY_OLLAMA_EMBED_MODEL"] = embed_model
    if base_url:
        keys["AUTOONTOLOGY_OPENAI_BASE_URL"] = base_url.rstrip("/")
    for key, value in keys.items():
        env_file.set(key, value)
    changed = env_file.changes()
    overrides = _shell_override_warnings(keys.items())  # before the write, as in init
    backup = env_file.write(path)
    for key, old, new in changed:
        typer.echo(msg("provider.set", change=onboarding.describe_change(key, old, new), path=path))
    if not changed:
        typer.echo(msg("provider.unchanged", path=path, name=name))
    if backup:
        typer.echo(msg("provider.backup", path=backup))
    for warning in overrides:
        typer.echo(msg("provider.warning", warning=warning), err=True)
    uses_openai = "openai" in (name, embed or settings.embed_provider)
    if name == "openai" and not (
        env_file.get("AUTOONTOLOGY_OPENAI_BASE_URL") or settings.openai_base_url
    ):
        typer.echo(msg("provider.openai_warning", path=path), err=True)
    elif name in CLI_PROVIDERS or name == "ollama":
        info = onboarding.provider_info(settings, name)
        if not info.ready:
            typer.echo(
                msg("provider.not_ready", name=name, note=info.note, remedy=info.remedy), err=True
            )
    if uses_openai and not (env_file.get("AUTOONTOLOGY_OPENAI_API_KEY") or settings.openai_api_key):
        typer.echo(
            msg("provider.key_hint", url=f"{settings.service_url}/ui/settings", path=path),
            err=True,
        )
    typer.echo(msg("provider.restart", command=onboarding.RESTART_HINT))
    typer.echo(msg("provider.restart_short"))


@app.command(help=msg("help.restart"))
def restart(
    only: str = typer.Option(None, "--only", help=msg("help.restart.only")),
) -> None:
    """Restart the service and the backend with the current .env (the same detached helper
    the Settings page starts, here in the foreground)."""
    import subprocess

    from autoontology.config import env_file_path
    from autoontology.engine import restart as helper

    if only is not None and only not in helper.ONLY_CHOICES:
        _fail(msg("restart.bad_only", choices=", ".join(helper.ONLY_CHOICES), value=only), code=2)
    settings = _settings()
    argv = helper.helper_argv(settings.home, env_file_path(), restart_id=helper.new_id(), only=only)
    code = subprocess.call(argv, env=helper.helper_env())
    if code:
        log = settings.logs_dir  # the service and backend logs
        _fail(msg("restart.failed", code=code, log=log), code=code)


from autoontology.security.cli import install as install_authority  # noqa: E402

install_authority(app, _settings)
