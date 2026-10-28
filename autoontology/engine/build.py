# SPDX-License-Identifier: MIT
"""``autoontology build``: one run over every registered source -> one ontology snapshot.

Steps, in order (each persisted as a ``steps`` row of the single ``build`` run):

1. every structured source, in registration order: ``structured:<name>:load`` (files
   sources only), ``:register``, ``:ingest``, ``:compile``;
2. every documents source: ``documents:<name>:read``, ``:extract``, ``:consolidate``;
3. ``align:<docs-name>`` per documents source, against the union of the Terms of every
   built structured source (skipped when there is none, or the source has no concepts);
4. ``merge``: the unified model (:func:`autoontology.ontology.model.build`) over every
   source and the accepted alignments;
5. ``snapshot``: the model saved as the next revision of ``ontology_snapshots``.

``sources`` (``--source N``) restricts the per-source steps (the others are recorded as
skipped); align, merge and snapshot always cover everything. ``resume`` reuses the latest
build run (also a succeeded one): a succeeded step is kept unless its source's inputs
changed since it ran (:func:`input_fingerprint`: the files' contents, kordoc's
availability, or the database locator plus its live schema -- an unreachable database is
never reused) or something it depends on runs again (a step of the same source, or -- for
align/merge/snapshot -- any step), and align/merge/snapshot also run again when the set of
registered sources changed since that run (:func:`source_set`). Unchanged inputs are no-ops
inside the steps too (file manifests, document hashes, the consolidation fingerprint, the
judge cache), so a plain re-run does no LLM work for what did not change.

The run holds the store lease of the workspace and of every source for its whole life:
it refuses to start while another build or export (or a source operation) is active.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import Settings
from autoontology.engine import align as align_job
from autoontology.engine import extract as extract_job
from autoontology.engine import jobs
from autoontology.engine.client import BackendClient
from autoontology.engine.jobs import JobError, check_leases, hold_sources, short_error
from autoontology.store.db import LeaseLost, Run, Source, Store, now_iso
from autoontology.store.documents import DocumentStore
from autoontology.structured import pipeline

logger = logging.getLogger("autoontology.jobs")

BUILD_KIND = "build"
EXPORT_KIND = "export"
#: Lease name of the workspace itself (never a valid source name): build and export hold it.
WORKSPACE_LEASE = "*workspace*"
STRUCTURED_KINDS = ("files", "database")
STRUCTURED_STEPS: tuple[str, ...] = ("load", "register", "ingest", "compile")
DOCUMENTS_STEPS: tuple[str, ...] = ("read", "extract", "consolidate")
#: Sub-step rows (one per chunk / judged concept) that are not build steps.
SUB_STEP_PREFIXES: tuple[str, ...] = (extract_job.CHUNK_STEP_PREFIX, align_job.CONCEPT_STEP_PREFIX)
#: Run params that change the align verdicts: a resumed build with other values re-aligns.
ALIGN_PARAMS: tuple[str, ...] = ("min_sim", "min_conf", "top_k", "identity_policy")
GLOBAL = "*"
#: Step detail key: the :func:`input_fingerprint` of the source when the step ran.
INPUTS_KEY = "inputs_sha"


@dataclass
class BuildResult:
    run_id: str
    status: str
    steps: dict[str, str] = field(default_factory=dict)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    revision: int | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    #: what made a finished run partial (failed chunks/documents/concepts)
    problems: list[str] = field(default_factory=list)
    #: what a succeeded run left out on purpose (documents that need kordoc)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Step:
    name: str
    group: str  # the source name, or GLOBAL for align/merge/snapshot
    fn: Callable[[], dict[str, Any]]


def is_top_step(name: str) -> bool:
    """A build step (not a per-chunk/per-concept sub-step row)."""
    return not name.startswith(SUB_STEP_PREFIXES)


def step_names(sources: list[Source]) -> list[str]:
    """The step names of a build over *sources* (registration order), without running it."""
    structured = [s for s in sources if s.kind in STRUCTURED_KINDS]
    documents = [s for s in sources if s.kind == "documents"]
    names = [f"structured:{s.name}:{step}" for s in structured for step in STRUCTURED_STEPS]
    names += [f"documents:{s.name}:{step}" for s in documents for step in DOCUMENTS_STEPS]
    names += [f"align:{s.name}" for s in documents]
    return [*names, "merge", "snapshot"]


def kordoc_state(home: Path) -> str:
    """What a documents read can do with kordoc files: the engine resolved (CLI path,
    its mtime and Node), or ``none`` -- so installing kordoc changes the fingerprint."""
    from autoontology.unstructured import kordoc

    try:
        engine = kordoc.resolve_engine(home)
        stamp = Path(engine.cli).stat().st_mtime_ns
    except (kordoc.KordocError, OSError):
        return "none"
    return f"{engine.cli}:{stamp}:{engine.node}"


def input_fingerprint(source: Source, home: Path | None = None) -> str:
    """A hash of what the per-source steps of *source* read: the locator plus, for files
    and documents sources, every input file's path and content hash -- and, for documents
    sources holding kordoc formats, whether kordoc is available (*home*). "" when the
    inputs cannot be listed -- never equal to a recorded fingerprint, so the steps run
    again."""
    digest = hashlib.sha256(f"{source.kind}\0{source.locator}\0".encode())
    try:
        if source.kind == "files":
            files = pipeline.files_locator(source).files()
        elif source.kind == "documents":
            target, recursive = extract_job.documents_target(source)
            files = extract_job.readers.discover_documents(target, recursive=recursive)
            if home is not None and any(
                p.suffix.lower() in extract_job.readers.kordoc.EXTENSIONS for p in files
            ):
                digest.update(f"kordoc\0{kordoc_state(home)}\0".encode())
        else:
            files = []
        for path in sorted(p.resolve() for p in files):
            digest.update(f"{path}\0{extract_job.sha256_file(path)}\0".encode())
    except Exception:  # noqa: BLE001 - a missing/unreadable input: the step reports it
        return ""
    return digest.hexdigest()


def registration_order(store: Store) -> list[Source]:
    return sorted(store.list_sources(), key=lambda s: (s.created_at, s.name))


class _Build:
    """State of one build run: the run row, the backends, the model between merge and
    snapshot."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        run: Run,
        sources: list[Source],
        client: BackendClient,
        resume: bool,
        extract_backends: extract_job.Backends | None,
        align_backends: align_job.AlignBackends | None,
        writer: Any,
    ) -> None:
        self.settings = settings
        self.store = store
        self.run = run
        self.sources = sources
        self.client = client
        self.resume = resume
        self.extract_backends = extract_backends or extract_job.Backends(settings)
        self.align_backends = align_backends
        self.writer = writer
        self.model: Any = None
        self.reuse_concepts = True
        #: sources whose inputs changed since the resumed run (full document re-scan)
        self.changed: set[str] = set()
        self._fingerprints: dict[str, str] = {}

    def fingerprinted(
        self, source: Source, fn: Callable[[], dict[str, Any]]
    ) -> Callable[[], dict[str, Any]]:
        """*fn*, with the source's input fingerprint (taken before the first of its steps
        runs) recorded in the step detail for a later ``--resume``."""

        def run() -> dict[str, Any]:
            if source.name not in self._fingerprints:
                self._fingerprints[source.name] = input_fingerprint(
                    self.fresh(source), self.settings.home
                )
            detail = fn()
            detail[INPUTS_KEY] = self._fingerprints[source.name]
            return detail

        return run

    @property
    def structured(self) -> list[Source]:
        return [s for s in self.sources if s.kind in STRUCTURED_KINDS]

    @property
    def documents(self) -> list[Source]:
        return [s for s in self.sources if s.kind == "documents"]

    def fresh(self, source: Source) -> Source:
        return self.store.get_source_by_id(source.id) or source

    def built_structured(self) -> list[Source]:
        return [s for s in (self.fresh(x) for x in self.structured) if s.catalog_database_id]

    # --- per-source steps ------------------------------------------------------------

    def _structured_ctx(self, source: Source) -> jobs._Ctx:
        source = self.fresh(source)
        return jobs._Ctx(
            settings=self.settings,
            store=self.store,
            source=source,
            run=self.run,
            client=self.client,
            database_name=pipeline.catalog_database_name(source),
        )

    def structured_step(self, source: Source, step: str) -> Callable[[], dict[str, Any]]:
        fn = {
            "load": jobs._step_load,
            "register": jobs._step_register,
            "ingest": jobs._step_ingest,
            "compile": jobs._step_compile,
        }[step]
        return self.fingerprinted(source, lambda: fn(self._structured_ctx(source)))

    def _extract_ctx(self, source: Source) -> extract_job._ExtractCtx:
        from autoontology.unstructured.extractor import workers_default

        source = self.fresh(source)
        target, recursive = extract_job.documents_target(source)
        return extract_job._ExtractCtx(
            settings=self.settings,
            store=self.store,
            docs=DocumentStore(self.store),
            source=source,
            run=self.run,
            target=target,
            recursive=recursive,
            workers=workers_default(self.settings),
            max_chunks=None,
            backends=self.extract_backends,
            full_scan=not self.resume or source.name in self.changed,
        )

    def documents_step(self, source: Source, step: str) -> Callable[[], dict[str, Any]]:
        fn = {
            "read": extract_job._step_read,
            "extract": extract_job._step_extract,
            "consolidate": extract_job._step_consolidate,
        }[step]

        def run() -> dict[str, Any]:
            ctx = self._extract_ctx(source)
            if step == "read" and not ctx.target.exists():
                raise RuntimeError(f"no such file or directory: {ctx.target}")
            return fn(ctx)

        return self.fingerprinted(source, run)

    # --- global steps ----------------------------------------------------------------

    def align_step(self, source: Source) -> Callable[[], dict[str, Any]]:
        def run() -> dict[str, Any]:
            src = self.fresh(source)
            structured = self.built_structured()
            if not structured:
                return {"skipped": "no built structured source to align to"}
            if not DocumentStore(self.store).list_concepts(src.id):
                return {"skipped": f"documents source {src.name!r} has no concepts"}
            if not self.reuse_concepts:
                self._forget_concept_steps(src)
            return align_job.align_pass(
                settings=self.settings,
                store=self.store,
                run=self.run,
                source=src,
                structured=structured,
                client=self.client,
                backends=self.align_backends,
                writer=self.writer,
                min_sim=self.run.params.get("min_sim"),
                min_conf=self.run.params.get("min_conf"),
                top_k=int(self.run.params.get("top_k") or align_job.al.DEFAULT_TOP_K),
            )

        return run

    def _forget_concept_steps(self, source: Source) -> None:
        """Per-concept rows of an earlier pass with other align params are not reusable."""
        ids = {c.id for c in DocumentStore(self.store).list_concepts(source.id)}
        stale = [
            s.name
            for s in self.store.get_steps(self.run.id)
            if s.name.startswith(align_job.CONCEPT_STEP_PREFIX)
            and s.name.removeprefix(align_job.CONCEPT_STEP_PREFIX) in ids
        ]
        if stale:
            self.store.reset_steps(self.run.id, stale)

    def merge(self) -> dict[str, Any]:
        from autoontology.engine.export_job import documents_inputs
        from autoontology.ontology import model as merged
        from autoontology.structured.semantic import fetch_snapshot

        built = self.built_structured()
        built_names = {b.name for b in built}
        missing = [s.name for s in self.structured if s.name not in built_names]
        snapshots = [fetch_snapshot(self.client, s) for s in built]
        documents = [self.fresh(s) for s in self.documents]
        inputs = documents_inputs(self.store, documents)
        self.model = merged.build(
            self.settings.workspace,
            structured=snapshots,
            documents=inputs,
            base=self.settings.base_iri,
        )
        counts = self.model.counts()
        for name in missing:
            logger.warning("  structured source %s has no built catalog: left out", name)
        logger.info(
            "  %d class(es) (%d Term, %d document), %d object / %d datatype propert(ies), "
            "%d individual(s), %d document(s); %d concept(s) folded into Terms",
            counts["classes"],
            counts["classes_structured"],
            counts["classes_document"],
            counts["object_properties"],
            counts["datatype_properties"],
            counts["individuals"],
            counts["documents"],
            counts["merged_concepts"],
        )
        return {
            "counts": counts,
            "alignment": self.model.alignment_counts,
            "structured": [s.name for s in built],
            "documents": [s.name for s in documents],
            "left_out": missing,
        }

    def snapshot(self) -> dict[str, Any]:
        if self.model is None:  # merge was reused from an earlier pass of this run
            self.merge()
        sources = snapshot_sources(self.store, [self.fresh(s) for s in self.sources], self.model)
        revision = self.store.save_snapshot(self.run.id, self.model, sources=sources)
        counts = self.model.counts()
        logger.info(
            "  revision %d: %d class(es), %d propert(ies), %d individual(s), %d merged concept(s)",
            revision,
            counts["classes"],
            counts["object_properties"] + counts["datatype_properties"],
            counts["individuals"],
            counts["merged_concepts"],
        )
        return {
            "revision": revision,
            "counts": {k: v for k, v in counts.items() if k != "by_source"},
        }


def snapshot_sources(store: Store, sources: list[Source], model: Any) -> list[dict[str, Any]]:
    """The ``sources`` list of a snapshot: name, kind and what the model holds of each."""
    by_source = model.counts().get("by_source", {})
    included = set(model.structured_sources) | set(model.documents_sources)
    out = []
    for s in sources:
        entry: dict[str, Any] = {
            "name": s.name,
            "kind": "documents" if s.kind == "documents" else "structured",
            "source_kind": s.kind,
            "included": s.name in included,
            "counts": by_source.get(s.name, {}),
        }
        if s.kind in STRUCTURED_KINDS:
            entry["database"] = pipeline.catalog_database_name(s)
        else:
            entry["documents"] = len(DocumentStore(store).list_documents(s.id))
        out.append(entry)
    return out


def _previous_build(store: Store) -> Run | None:
    return store.latest_workspace_run(BUILD_KIND)


def refuse_if_active(store: Store, *, log_path: Path | None = None) -> None:
    """A :class:`JobError` while another build or export run is really active (runs left
    'running' by a dead process are reaped first)."""
    for run in store.active_runs():
        if run.kind not in (BUILD_KIND, EXPORT_KIND):
            continue
        reason = jobs.stale_reason(store, run)
        if reason is not None:
            jobs.reap_run(store, run, reason)
            continue
        raise JobError(
            f"another {run.kind} run {run.id} is active; wait for it to finish",
            log_path=log_path,
        )


def _resume_reuse(
    store: Store, run: Run, sources: list[Source], settings: Settings
) -> tuple[set[str], set[str]]:
    """``(steps, changed)``: the succeeded steps of the resumed *run* that can be reused as
    they are, and the sources whose inputs changed since their steps ran (all their steps
    run again). Mirrors the per-kind resume rules too: re-read documents that failed,
    re-enter an extraction that left chunks, re-consolidate an incomplete/stale
    consolidation, re-align after failed concepts."""
    from autoontology.unstructured import consolidate

    rows = {s.name: s for s in store.get_steps(run.id)}
    already = {n for n, s in rows.items() if s.status == "succeeded" and is_top_step(n)}
    changed: set[str] = set()
    for source in sources:
        group = "documents" if source.kind == "documents" else "structured"
        kept = {n for n in already if n.startswith(f"{group}:{source.name}:")}
        if not kept:
            continue
        current = input_fingerprint(source, settings.home)
        if not current or any(rows[n].detail.get(INPUTS_KEY) != current for n in kept):
            logger.info("--resume: inputs of %s changed since run %s: rebuilt", source.name, run.id)
            already -= kept
            changed.add(source.name)
    docs = DocumentStore(store)
    for source in sources:
        if source.kind != "documents":
            continue
        prefix = f"documents:{source.name}:"
        read = rows.get(prefix + "read")
        if read is not None and read.detail.get("failed"):
            already.discard(prefix + "read")
        extract = rows.get(prefix + "extract")
        if extract is not None and (
            extract.detail.get("remaining") or extract.detail.get("failed")
        ):
            already.discard(prefix + "extract")
        consolidated = rows.get(prefix + "consolidate")
        if consolidated is not None and prefix + "consolidate" in already:
            config = extract_job.consolidation_config(
                settings, consolidate.threshold_from_env(), consolidate.max_judge_from_env()
            )
            identity = consolidated.detail.get("ontology_sha")
            if (
                extract_job._consolidation_incomplete(consolidated.detail)
                or consolidated.detail.get("config") != config
                or not identity
                or identity != docs.ontology_identity(source.id)
            ):
                already.discard(prefix + "consolidate")
        aligned = rows.get(f"align:{source.name}")
        if aligned is not None and aligned.detail.get("partial"):
            already.discard(f"align:{source.name}")
    return already, changed


def _align_params() -> dict[str, Any]:
    from autoontology.unstructured import align as al

    try:
        return {
            "min_sim": al.min_sim_from_env(),
            "min_conf": al.min_conf_from_env(),
            "top_k": al.DEFAULT_TOP_K,
            "identity_policy": al.IDENTITY_POLICY_VERSION,
        }
    except ValueError as exc:
        raise JobError(str(exc)) from exc


def run_build(
    store: Store,
    settings: Settings,
    *,
    sources: list[str] | None = None,
    resume: bool = False,
    log: Callable[[str], None] | None = None,
    client: BackendClient | None = None,
    extract_backends: extract_job.Backends | None = None,
    align_backends: align_job.AlignBackends | None = None,
    writer: Any = None,
    check_service: bool = True,
) -> BuildResult:
    """Build every registered source into one snapshot (module docstring).

    *log* receives one line per step outcome (the CLI prints them); everything also goes
    to ``<home>/logs/jobs.log``."""
    log_path = jobs.setup_job_logging(settings)
    registered = registration_order(store)
    if not registered:
        raise JobError(
            "no sources: add one with `autoontology source add <path> --name <name>`",
            log_path=log_path,
        )
    only = list(dict.fromkeys(sources or []))
    unknown = [n for n in only if n not in {s.name for s in registered}]
    if unknown:
        raise JobError(
            f"unknown source {unknown[0]!r} (see `autoontology source list`)", log_path=log_path
        )
    names = [WORKSPACE_LEASE, *(s.name for s in registered)]
    with hold_sources(store, names, BUILD_KIND, log_path=log_path):
        registered = jobs.verify_sources(store, registered, log_path=log_path)
        refuse_if_active(store, log_path=log_path)
        return _run_held(
            store,
            settings,
            registered=registered,
            only=only,
            resume=resume,
            log=log or (lambda _line: None),
            client=client,
            extract_backends=extract_backends,
            align_backends=align_backends,
            writer=writer,
            check_service=check_service,
            log_path=log_path,
        )


def _run_held(
    store: Store,
    settings: Settings,
    *,
    registered: list[Source],
    only: list[str],
    resume: bool,
    log: Callable[[str], None],
    client: BackendClient | None,
    extract_backends: extract_job.Backends | None,
    align_backends: align_job.AlignBackends | None,
    writer: Any,
    check_service: bool,
    log_path: Path,
) -> BuildResult:
    client = client or BackendClient(settings.backend_url)
    if any(s.kind in STRUCTURED_KINDS for s in registered):
        client.health()  # BackendUnavailable -> "run autoontology up"
    if check_service:
        jobs.require_llm_proxy(settings, log_path=log_path)
    params: dict[str, Any] = {
        "sources": only or None,
        "resume": resume,
        "pid": os.getpid(),
        **_align_params(),
    }
    previous = _previous_build(store) if resume else None
    reuse_concepts = True
    changed: set[str] = set()
    if previous is not None:
        already, changed = _resume_reuse(store, previous, registered, settings)
        if any(previous.params.get(k) != params[k] for k in ALIGN_PARAMS):
            logger.info(
                "--resume: align settings changed since run %s: aligning again", previous.id
            )
            already = {n for n in already if not n.startswith("align:")}
            reuse_concepts = False
        store.update_run(
            previous.id,
            status="running",
            params={**previous.params, **params, "resumed_at": now_iso()},
        )
        run = store.get_run(previous.id)
        assert run is not None
        logger.info(
            "resuming build run %s (reusable: %s)", run.id, ", ".join(sorted(already)) or "-"
        )
    else:
        if resume:
            logger.info("no earlier build run; starting a new one")
        run = store.create_run(kind=BUILD_KIND, source_id=None, params=params)
        already = set()
        logger.info(
            "build run %s: %s",
            run.id,
            ", ".join(f"{s.name} ({s.kind})" for s in registered),
        )
    build = _Build(
        settings=settings,
        store=store,
        run=run,
        sources=registered,
        client=client,
        resume=previous is not None,
        extract_backends=extract_backends,
        align_backends=align_backends,
        writer=writer,
    )
    build.reuse_concepts = reuse_concepts
    build.changed = changed
    plan: list[_Step] = []
    skip: dict[str, str] = {}
    for s in build.structured:
        for step in STRUCTURED_STEPS:
            name = f"structured:{s.name}:{step}"
            plan.append(_Step(name, s.name, build.structured_step(s, step)))
            if step == "load" and s.kind != "files":
                skip[name] = "database source: data stays in the external database"
    for s in build.documents:
        for step in DOCUMENTS_STEPS:
            name = f"documents:{s.name}:{step}"
            plan.append(_Step(name, s.name, build.documents_step(s, step)))
    if only:
        for step in plan:
            if step.group not in only:
                skip.setdefault(step.name, f"--source {', '.join(only)}")
    for s in build.documents:
        plan.append(_Step(f"align:{s.name}", GLOBAL, build.align_step(s)))
    plan.append(_Step("merge", GLOBAL, build.merge))
    plan.append(_Step("snapshot", GLOBAL, build.snapshot))
    result = _execute(build, plan, already=already, skip=skip, log=log, log_path=log_path)
    return _finish(build, result)


def _execute(
    build: _Build,
    plan: list[_Step],
    *,
    already: set[str],
    skip: dict[str, str],
    log: Callable[[str], None],
    log_path: Path,
) -> BuildResult:
    """Run *plan* in order, persisting each step row (like :func:`jobs._execute`, with
    dependency-aware reuse: a step that runs again invalidates the later steps of its own
    source and every global step, nothing else)."""
    store, run = build.store, build.run
    result = BuildResult(run_id=run.id, status="running")
    total = len(plan)
    dirty: set[str] = set()
    for index, step in enumerate(plan, start=1):
        label = f"[{index}/{total}] {step.name}"
        stale = step.group in dirty or (step.group == GLOBAL and dirty)
        if step.name in already and not stale:
            logger.info("%s: already succeeded in run %s, kept (--resume)", label, run.id)
            log(f"{label}: kept")
            result.steps[step.name] = "succeeded"
            continue
        if step.name in skip:
            store.finish_step(
                run.id, step.name, status="skipped", detail={"reason": skip[step.name]}
            )
            logger.info("%s: skipped (%s)", label, skip[step.name])
            log(f"{label}: skipped ({skip[step.name]})")
            result.steps[step.name] = "skipped"
            continue
        try:
            check_leases()  # never mutate after another operation took the sources over
        except LeaseLost as exc:
            store.update_run(run.id, status="failed", error=f"{step.name}: {exc}")
            logger.error("%s: %s", label, exc)
            raise JobError(
                f"step {step.name} not started: {exc}",
                run_id=run.id,
                step=step.name,
                log_path=log_path,
            ) from exc
        logger.info("%s ...", label)
        store.start_step(run.id, step.name)
        started = time.monotonic()
        try:
            detail = step.fn()
        except BaseException as exc:
            seconds = round(time.monotonic() - started, 1)
            interrupted = not isinstance(exc, Exception)
            message = "interrupted" if interrupted else short_error(exc)
            store.finish_step(
                run.id, step.name, status="failed", detail={"error": message, "seconds": seconds}
            )
            store.update_run(
                run.id,
                status="cancelled" if interrupted else "failed",
                error=f"{step.name}: {message}",
            )
            logger.exception("%s failed after %.1fs", label, seconds)
            log(f"{label}: FAILED ({message})")
            if interrupted:
                raise
            raise JobError(
                f"step {step.name} failed: {message}",
                run_id=run.id,
                step=step.name,
                log_path=log_path,
            ) from exc
        detail["seconds"] = round(time.monotonic() - started, 1)
        status = "skipped" if detail.get("skipped") else "succeeded"
        store.finish_step(run.id, step.name, status=status, detail=detail)
        dirty.add(step.group)
        outcome = "ok" if status == "succeeded" else f"skipped ({detail['skipped']})"
        logger.info("%s: %s (%.1fs)", label, outcome, detail["seconds"])
        log(f"{label}: {'ok' if status == 'succeeded' else 'skipped'} ({detail['seconds']}s)")
        result.steps[step.name] = status
        result.details[step.name] = detail
    return result


def _problems(store: Store, run_id: str) -> list[str]:
    """Why a finished build is partial: failed documents/chunks, an incomplete
    consolidation, failed align judgements (all retried by ``--resume``)."""
    out: list[str] = []
    for step in store.get_steps(run_id):
        d = step.detail
        if step.name.endswith(":read") and d.get("failed"):
            out.append(f"{step.name}: {len(d['failed'])} document(s) failed to read")
        if step.name.endswith(":extract") and d.get("failed"):
            out.append(f"{step.name}: {d['failed']} of {d.get('attempted', '?')} chunk(s) failed")
        if step.name.endswith(":consolidate") and d.get("partial"):
            out.append(f"{step.name}: consolidation incomplete")
        if step.name.startswith("align:") and d.get("partial"):
            out.append(f"{step.name}: {d.get('failed', 0)} concept(s) failed")
    return out


def _warnings(store: Store, run_id: str) -> list[str]:
    """What the run skipped without failing: documents that need the kordoc engine."""
    from autoontology.unstructured.kordoc import INSTALL_COMMAND
    from autoontology.unstructured.report import display_path

    out = []
    for step in store.get_steps(run_id):
        skipped = step.detail.get("needs_kordoc") if step.name.endswith(":read") else None
        if skipped:
            names = ", ".join(display_path(f["path"]) for f in skipped)
            out.append(
                f"{step.name}: {len(skipped)} document(s) need kordoc and were skipped "
                f"({names}): run `{INSTALL_COMMAND}`, then build again"
            )
    return out


def _finish(build: _Build, result: BuildResult) -> BuildResult:
    store, run = build.store, build.run
    snap = next(
        (s for s in store.get_steps(run.id) if s.name == "snapshot" and s.status == "succeeded"),
        None,
    )
    if snap is not None:
        result.revision = snap.detail.get("revision")
        result.counts = snap.detail.get("counts", {})
    result.problems = _problems(store, run.id)
    result.warnings = _warnings(store, run.id)  # the CLI prints them after the run
    if result.problems:
        error = "partial: " + " | ".join(result.problems)
        store.update_run(
            run.id,
            status="failed",
            error=error,
            params={**(store.get_run(run.id) or run).params, "partial": True},
        )
        result.status = "failed"
        logger.warning("build run %s: %s (retry with --resume)", run.id, error)
    else:
        store.update_run(run.id, status="succeeded")
        result.status = "succeeded"
    return result
