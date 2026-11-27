# SPDX-License-Identifier: MIT
"""Writes to upstream Terms that the public API cannot make.

``PATCH /api/terms/{id}`` (``auto_ontology/server/terms/router.py:34`` ``TermUpdate``)
accepts name, description, certification flags and owner only: there is **no synonyms
field**, and pydantic silently drops one (a synonyms-only body answers 422 "No Term fields
to update"). Upstream writes ``term.synonyms`` only during compilation
(``dal/terms.py`` ``upsert_table_term``). So the align step of ``onto build`` writes synonyms in-process
through upstream's own DAL -- ``dal.schema.term``, ``dal.session.write_transaction`` /
``store`` -- and then refreshes the Term's semantic embeddings with
``server.terms.service.refresh_term_embeddings``, as the PATCH route does after a
description change, so search and text-to-SQL see the new aliases.

These are upstream internals, pinned by ``tests/test_upstream_terms.py`` against the
vendored source; :func:`require_internals` fails loudly at runtime too.

A recompilation of the Term's table (``source rm`` + fresh ``build``) resets synonyms to
the compiler's; re-running ``align`` restores them.

Projects: every write takes the *databases* of the calling project's structured sources;
a Term that no table of those databases represents is refused (:class:`TermNotInProject`)
inside the same write transaction, so a project never edits another project's Terms (a
database belongs to one project only, ``structured/pipeline.py`` ``add_source``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ontology.config import Settings

logger = logging.getLogger("ontology.jobs")


class UpstreamInternalsChanged(RuntimeError):
    pass


class TermNotInProject(PermissionError):
    """The Term belongs to no structured source of the calling project."""


def term_in_databases(found: Sequence[str], databases: Sequence[str]) -> bool:
    """Whether a Term represented in the catalog databases *found* belongs to a project
    whose structured sources are *databases* (case-insensitive, like the catalog keys)."""
    wanted = {d.casefold() for d in databases}
    return any(str(d).casefold() in wanted for d in found)


def _guard(up: dict[str, Any], term_id: str, databases: Sequence[str] | None) -> None:
    """Raise :class:`TermNotInProject` unless *databases* is None or the Term is one of a
    table of them (read inside the caller's transaction)."""
    if databases is None:
        return
    from ontology.engine.upstream_semantic import term_databases_statement

    rows = up["store"]().query_write(term_databases_statement(up["s"], term_id))
    found = [r["name"] for r in rows]
    if not term_in_databases(found, databases):
        raise TermNotInProject(
            f"upstream Term {term_id} belongs to no structured source of this project "
            f"(its databases: {', '.join(sorted(found)) or 'none'}); refusing to change it"
        )


def check_term_in_databases(settings: Settings, term_id: str, databases: Sequence[str]) -> None:
    """:class:`TermNotInProject` unless Term *term_id* belongs to *databases* (a read in
    its own transaction: the description PATCH goes through upstream's API)."""
    from ontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    up = require_internals()
    with up["write_transaction"]():
        _guard(up, term_id, databases)


def require_internals() -> dict[str, Any]:
    """Import the upstream pieces the synonyms write needs, or raise a clear error."""
    try:
        from auto_ontology.dal import schema as s
        from auto_ontology.dal.session import store, write_transaction
        from auto_ontology.server.terms.service import refresh_term_embeddings
    except ImportError as exc:  # pragma: no cover - guarded by the pin test
        raise UpstreamInternalsChanged(
            f"upstream moved the Term DAL/service ({exc}); update ontology/engine/upstream_terms.py"
        ) from exc
    missing = [c for c in ("synonyms", "name", "modified", "modified_by") if c not in s.term.c]
    if missing:
        raise UpstreamInternalsChanged(
            f"upstream table `term` has no {missing} column(s); "
            "update ontology/engine/upstream_terms.py"
        )
    return {
        "s": s,
        "store": store,
        "write_transaction": write_transaction,
        "refresh_term_embeddings": refresh_term_embeddings,
    }


def refresh_term(settings: Settings, term_id: str) -> bool:
    """Re-embed one Term in upstream's semantic VDB (best effort, like the PATCH route).
    Returns False on failure (logged); ``align`` retries it on the next run."""
    from ontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    up = require_internals()
    try:
        up["refresh_term_embeddings"](term_id, refresh_dependent_attrs=False)
    except Exception:  # noqa: BLE001 - best effort, as upstream's own PATCH route
        logger.warning("refreshing the embeddings of Term %s failed", term_id, exc_info=True)
        return False
    return True


def add_term_synonyms(
    settings: Settings,
    term_id: str,
    additions: Sequence[str],
    *,
    databases: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Merge *additions* into ``term.synonyms`` and refresh the Term's embeddings.

    The merge happens inside one upstream write transaction: the current array is re-read
    with ``SELECT ... FOR UPDATE`` (row lock), merged (:func:`merge_synonyms`: existing
    first, case-insensitive dedupe, never removes) and written back, so an edit made
    between ``align``'s plan and this write is kept instead of overwritten. ``modified`` /
    ``modified_by`` are stamped as upstream's PATCH does (``modified_by`` = the user id
    Ontology sends, :data:`ontology.engine.client.USER_ID`).

    Returns ``{"synonyms", "added", "embeddings_refreshed"}``; nothing is written (and no
    refresh is made) when every addition is already there."""
    from ontology.engine.client import USER_ID
    from ontology.engine.jobs import prepare_upstream_env
    from ontology.unstructured.align import merge_synonyms

    prepare_upstream_env(settings)
    up = require_internals()
    from sqlalchemy import func, select, update

    s = up["s"]
    with up["write_transaction"]():
        _guard(up, term_id, databases)
        rows = up["store"]().query_write(
            select(s.term.c.name, s.term.c.synonyms).where(s.term.c.id == term_id).with_for_update()
        )
        if not rows:
            raise LookupError(f"upstream Term {term_id} not found")
        current = list(rows[0]["synonyms"] or [])
        merged = merge_synonyms(current, additions, rows[0]["name"] or "")
        added = merged[len(current) :]
        if added:
            up["store"]().query_write(
                update(s.term)
                .where(s.term.c.id == term_id)
                .values(synonyms=merged, modified=func.now(), modified_by=USER_ID)
            )
    if not added:
        return {"synonyms": current, "added": [], "embeddings_refreshed": None}
    return {
        "synonyms": merged,
        "added": added,
        "embeddings_refreshed": refresh_term(settings, term_id),
    }


def set_term_synonyms(
    settings: Settings,
    term_id: str,
    synonyms: Sequence[str],
    *,
    databases: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Replace ``term.synonyms`` outright (manual corrections only; ``align`` never
    removes synonyms and uses :func:`add_term_synonyms`)."""
    from ontology.engine.client import USER_ID
    from ontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    up = require_internals()
    from sqlalchemy import func, update

    s = up["s"]
    with up["write_transaction"]():
        _guard(up, term_id, databases)
        rows = up["store"]().query_write(
            update(s.term)
            .where(s.term.c.id == term_id)
            .values(synonyms=list(synonyms), modified=func.now(), modified_by=USER_ID)
            .returning(s.term.c.id, s.term.c.synonyms)
        )
    if not rows:
        raise LookupError(f"upstream Term {term_id} not found")
    return {
        "synonyms": list(rows[0]["synonyms"] or []),
        "embeddings_refreshed": refresh_term(settings, term_id),
    }


def remove_term_synonyms(
    settings: Settings,
    term_id: str,
    removals: Sequence[str],
    *,
    databases: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Reviewed repair of a wrong alignment: take *removals* (the exact strings recorded
    as added: no case or whitespace normalisation, whole aliases only, so a manual
    ``계약  서`` or ``계약서`` survives the removal of ``계약 서``) off ``term.synonyms`` inside one write transaction
    (``SELECT ... FOR UPDATE``, so a concurrent edit is kept), then refresh the Term's
    embeddings. ``align`` itself never removes synonyms.

    Returns ``{"synonyms", "removed", "embeddings_refreshed"}`` (no write and no refresh
    when none of *removals* is there)."""
    from ontology.engine.client import USER_ID
    from ontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    up = require_internals()
    from sqlalchemy import func, select, update

    drop = {r for r in removals if r}
    s = up["s"]
    with up["write_transaction"]():
        _guard(up, term_id, databases)
        rows = up["store"]().query_write(
            select(s.term.c.name, s.term.c.synonyms).where(s.term.c.id == term_id).with_for_update()
        )
        if not rows:
            raise LookupError(f"upstream Term {term_id} not found")
        current = list(rows[0]["synonyms"] or [])
        kept = [x for x in current if x not in drop]
        removed = [x for x in current if x in drop]
        if removed:
            up["store"]().query_write(
                update(s.term)
                .where(s.term.c.id == term_id)
                .values(synonyms=kept, modified=func.now(), modified_by=USER_ID)
            )
    if not removed:
        return {"synonyms": current, "removed": [], "embeddings_refreshed": None}
    return {
        "synonyms": kept,
        "removed": removed,
        "embeddings_refreshed": refresh_term(settings, term_id),
    }
