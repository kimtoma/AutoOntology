# SPDX-License-Identifier: MIT
"""Remove a registered source (``autoontology source rm`` and ``DELETE /api/sources``).

Structured sources: delete the upstream connection, wait until the service has torn down
the ingested graph (``/ingest/delete`` -> ``delete_all_data``), then optionally drop the
files source's ``autoontology_src_<name>`` database, then delete the store rows. Documents
sources only live in the store.
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
    from autoontology.config import validate_pg_database_name
    from autoontology.store.db import SourceBusy

    def say(message: str, warning: bool = False) -> None:
        if notify:
            notify(message, warning)

    timeout = RM_GRAPH_WAIT_S if timeout is None else timeout
    if drop_db and src.kind == "files" and src.pg_database:
        try:  # before anything is torn down: never DROP a name Postgres would truncate
            validate_pg_database_name(src.pg_database)
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
    from autoontology.config import PG_MAINTENANCE_DB, validate_pg_database_name
    from autoontology.engine import pg
    from autoontology.engine.client import BackendClient, BackendError, BackendUnavailable
    from autoontology.structured import pipeline
    from autoontology.structured.loaders import quote_ident

    if store.active_runs(src.id):
        raise RemoveError(f"source {src.name!r} has an active run; wait for it to finish")
    if src.kind == "documents":  # nothing upstream: documents/concepts live in the store
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
            validate_pg_database_name(src.pg_database)  # never DROP a truncated name
            if held is not None and not held.owned():
                raise RemoveError("lease lost before DROP DATABASE; nothing was dropped")
            with pg.connect(settings, PG_MAINTENANCE_DB) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(src.pg_database)} WITH (FORCE)")
            say(f"dropped database {src.pg_database}")
    store.delete_source(src.id)
    say(f"removed source {name}")
