# SPDX-License-Identifier: MIT
"""Remove a registered source (``autoontology source rm`` and ``DELETE /api/sources``).

Structured sources: delete the upstream connection, wait until the service has torn down
the ingested graph (``/ingest/delete`` -> ``delete_all_data``), then optionally drop the
files source's ``autoontology_src_<name>`` database, then delete the store rows. Documents
sources live in the store; the synonyms their alignments wrote to upstream Terms are taken
off first (:func:`remove_added_aliases`; manual and other sources' synonyms stay).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autoontology.config import Settings
from autoontology.store.db import Source, Store

#: How long removal waits for the service to tear down the ingested graph.
RM_GRAPH_WAIT_S = 120.0
RM_POLL_S = 1.0

#: ``notify(message, is_warning)``
Notify = Callable[[str, bool], None]


#: What a user types to delete upstream connections no registered source owns.
PRUNE_COMMAND = "uv run autoontology tools prune-connections"
#: Only connections AutoOntology itself created (files sources) are ever pruned.
ORPHAN_CONNECTION_RE = re.compile(r"^autoontology_src_[a-z0-9_]+$")


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
        f"later with `{PRUNE_COMMAND}` with the services up"
    )
    connection_gone = False
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
            connection_gone = True
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
        elif not connection_gone:
            # a connection to a dropped database makes every later ingest time out
            say(
                f"warning: database {src.pg_database} kept: its upstream connection could "
                "not be deleted, and dropping the database under it would break every "
                "later build. Start the services (`uv run autoontology up`), then run "
                f"`{PRUNE_COMMAND} --drop-db`",
                True,
            )
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


@dataclass(frozen=True)
class OrphanConnection:
    """An upstream connection to a per-source database that no registered source owns,
    or whose database is gone."""

    name: str
    database_missing: bool
    owned: bool

    @property
    def reason(self) -> str:
        parts = []
        if self.database_missing:
            parts.append("database missing")
        if not self.owned:
            parts.append("no registered source")
        return ", ".join(parts)


def backend_is_ours(settings: Settings) -> bool:
    """The backend port answers and every listener is a process AutoOntology started
    (never query or prune another app's backend)."""
    from autoontology.engine.supervisor import Supervisor, listening_pids, port_in_use

    if not port_in_use(settings.backend_port):
        return False
    owned = Supervisor.from_settings(settings).owned_pids()
    holders = listening_pids(settings.backend_port)

    def pgid(pid: int) -> int | None:
        import os

        try:
            return os.getpgid(pid)
        except OSError:
            return None

    return bool(holders) and all(p in owned or pgid(p) in owned for p in holders)


def find_orphan_connections(
    settings: Settings,
    store: Store | None,
    client: Any,
    *,
    database_exists: Callable[[str], bool] | None = None,
) -> list[OrphanConnection]:
    """Upstream connections named ``autoontology_src_<name>`` whose database no longer
    exists in Postgres or that no registered source owns (sorted by name)."""
    from autoontology.structured import pipeline

    if database_exists is None:
        from autoontology.engine import pg

        def database_exists(name: str) -> bool:
            return pg.database_exists(settings, name)

    owned: set[str] = set()
    for src in store.list_sources() if store is not None else []:
        if src.kind in ("files", "database"):
            try:
                owned.add(pipeline.catalog_database_name(src).casefold())
            except Exception:  # noqa: BLE001 - an unparsable locator owns nothing
                continue
    out: dict[str, OrphanConnection] = {}
    for conn in client.list_connections():
        name = str(conn.get("database_name") or "")
        if not ORPHAN_CONNECTION_RE.fullmatch(name):
            continue
        missing = not database_exists(name)
        is_owned = name.casefold() in owned
        if missing or not is_owned:
            out[name] = OrphanConnection(name, missing, is_owned)
    return [out[k] for k in sorted(out)]


def prune_orphan_connections(
    settings: Settings,
    client: Any,
    orphans: list[OrphanConnection],
    *,
    drop_db: bool = False,
    say: Callable[..., None] | None = None,
    timeout: float | None = None,
    poll: float | None = None,
    database_exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Delete each orphan's upstream connection (and wait for its graph teardown); with
    *drop_db*, then drop its database when it still exists and no source owns it.
    Returns the deleted connection names."""
    from autoontology.config import PG_MAINTENANCE_DB, assert_source_database
    from autoontology.engine import pg
    from autoontology.engine.client import BackendError
    from autoontology.structured.loaders import quote_ident

    tell = say or (lambda _m, _w=False: None)
    exists = database_exists or (lambda name: pg.database_exists(settings, name))
    deleted = []
    for orphan in orphans:
        name = orphan.name
        if not ORPHAN_CONNECTION_RE.fullmatch(name):  # never anything AutoOntology did not make
            tell(f"skipped {name}: not an AutoOntology connection", True)
            continue
        try:
            client.delete_connection(name)
        except BackendError as exc:
            if exc.status != 404:
                tell(f"warning: could not delete upstream connection {name}: {exc}", True)
                continue
        deleted.append(name)
        if wait_catalog_gone(client, name, timeout=timeout, poll=poll):
            tell(f"deleted upstream connection {name} ({orphan.reason})")
        else:
            tell(
                f"deleted upstream connection {name}; its graph is still being torn down "
                f"(see {settings.logs_dir / 'service.log'})",
                True,
            )
            continue
        if drop_db and orphan.owned is False and exists(name):
            try:
                assert_source_database(settings, name)
            except ValueError as exc:
                tell(f"database {name} kept: {exc}", True)
                continue
            with pg.connect(settings, PG_MAINTENANCE_DB) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(name)} WITH (FORCE)")
            tell(f"dropped database {name}")
    return deleted


def _other_support(store: Store, src: Source) -> dict[str, dict[str, list[str]]]:
    """term id -> normalized alias -> ids of the *other* documents sources whose accepted
    ``same`` alignment still accounts for that alias on the Term (sorted)."""
    from autoontology.store.alignments import AlignmentStore
    from autoontology.store.documents import DocumentStore
    from autoontology.unstructured import align as al

    aligns, docs = AlignmentStore(store), DocumentStore(store)
    support: dict[str, dict[str, set[str]]] = {}
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
            per_term = support.setdefault(a.term_id, {})
            for alias in al.accepted_aliases(info):
                per_term.setdefault(alias, set()).add(other.id)
    return {t: {k: sorted(v) for k, v in m.items()} for t, m in support.items()}


def _split_plan(
    store: Store, src: Source
) -> tuple[dict[str, list[str]], list[tuple[str, str, str]]]:
    """(removal plan, handover): *src*'s live recorded synonyms that no other documents
    source supports go; the supported ones stay and are handed over to a supporting
    source ``(term id, alias, new source id)``, so removing the last supporter takes them
    off too."""
    from autoontology.store.alignments import AlignmentStore
    from autoontology.unstructured import align as al

    support = _other_support(store, src)
    plan: dict[str, list[str]] = {}
    handover: list[tuple[str, str, str]] = []
    for term_id, records in AlignmentStore(store).alias_provenance(live=True).items():
        safe = support.get(term_id, {})
        for r in records:
            if r["source_id"] != src.id:
                continue
            others = safe.get(al.norm_alias(r["alias"]))
            if others:
                handover.append((term_id, r["alias"], others[0]))
            else:
                plan.setdefault(term_id, []).append(r["alias"])
    return plan, handover


def alias_removal_plan(store: Store, src: Source) -> dict[str, list[str]]:
    """term id -> the live synonyms that documents source *src*'s alignments wrote to the
    Term (recorded provenance), minus those an accepted ``same`` alignment of another
    documents source also accounts for. Manual aliases and other sources' are never in it."""
    return _split_plan(store, src)[0]


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

    plan, handover = _split_plan(store, src)
    aligns = AlignmentStore(store)
    for term_id, alias, new_owner in handover:  # still supported: the supporter owns it now
        aligns.reassign_alias_source(term_id, alias, new_owner)
    if not plan:
        return
    if remover is None:
        from autoontology.engine.upstream_terms import remove_term_synonyms as remover
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
        if removed and result.get("embeddings_refreshed") is False:
            # the next align run retries it (the queue outlives this source)
            aligns.add_pending_refresh(term_id, f"synonyms removed by source rm {src.name}")
            say(
                f"warning: re-embedding Term {term_id} failed; queued for the next align run",
                True,
            )
        say(
            f"removed {len(removed)} synonym(s) {src.name} added to Term {term_id}"
            + (f" ({', '.join(removed)})" if removed else "")
        )
