"""AutoOntology command-line interface."""

from __future__ import annotations

import typer

from autoontology import __version__

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


@app.command()
def doctor(
    skip_models: bool = typer.Option(False, "--skip-models", help="Skip chat/embedding calls."),
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
        _fail(f"postgres: {exc}\nstart Postgres first, e.g. `docker compose up -d`")
    try:
        run_migrations(settings)
    except MigrationError as exc:
        _fail(f"migrations: {exc}")
    typer.echo(f"migrations: database at {current_revision(settings)} (head {head_revision()})")
    if skip_models:
        typer.echo("models: skipped (--skip-models)")
    elif settings.chat_provider == "openai" and settings.embed_provider == "openai":
        typer.echo(
            "models: skipped (chat and embeddings use the openai provider; "
            "`uv run autoontology doctor` checks the endpoint)"
        )
    else:
        from autoontology.config import OLLAMA_DEFAULT_CHAT_MODEL
        from autoontology.engine.ollama import (
            OllamaSetupError,
            TemplatePatchError,
            pull_models,
            setup_ollama,
        )

        if (
            settings.chat_provider == "openai"
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
                f"models: {', '.join(wanted)} present"
                + (f" (pulled {', '.join(pulled)})" if pulled else "")
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
                f"models: {result.alias} answers {result.answer!r} with "
                f"completion_tokens {result.completion_tokens} (thinking off); "
                f"embeddings {settings.ollama_embed_model}"
            )
    if docs:
        _install_kordoc(settings, version=None, omit_optional=False, force=False)
    else:
        typer.echo("kordoc: skipped (pass --docs for HWP/PDF/DOCX/PPTX documents)")
    typer.echo("next: uv run autoontology up")


def _install_kordoc(settings, *, version: str | None, omit_optional: bool, force: bool) -> None:
    from autoontology.unstructured import kordoc

    try:
        result = kordoc.install_kordoc(
            settings.home,
            version=version or kordoc.DEFAULT_VERSION,
            omit_optional=omit_optional,
            force=force,
            echo=typer.echo,
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


@tools_app.command("install-kordoc")
def tools_install_kordoc(
    version: str = typer.Option(None, "--version", help="kordoc version (default: pinned)."),
    omit_optional: bool = typer.Option(
        False, "--omit-optional", help="Skip optional deps (PDF/OCR then unavailable)."
    ),
    force: bool = typer.Option(False, "--force", help="Reinstall even if present."),
) -> None:
    """Install the kordoc document engine into <home>/kordoc (npm, Node >= 20)."""
    _install_kordoc(_settings(), version=version, omit_optional=omit_optional, force=force)


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

    return Store.for_home(settings.home)


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    return "\n".join(
        "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
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
            _fail(f"source add failed: no such file or directory: {path}")
        if store.get_source(name):
            _fail(
                f"source add failed: source {name!r} already exists "
                f"(remove it with `source rm {name}`)"
            )
        try:
            src = ensure_documents_source(store, name, path, recursive)
        except (JobError, ValueError) as exc:
            _fail(f"source add failed: {exc}")
        typer.echo(f"added source {src.name} (documents)")
        typer.echo(f"  locator: {pipeline.display_locator(src)}")
    elif kind == "structured":
        try:
            src = pipeline.add_source(store, target, name=name, recursive=recursive)
        except (LoadError, ValueError) as exc:
            _fail(f"source add failed: {exc}")
        typer.echo(f"added source {src.name} ({src.kind}) -> {pipeline.catalog_database_name(src)}")
        typer.echo(f"  locator: {pipeline.display_locator(src)}")
        if src.kind == "files":
            files = pipeline.files_locator(src).files()
            typer.echo(f"  files ({len(files)}): " + ", ".join(f.name for f in files))
    else:
        _fail(f"unknown --kind {kind!r} (structured | documents)", code=2)
    typer.echo("next: uv run autoontology build")


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
        _fail(f"unknown source {name!r}")
    try:
        remove_source(
            settings,
            store,
            src,
            drop_db=drop_db,
            force=force,
            notify=lambda message, warning: typer.echo(message, err=warning),
        )
    except RemoveError as exc:
        _fail(str(exc))


def _report_extract(store, name: str, result) -> bool:
    """Print the extract summary of documents source *name*; False when it was partial."""
    from autoontology.engine import extract as extract_job

    rows = extract_job.summary_rows(store, name)
    if rows:
        typer.echo(_table(("DOCUMENT", "ENGINE", "PAGES", "CHUNKS", "EXTRACTED", "CONCEPTS"), rows))
    report = result.details.get("report", {})
    if report:
        typer.echo(
            f"concepts {report.get('concepts', 0)}, relations {report.get('relations', 0)}"
            f", evidence {report.get('evidence', 0)} ({report.get('evidence_verified', 0)}"
            f" verified), chunks failed {report.get('chunks_failed', 0)}, pending "
            f"{report.get('chunks_pending', 0)}"
        )
    report_path = result.export_dir / "report.md" if result.export_dir else None
    if result.status == "succeeded":
        typer.echo(f"extract {name} {result.run_id}: {result.status}")
        if report_path:
            typer.echo(f"report: {report_path}")
        return True
    from autoontology.unstructured.report import display_path as shown_path

    steps = {s.name: s.detail for s in store.get_steps(result.run_id)}
    failed = steps.get("extract", {}).get("failed_chunks", [])
    unread = steps.get("read", {}).get("failed", [])
    typer.echo(
        f"extract {name} {result.run_id}: partial ({len(failed)} chunk(s) failed, "
        f"{len(unread)} document(s) failed to read)"
    )
    for item in failed[:10]:
        typer.echo(f"  failed: {item['document']} #{item['index']}: {item['error'][:160]}")
    for item in unread[:10]:
        typer.echo(f"  unread: {shown_path(item['path'])} ({item['code']}): {item['error'][:160]}")
    if steps.get("consolidate", {}).get("partial"):
        typer.echo(
            "  consolidation incomplete (embedding/merge judge failures; see "
            "jobs.log): the next run consolidates again"
        )
    if report_path:
        typer.echo(f"report: {report_path}")
    return False


@app.command()
def build(
    source: str = typer.Option(None, "--source", help="Only this source (default: all)."),
    resume: bool = typer.Option(False, "--resume", help="Skip steps that already succeeded."),
) -> None:
    """Build every registered source: structured (load -> ingest -> compile), then documents
    (read -> chunk -> extract -> consolidate), then align documents to the structured Terms."""
    import time

    from autoontology.engine import align as align_job
    from autoontology.engine import extract as extract_job
    from autoontology.engine import jobs
    from autoontology.engine.client import BackendError

    settings = _settings()
    store = _store(settings)
    sources = store.list_sources()
    if source:
        sources = [s for s in sources if s.name == source]
        if not sources:
            _fail(f"unknown source {source!r} (see `autoontology source list`)")
    if not sources:
        _fail("no sources: add one with `autoontology source add <path> --name <name>`")
    structured = [s for s in sources if s.kind in ("files", "database")]
    documents = [s for s in sources if s.kind == "documents"]
    started = time.monotonic()
    log_path = jobs.jobs_log_path(settings)
    failed: list[str] = []
    jobs.setup_job_logging(settings, console=True)
    for src in structured:
        try:
            result = jobs.run_build(src.name, resume=resume, settings=settings, store=store)
        except (jobs.JobError, BackendError) as exc:
            hint = f" (log: {exc.log_path})" if getattr(exc, "log_path", None) else ""
            typer.echo(f"build {src.name} failed: {exc}{hint}", err=True)
            failed.append(src.name)
            continue
        except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
            _unexpected(f"build {src.name}", exc, log_path)
        typer.echo(f"build {src.name} {result.run_id}: {result.status}")
    for src in documents:
        target, recursive = extract_job.documents_target(src)
        try:
            result = extract_job.run_extract(
                src.name, target, settings=settings, store=store, recursive=recursive, resume=resume
            )
        except jobs.JobError as exc:
            hint = f" (log: {exc.log_path})" if exc.log_path else ""
            typer.echo(f"extract {src.name} failed: {exc}{hint}", err=True)
            failed.append(src.name)
            continue
        except Exception as exc:  # noqa: BLE001
            _unexpected(f"extract {src.name}", exc, log_path)
        if not _report_extract(store, src.name, result):
            failed.append(src.name)
    built = [
        s
        for s in (store.get_source(x.name) for x in store.list_sources())
        if s is not None and s.kind in ("files", "database") and s.catalog_database_id
    ]
    if documents and not built:
        typer.echo("align: skipped (no built structured source to align to)")
    for doc in documents:
        if doc.name in failed:
            continue
        for target_src in built:
            try:
                result = align_job.run_align(
                    doc.name,
                    settings=settings,
                    store=store,
                    structured_source=target_src.name,
                    resume=resume,
                )
            except jobs.JobError as exc:
                typer.echo(f"align {doc.name} -> {target_src.name} failed: {exc}", err=True)
                failed.append(f"{doc.name}->{target_src.name}")
                continue
            except Exception as exc:  # noqa: BLE001
                _unexpected(f"align {doc.name}", exc, log_path)
            typer.echo(f"align {doc.name} -> {target_src.name} {result.run_id}: {result.status}")
    elapsed = time.monotonic() - started
    if failed:
        _fail(
            f"build finished with failures in {elapsed:.1f}s: {', '.join(failed)}\n"
            f"log: {log_path}\nretry: uv run autoontology build --resume"
        )
    typer.echo(f"build: succeeded in {elapsed:.1f}s; next: uv run autoontology export")


@app.command()
def runs(
    source: str = typer.Option(None, "--source", help="Only runs of this source."),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Show recent runs and their steps."""
    settings = _settings()
    store = _store(settings)
    source_id = None
    if source:
        src = store.get_source(source)
        if src is None:
            _fail(f"unknown source {source!r}")
        source_id = src.id
    names = {s.id: s.name for s in store.list_sources()}
    rows = []
    for run in store.list_runs(source_id, limit=limit):
        all_steps = store.get_steps(run.id)
        steps = " ".join(
            f"{s.name}={s.status}" + (f"({s.detail['seconds']}s)" if "seconds" in s.detail else "")
            for s in all_steps
            if not s.name.startswith(("chunk:", "concept:"))
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
def export(
    out: str = typer.Option(
        None, "--out", help="Output directory (default: exports/<name>/<run>/)."
    ),
) -> None:
    """Write the merged ontology of all sources: ontology.ttl, ontology.jsonld,
    graph.graphml, report.md."""
    import time
    from pathlib import Path

    from autoontology.engine import export_job, jobs

    settings = _settings()
    started = time.monotonic()
    log_path = jobs.jobs_log_path(settings)
    store = _store(settings)
    names = [s.name for s in store.list_sources()]
    if not names:
        _fail("nothing to export: no sources (see `autoontology source add`)")
    try:
        jobs.setup_job_logging(settings, console=True)
        result = export_job.run_export(
            export_job.MERGED_EXPORT_NAME,
            settings=settings,
            store=store,
            sources=names,
            out=Path(out) if out else None,
        )
    except jobs.JobError as exc:
        hint = f"\nlog: {exc.log_path}" if exc.log_path else ""
        _fail(f"export failed: {exc}{hint}")
    except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
        _unexpected("export", exc, log_path)
    counts = result.details.get("collect", {}).get("counts", {})
    if counts:
        typer.echo(
            _table(
                (
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
                    tuple(
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
                    )
                ],
            )
        )
    for entry in result.details.get("report", {}).get("files", []):
        typer.echo(f"  {entry['name']}  {entry['bytes']} bytes")
    typer.echo(f"export {result.run_id}: {result.status} in {time.monotonic() - started:.1f}s")
    if result.export_dir:
        typer.echo(f"dir: {result.export_dir}")
