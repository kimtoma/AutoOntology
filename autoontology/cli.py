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
        _fail(f"postgres: {exc}\nstart Postgres first, e.g. `docker compose up -d`")
    try:
        run_migrations(settings)
    except MigrationError as exc:
        _fail(f"migrations: {exc}")
    typer.echo(f"migrations: database at {current_revision(settings)} (head {head_revision()})")
    if skip_models:
        typer.echo("models: skipped (--skip-models)")
    elif settings.chat_provider != "ollama" and settings.embed_provider == "openai":
        roles = (
            "chat and embeddings use the openai provider"
            if settings.chat_provider == "openai"
            else f"chat uses the {settings.chat_provider} provider, embeddings the openai one"
        )
        typer.echo(f"models: skipped ({roles}; `uv run autoontology doctor` checks the endpoint)")
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
    if settings.vision_model and not skip_models:
        from autoontology.engine.ollama import OllamaSetupError, pull_models

        try:
            pulled = pull_models(settings.ollama_endpoint, [settings.vision_model], echo=typer.echo)
        except OllamaSetupError as exc:
            _fail(f"vision model: {exc}")
        typer.echo(
            f"vision model: {settings.vision_model} present" + (" (pulled)" if pulled else "")
        )
    elif settings.vision_model:
        typer.echo(f"vision model: {settings.vision_model} skipped (--skip-models)")
    if docs:
        _install_kordoc(
            settings,
            version=None,
            omit_optional=False,
            force=False,
            skip_ocr_models=skip_ocr_models,
        )
    elif in_init:
        typer.echo(
            "kordoc: skipped (answer yes to document support or run "
            "`uv run autoontology tools install-kordoc` later)"
        )
    else:
        typer.echo("kordoc: skipped (pass --docs for HWP/PDF/DOCX/PPTX documents)")
    if not in_init:
        typer.echo("next: uv run autoontology up")


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


# --- onboarding: init / demo / provider ---------------------------------------------------

provider_app = typer.Typer(help="Show, test and switch the chat provider.", no_args_is_help=True)
app.add_typer(provider_app, name="provider")

INIT_STEPS = 7
POSTGRES_RETRIES = 3
LOGIN_RETRIES = 3


def _step(n: int, total: int, title: str) -> None:
    typer.echo(f"\n[{n}/{total}] {title}")


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _model_for(settings, provider: str) -> str:
    import dataclasses

    return dataclasses.replace(settings, chat_provider=provider).effective_chat_model


def _shell_override_warnings(keys) -> list[str]:
    from autoontology.config import shell_value

    out = []
    for key, value in keys:
        shell = shell_value(key)
        if shell is not None and shell != value:
            out.append(
                f"your shell sets {key}={shell}, which wins over .env: "
                f"run `unset {key}` (and remove it from your shell profile)"
            )
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
        if docker:
            typer.echo("  fix:  start the bundled Postgres in this folder: `docker compose up -d`")
        else:
            typer.echo(
                "  fix:  install Docker (https://docs.docker.com/get-docker/) and run "
                "`docker compose up -d` in this folder"
            )
        typer.echo(
            f"        or set POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD in "
            f"{env_path} to your own Postgres 17 with pgvector"
        )
        tries += 1
        where = f"{settings.postgres_host}:{settings.postgres_port}"
        if (
            yes
            or tries > POSTGRES_RETRIES
            or not typer.confirm(
                f"  Postgres is not reachable at {where}. Start it (docker compose up -d) and "
                "press Enter to retry, or n to stop",
                default=True,
            )
        ):
            _fail(
                "init stopped: Postgres is not reachable. Apply the fix above, then run "
                "`uv run autoontology init` again.",
                code=2,
            )
        if not yes:
            from autoontology.config import unload_env

            unload_env()  # the user may have edited POSTGRES_* in .env meanwhile


def _init_login(settings, name: str, info, yes: bool):
    from autoontology import onboarding

    typer.echo(f"  {name}: {info.note or 'not ready'}")
    typer.echo(f"  fix:  {info.remedy or onboarding.LOGIN_COMMANDS[name]}")
    if yes:
        _fail(
            f"init stopped: the {name} CLI is not logged in. Run "
            f"`{onboarding.LOGIN_COMMANDS[name]}` in a terminal, then run init again "
            "(or pick another provider with --provider).",
            code=2,
        )
    for _ in range(LOGIN_RETRIES):
        if not typer.confirm(f"  Retry after `{onboarding.LOGIN_COMMANDS[name]}`?", default=True):
            break
        info = onboarding.provider_info(settings, name)
        if info.ready:
            typer.echo(f"  ok    {name}: {info.note}")
            return info
        typer.echo(f"  still not ready: {info.note}")
    _fail(
        f"init stopped: {name} is not logged in. Run `{onboarding.LOGIN_COMMANDS[name]}`, "
        "then `uv run autoontology init` again (or choose another provider).",
        code=2,
    )


def _init_setup(docs: bool) -> None:
    """Step 6: the ``setup`` command itself (same code path)."""
    setup(docs=docs, skip_models=False, force_alias=False, skip_ocr_models=False, in_init=True)


@app.command()
def init(
    provider: str = typer.Option(
        None,
        "--provider",
        help="Chat provider: ollama | claude | codex | openai (default: detected).",
    ),
    chat_model: str = typer.Option(None, "--chat-model", help="Chat model for that provider."),
    docs: bool = typer.Option(
        None,
        "--docs/--no-docs",
        help="Install the kordoc document engine and OCR models (default: yes with Node.js 20+).",
    ),
    vision_model: str = typer.Option(
        None, "--vision-model", help="Optional Ollama vision model that describes images."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Ask nothing: take the detected defaults (scripts, CI)."
    ),
    env: str = typer.Option(None, "--env", help="The .env file to write (default: <repo>/.env)."),
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

    if provider is not None and provider not in CHAT_PROVIDERS:
        _fail(f"--provider must be one of {', '.join(CHAT_PROVIDERS)}, got {provider!r}", code=2)
    for flag, value in (("--chat-model", chat_model), ("--vision-model", vision_model)):
        if value is not None and not CHAT_MODEL_RE.fullmatch(value):
            _fail(f"{flag} {value!r} is not a valid model name", code=2)
    if env:
        os.environ["AUTOONTOLOGY_ENV_FILE"] = str(Path(env).expanduser().resolve())
        unload_env()
    env_path = env_file_path()
    import shutil

    _step(1, INIT_STEPS, "Postgres")
    typer.echo(f"  ok    {_init_postgres(yes, shutil.which('docker') is not None, env_path)}")
    settings = _settings()
    det = onboarding.detect_environment(settings, env_path)

    # 2. chat provider
    _step(2, INIT_STEPS, "Chat provider")
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
            for line in _table(("PROVIDER", "FOUND", "LOGGED IN", "NOTE"), rows).splitlines()
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
        typer.echo(f"  note  current: {current}; recommended: {recommended}")
    if provider:
        choice = provider
    elif yes:
        choice = default_choice
        if current:
            typer.echo(
                f"  ok    keeping the current provider {current}"
                + (
                    f" (recommended: {recommended}; pass --provider {recommended} to switch)"
                    if current != recommended
                    else ""
                )
            )
    else:
        label = (
            f"  Chat provider (current: {current}; recommended: {recommended})"
            if current and current != recommended
            else "  Chat provider"
        )
        choice = typer.prompt(
            label, default=default_choice, type=click.Choice(list(CHAT_PROVIDERS))
        )
    info = det.providers[choice]
    if choice in CLI_PROVIDERS and not info.ready:
        info = _init_login(settings, choice, info, yes)
    if choice == "ollama" and not info.found:
        typer.echo(
            f"  warn  Ollama is not answering ({info.note}): install it from https://ollama.com "
            "and start it (`ollama serve`); setup pulls the models"
        )
    keys: dict[str, str] = {"AUTOONTOLOGY_CHAT_PROVIDER": choice}
    default_model = _model_for(settings, choice)
    if choice == "openai":
        base = settings.openai_base_url
        if not yes:
            base = typer.prompt("  Base URL (including /v1)", default=base or None)
        base = (base or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            _fail(
                "init stopped: the openai provider needs a base URL such as "
                "https://api.openai.com/v1 (set AUTOONTOLOGY_OPENAI_BASE_URL in .env, or run "
                "init without --yes)",
                code=2,
            )
        keys["AUTOONTOLOGY_OPENAI_BASE_URL"] = base
    model = chat_model
    if model is None:
        if yes:
            model = default_model
        else:
            hint = f" ({', '.join(CLI_KNOWN_MODELS[choice])})" if choice in CLI_KNOWN_MODELS else ""
            model = typer.prompt(f"  Chat model{hint}", default=default_model or None)
    model = (model or "").strip()
    if not model or not CHAT_MODEL_RE.fullmatch(model):
        _fail(
            f"init stopped: the {choice} provider needs a chat model name "
            "(pass --chat-model, or run init without --yes)",
            code=2,
        )
    keys[onboarding.CHAT_MODEL_VARS[choice]] = model
    typer.echo(f"  ok    chat: {choice} {model}")
    if choice == "openai":
        typer.echo(
            "  note  Add AUTOONTOLOGY_OPENAI_API_KEY=… to .env yourself "
            f"({env_path}; never paste keys into chats or shells with history)"
        )

    # 3. embeddings
    _step(3, INIT_STEPS, "Embeddings")
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
                "  Embeddings provider",
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
            f"  ok    embeddings: openai {settings.openai_embed_model or '(model not set)'} "
            "(current, kept)"
        )
    elif embed == "openai":
        embed_model = settings.openai_embed_model
        if not yes:
            embed_model = typer.prompt("  Embedding model", default=embed_model or None)
        embed_model = (embed_model or "").strip()
        if not embed_model or not CHAT_MODEL_RE.fullmatch(embed_model):
            _fail(
                "init stopped: openai embeddings need a model name "
                "(set AUTOONTOLOGY_OPENAI_EMBED_MODEL in .env, or run init without --yes)",
                code=2,
            )
        keys["AUTOONTOLOGY_OPENAI_EMBED_MODEL"] = embed_model
        typer.echo(
            f"  ok    embeddings: openai {embed_model} at {keys.get('AUTOONTOLOGY_OPENAI_BASE_URL')}"
        )
    else:
        state = (
            "present"
            if det.ollama_models
            else "pulled by setup"
            if det.ollama_reachable
            else "needs Ollama running (https://ollama.com, `ollama serve`)"
        )
        typer.echo(f"  ok    embeddings: ollama {settings.ollama_embed_model} ({state})")

    # 4. documents
    _step(4, INIT_STEPS, "Documents (kordoc: HWP/PDF/DOCX/PPTX/XLSX and image OCR)")
    if not det.node_ok:
        if docs:
            typer.echo("  warn  --docs needs Node.js 20 or newer")
        typer.echo(
            f"  skip  {det.node_detail or 'Node.js 20+ not found'}. Markdown, text, HTML, CSV and "
            ".doc documents still work. For the others install Node.js 20+ (https://nodejs.org), "
            "then run `uv run autoontology tools install-kordoc`"
        )
        want_docs = False
    elif docs is not None:
        want_docs = docs
    elif yes:
        want_docs = True
    else:
        want_docs = typer.confirm("  Install kordoc and its OCR models (~18 MB) now?", default=True)
    if det.node_ok:
        typer.echo(
            f"  ok    documents: {'kordoc + OCR models via setup' if want_docs else 'skipped'}"
            + ("" if want_docs else " (later: `uv run autoontology tools install-kordoc`)")
        )
    if vision_model:
        keys["AUTOONTOLOGY_VISION_MODEL"] = vision_model

    # 5. .env
    _step(5, INIT_STEPS, f"Write {env_path}")
    env_file = onboarding.EnvFile.load(env_path, template=REPO_ROOT / ".env.example")
    created = not env_file.existed
    for key, value in keys.items():
        env_file.set(key, value)
    changed = [c for c in env_file.changes() if c[0] in keys]
    backup = env_file.write(env_path)
    if created:
        typer.echo(f"  ok    created {env_path} from .env.example")
    for key, old, new in changed:
        typer.echo(f"  set   {onboarding.describe_change(key, old, new)}")
    if not changed:
        typer.echo("  ok    no changes (already set)")
    if backup:
        typer.echo(f"  ok    backup of the previous file: {backup}")
    for warning in _shell_override_warnings(keys.items()):
        typer.echo(f"  warn  {warning}")
    unload_env()

    # 6. setup
    _step(
        6, INIT_STEPS, "Setup (database, migrations, models" + (", kordoc)" if want_docs else ")")
    )
    try:
        _init_setup(want_docs)
    except typer.Exit as exc:
        if exc.exit_code:
            typer.echo(
                "init stopped during setup (see the line above): fix it, then run "
                "`uv run autoontology init` again (it is safe to repeat)",
                err=True,
            )
        raise

    # 7. summary
    _step(7, INIT_STEPS, "Summary")
    settings = _settings()
    summary = [
        (
            "postgres",
            f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}",
        ),
        ("chat", f"{settings.chat_provider} {settings.effective_chat_model}"),
        ("embeddings", f"{settings.embed_provider} {settings.effective_embed_model}"),
        ("documents", "kordoc + OCR" if want_docs else "native formats only"),
        ("vision", settings.vision_model or "off (images: OCR text only)"),
        (".env", str(env_path)),
    ]
    typer.echo("\n".join("  " + line for line in _table(("ITEM", "VALUE"), summary).splitlines()))
    nxt = "uv run autoontology demo" + (" --set hanbit" if choice != "ollama" else "")
    typer.echo(f"\nnext: {nxt}")


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
                typer.echo(
                    "  note  upstream connections with no source in this checkout (kept: "
                    "another checkout may use them): " + ", ".join(o.name for o in live)
                )
            if not dead:
                return
            typer.echo(
                "  note  pruning upstream connections whose database is gone (they would make "
                "every ingest time out): " + ", ".join(o.name for o in dead)
            )
            remove.prune_orphan_connections(
                settings,
                client,
                dead,
                say=lambda message, warning=False: typer.echo(
                    f"  {'warn' if warning else 'ok'}    {message}"
                ),
            )
    except BackendError as exc:
        typer.echo(
            f"  warn  could not check the upstream connections ({exc}); if the build times "
            f"out, run `{remove.PRUNE_COMMAND}`"
        )
    except psycopg.Error as exc:
        first = (str(exc).splitlines() or [type(exc).__name__])[0][:160]
        typer.echo(
            f"  warn  could not check the upstream connections: Postgres is not reachable "
            f"({first}); start it (`docker compose up -d`), then `uv run autoontology doctor`"
        )
    except Exception as exc:  # noqa: BLE001 - a check must never crash demo
        typer.echo(
            f"  warn  could not check the upstream connections ({type(exc).__name__}: "
            f"{(str(exc).splitlines() or [''])[0][:160]}); see `uv run autoontology doctor`"
        )


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
        typer.echo(
            "\n".join("  " + line for line in _table(("STEP", "STATUS", "TIME"), rows).splitlines())
        )


@app.command()
def demo(
    set_name: str = typer.Option(
        "quickstart", "--set", help="Sample set: quickstart (small) | hanbit (full company)."
    ),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the web UI."),
    resume: bool = typer.Option(
        False, "--resume", help="Continue the latest build, keeping its succeeded steps."
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Replace sources that use a sample name with other data (deletes their "
        "database and extracted concepts) without asking.",
    ),
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
        _fail(f"demo: {exc}", code=2)
    started = time.monotonic()
    total = 5

    _step(1, total, "Services")
    up()
    store = _store(settings)
    _demo_prune_orphans(settings, store)

    _step(2, total, f"Sample sources ({set_name})")
    for conflict in onboarding.sample_conflicts(settings, store, set_name):
        typer.echo(
            f"  {conflict['name']}: registered for {conflict['registered']} "
            f"({conflict['kind']}), the sample is {conflict['sample']}"
        )
        if replace:
            continue
        try:
            ok = typer.confirm(
                f"  Replace source {conflict['name']} (its database and extracted concepts "
                "will be deleted)?",
                default=False,
            )
        except (typer.Abort, click.exceptions.Abort):  # no stdin: a script
            ok = False
        if not ok:
            _fail(
                f"demo stopped: source {conflict['name']!r} was kept. Re-run with --replace "
                "to replace it, or remove it yourself (`uv run autoontology source list`)",
                code=2,
            )
    try:
        onboarding.register_sample_set(
            settings, store, set_name, notify=lambda m: typer.echo(f"  {m}"), replace=True
        )
    except (onboarding.SampleSetError, RemoveError, LoadError, JobError, ValueError) as exc:
        _fail(
            f"demo: could not register the sample sources: {exc}\n"
            "see `uv run autoontology source list`; remove a conflicting source with "
            "`uv run autoontology source rm <name>`"
        )

    _step(3, total, "Build")
    rows = onboarding.duration_rows(set_name, settings.chat_provider)
    typer.echo(f"  expected time for the first build of {set_name} (later builds reuse results):")
    typer.echo(
        "\n".join("    " + line for line in _table(("PROVIDER", "TIME", "NOTE"), rows).splitlines())
    )
    typer.echo(
        f"  chat provider: {settings.chat_provider} {settings.effective_chat_model} "
        "(switch with `uv run autoontology provider use <name>`)"
    )
    build_since = now_iso()
    build_started = time.monotonic()
    try:
        build(source=[s.name for s in samples], resume=resume)
    except typer.Exit as exc:
        _print_step_times(store, build_since)
        if exc.exit_code:
            typer.echo("demo stopped: continue with `uv run autoontology demo --resume`", err=True)
        raise
    _print_step_times(store, build_since)
    typer.echo(f"  build total: {time.monotonic() - build_started:.1f}s")

    _step(4, total, "Export")
    export(revision=None, out=None, format="all")

    _step(5, total, "Web UI")
    ui(no_browser=no_open)
    typer.echo(f"\ndemo finished in {time.monotonic() - started:.1f}s")
    typer.echo("What to look at next: Ontology → Graph → Playground")


@provider_app.command("status")
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
            f"{'ok' if last.get('ok') else 'failed'} {last.get('seconds', '?')}s "
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
                info.remedy or "-",
            )
        )
    typer.echo(_table(("PROVIDER", "AVAILABLE", "LOGGED IN", "MODEL", "LAST TEST", "REMEDY"), rows))
    origin = settings.selection_origin.get("chat_provider", "?")
    typer.echo(f"* = active chat provider ({settings.chat_provider}, from {origin})")


@provider_app.command("test")
def provider_test(
    provider: str = typer.Option(
        None, "--provider", help="ollama | openai | claude | codex (default: the active one)."
    ),
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
            f"{name} ok: {result.get('model')} answered {result.get('answer')!r} in "
            f"{result.get('seconds')}s (via {result.get('via')})"
        )
        return
    remedy = result.get("remedy") or "see `uv run autoontology provider status`"
    _fail(
        f"{name} failed: {result.get('error') or result.get('answer') or 'no answer'}"
        f" ({result.get('seconds')}s)\nfix: {remedy}"
    )


@provider_app.command("use")
def provider_use(
    name: str = typer.Argument(..., help="ollama | claude | codex | openai"),
    chat_model: str = typer.Option(None, "--chat-model", help="Chat model for that provider."),
) -> None:
    """Switch the chat provider in .env (comments and other lines are kept)."""
    from autoontology import onboarding
    from autoontology.config import CHAT_MODEL_RE, CHAT_PROVIDERS, CLI_PROVIDERS, env_file_path

    if name not in CHAT_PROVIDERS:
        _fail(f"unknown provider {name!r}: choose one of {', '.join(CHAT_PROVIDERS)}", code=2)
    if chat_model is not None and not CHAT_MODEL_RE.fullmatch(chat_model):
        _fail(f"--chat-model {chat_model!r} is not a valid model name", code=2)
    settings = _settings()
    path = env_file_path()
    env_file = onboarding.EnvFile.load(path)
    keys = {"AUTOONTOLOGY_CHAT_PROVIDER": name}
    if chat_model:
        keys[onboarding.CHAT_MODEL_VARS[name]] = chat_model
    for key, value in keys.items():
        env_file.set(key, value)
    changed = env_file.changes()
    backup = env_file.write(path)
    for key, old, new in changed:
        typer.echo(f"set {onboarding.describe_change(key, old, new)} in {path}")
    if not changed:
        typer.echo(f"{path} already selects {name}; nothing changed")
    if backup:
        typer.echo(f"backup: {backup}")
    for warning in _shell_override_warnings(keys.items()):
        typer.echo(f"warning: {warning}", err=True)
    if name == "openai" and not (
        env_file.get("AUTOONTOLOGY_OPENAI_BASE_URL") or settings.openai_base_url
    ):
        typer.echo(
            "warning: set AUTOONTOLOGY_OPENAI_BASE_URL, AUTOONTOLOGY_OPENAI_CHAT_MODEL and "
            f"AUTOONTOLOGY_OPENAI_API_KEY in {path} (type the key yourself)",
            err=True,
        )
    elif name in CLI_PROVIDERS or name == "ollama":
        info = onboarding.provider_info(settings, name)
        if not info.ready:
            typer.echo(f"warning: {name} is not ready ({info.note}): {info.remedy}", err=True)
    typer.echo(f"restart: {onboarding.RESTART_HINT}")
