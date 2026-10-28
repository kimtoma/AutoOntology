# SPDX-License-Identifier: MIT
"""Remove a registered source (``autoontology source rm`` and ``DELETE /api/sources``).

Structured sources: delete the upstream connection, wait until the service has torn down
the ingested graph (``/ingest/delete`` -> ``delete_all_data``), then optionally drop the
files source's ``autoontology_src_<name>`` database, then delete the store rows. Documents
sources live in the store; the synonyms their alignments wrote to upstream Terms are taken
off first (:func:`remove_added_aliases`; manual and other sources' synonyms stay).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from autoontology.config import Settings
from autoontology.store.db import Source, Store

#: How long removal waits for the service to tear down the ingested graph.
RM_GRAPH_WAIT_S = 120.0
RM_POLL_S = 1.0

#: ``notify(message, is_warning)``
Notify = Callable[[str, bool], None]


class RemoveError(RuntimeError):
    """Removal stopped; nothing was dropped and the source row is kept."""


def wait_catalog_gone(
    client, database: str, *, timeout: float | None = None, poll: float | None = None
) -> bool:
    """Poll the upstream catalog until *database* is gone (the service's /ingest/delete
    thread runs ``delete_all_data``). True when gone within *timeout*."""
    timeout = RM_GRAPH_WAIT_S if timeout is None else timeout
    poll = RM_POLL_S if poll is None else poll
    deadline = time.monotonic() + timeout
    while True:
        if client.database_id_by_name(database) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def remove_source(
    settings: Settings,
    store: Store,
    src: Source,
    *,
    drop_db: bool = False,
    force: bool = False,
    notify: Notify | None = None,
    timeout: float | None = None,
    poll: float | None = None,
) -> None:
    """Remove *src*. Raises :class:`RemoveError` (source kept) when the backend teardown
    fails or does not finish and *force* is not set, or another operation holds the
    source's lease."""
    from autoontology.config import assert_source_database
    from autoontology.store.db import SourceBusy

    def say(message: str, warning: bool = False) -> None:
        if notify:
            notify(message, warning)

    timeout = RM_GRAPH_WAIT_S if timeout is None else timeout
    if drop_db and src.kind == "files" and src.pg_database:
        try:  # before anything is torn down: never DROP a name Postgres would truncate
            assert_source_database(settings, src.pg_database)
        except ValueError as exc:
            raise RemoveError(f"refusing --drop-db: {exc}") from exc
    try:  # held through the whole teardown: no build/extract/align may start meanwhile
        lease = store.lease([src.name], "remove")
        held = lease.__enter__()
    except SourceBusy as exc:
        raise RemoveError(str(exc)) from exc
    try:
        # the request resolved *src* before the lease: re-read it, and refuse when the
        # name now belongs to another source (never tear down a replacement)
        current = store.get_source(src.name)
        if current is None or current.id != src.id:
            raise RemoveError(
                f"source {src.name!r} was removed or replaced since this "
                "request was made; nothing was removed"
            )
        _remove_held(
            settings,
            store,
            current,
            drop_db=drop_db,
            force=force,
            say=say,
            timeout=timeout,
            poll=poll,
            held=held,
        )
    finally:
        lease.__exit__(None, None, None)


def _remove_held(
    settings: Settings,
    store: Store,
    src: Source,
    *,
    drop_db: bool,
    force: bool,
    say: Callable[..., None],
    timeout: float,
    poll: float | None,
    held: Any = None,
) -> None:
    from autoontology.config import PG_MAINTENANCE_DB, assert_source_database
    from autoontology.engine import pg
    from autoontology.engine.client import BackendClient, BackendError, BackendUnavailable
    from autoontology.structured import pipeline
    from autoontology.structured.loaders import quote_ident

    if store.active_runs(src.id):
        raise RemoveError(f"source {src.name!r} has an active run; wait for it to finish")
    if src.kind == "documents":  # documents/concepts live in the store
        remove_added_aliases(settings, store, src, force=force, say=say)
        store.delete_source(src.id)
        say(
            f"removed documents source {src.name} (documents, chunks, concepts, "
            "relations, evidence)"
        )
        return
    name = src.name
    database = pipeline.catalog_database_name(src)
    leftover = (
        f"the upstream connection/catalog entry for {database} may remain; remove it "
        f"later by re-adding the source as {name!r} and running `source rm {name}` "
        "with the services up"
    )
    with BackendClient(settings.backend_url) as client:
        try:
            try:
                client.delete_connection(database)
                say(
                    f"deleted upstream connection {database}; waiting for the "
                    "service to delete its ingested graph ..."
                )
            except BackendError as exc:
                if exc.status != 404:
                    raise
                say(f"no upstream connection {database} (already gone)")
            if wait_catalog_gone(client, database, timeout=timeout, poll=poll):
                say(f"ingested graph of {database} deleted")
            elif force:
                say(
                    f"warning: graph deletion of {database} still running after "
                    f"{timeout:.0f}s (--force: continuing); see "
                    f"{settings.logs_dir / 'service.log'}",
                    True,
                )
            else:
                raise RemoveError(
                    f"graph deletion of {database} did not finish within {timeout:.0f}s; "
                    f"nothing was dropped. See {settings.logs_dir / 'service.log'} and re-run "
                    f"`source rm {name}` (or add --force)"
                )
        except BackendUnavailable as exc:
            if not force:
                raise RemoveError(
                    f"source rm failed: {exc} (or use --force to remove it locally only)"
                ) from exc
            say(f"warning: backend down (--force): {leftover}", True)
        except BackendError as exc:
            if not force:
                raise RemoveError(f"source rm failed: {exc}") from exc
            say(f"warning: {exc} (--force): {leftover}", True)
    if drop_db:
        if src.kind != "files" or not src.pg_database:
            say("--drop-db ignored: only files sources own a local database")
        else:
            # never DROP a truncated name, nor the catalog database itself
            assert_source_database(settings, src.pg_database)
            if held is not None and not held.owned():
                raise RemoveError("lease lost before DROP DATABASE; nothing was dropped")
            with pg.connect(settings, PG_MAINTENANCE_DB) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(src.pg_database)} WITH (FORCE)")
            say(f"dropped database {src.pg_database}")
    store.delete_source(src.id)
    say(f"removed source {name}")


def alias_removal_plan(store: Store, src: Source) -> dict[str, list[str]]:
    """term id -> the live synonyms that documents source *src*'s alignments wrote to the
    Term (recorded provenance), minus those an accepted ``same`` alignment of another
    documents source also accounts for. Manual aliases and other sources' are never in it."""
    from autoontology.store.alignments import AlignmentStore
    from autoontology.store.documents import DocumentStore
    from autoontology.unstructured import align as al

    aligns, docs = AlignmentStore(store), DocumentStore(store)
    protected: dict[str, set[str]] = {}
    for other in store.list_sources():
        if other.kind != "documents" or other.id == src.id:
            continue
        concepts = {c.id: c for c in docs.list_concepts(other.id)}
        for a in aligns.list(other.id):
            c = concepts.get(a.concept_id)
            if not (a.accepted and a.relation == "same" and a.term_id and c is not None):
                continue
            info = al.ConceptInfo(
                id=c.id, name=c.name, kind=c.kind, name_en=c.name_en, synonyms=c.synonyms
            )
            protected.setdefault(a.term_id, set()).update(al.accepted_aliases(info))
    plan: dict[str, list[str]] = {}
    for term_id, records in aligns.alias_provenance(live=True).items():
        safe = protected.get(term_id, set())
        mine = [
            r["alias"]
            for r in records
            if r["source_id"] == src.id and al.norm_alias(r["alias"]) not in safe
        ]
        if mine:
            plan[term_id] = mine
    return plan


def remove_added_aliases(
    settings: Settings,
    store: Store,
    src: Source,
    *,
    force: bool,
    say: Callable[..., None],
    remover: Callable[[Settings, str, list[str]], dict[str, Any]] | None = None,
) -> None:
    """Take the synonyms *src*'s alignments added (:func:`alias_removal_plan`) off the
    upstream Terms before the source goes. A failure keeps the source (RemoveError)
    unless *force*, which only warns."""
    from autoontology.store.alignments import AlignmentStore

    plan = alias_removal_plan(store, src)
    if not plan:
        return
    if remover is None:
        from autoontology.engine.upstream_terms import remove_term_synonyms as remover
    aligns = AlignmentStore(store)
    for term_id, aliases in sorted(plan.items()):
        try:
            result = remover(settings, term_id, aliases)
            removed = result.get("removed", [])
        except LookupError:
            removed = []  # the Term is gone already
        except Exception as exc:  # noqa: BLE001 - one message; --force continues
            if not force:
                raise RemoveError(
                    f"could not remove the synonyms {src.name} added to Term {term_id} "
                    f"({', '.join(aliases)}): {exc} (start the services, or --force to keep "
                    "them)"
                ) from exc
            say(f"warning: synonyms of Term {term_id} kept (--force): {exc}", True)
            continue
        aligns.mark_aliases_removed(term_id, aliases)
        say(
            f"removed {len(removed)} synonym(s) {src.name} added to Term {term_id}"
            + (f" ({', '.join(removed)})" if removed else "")
        )
