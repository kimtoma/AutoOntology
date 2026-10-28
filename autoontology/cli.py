# SPDX-License-Identifier: MIT
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
def ontology() -> None:
    """Show the latest ontology snapshot: revision, sources and counts."""
    settings = _settings()
    snap = _store(settings).latest_snapshot(with_model=False)
    if snap is None:
        _fail("no ontology snapshot yet: run `uv run autoontology build`")
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
    revision: int = typer.Option(None, "--revision", help="Snapshot revision (default: latest)."),
    out: str = typer.Option(
        None, "--out", help="Parent directory: writes <DIR>/r<rev>/ (default: exports/r<rev>/)."
    ),
    format: str = typer.Option(
        "all",
        "--format",
        help="all | rdf (ttl, jsonld, graphml, upstream/) | ossie (ossie/, upstream/) | okf. "
        "The other formats' files of the previous export of the revision are kept.",
    ),
) -> None:
    """Write an ontology snapshot (default: the latest) as ontology.ttl, ontology.jsonld,
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
        result = export_job.run_export(
            store, settings, revision=revision, out=Path(out) if out else None, format=format
        )
    except (jobs.JobError, BackendError) as exc:
        hint = f"\nlog: {exc.log_path}" if getattr(exc, "log_path", None) else ""
        _fail(f"export failed: {exc}{hint}")
    except Exception as exc:  # noqa: BLE001 - one clean line; AUTOONTOLOGY_DEBUG=1 re-raises
        _unexpected("export", exc, log_path)
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
