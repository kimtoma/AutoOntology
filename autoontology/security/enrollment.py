# SPDX-License-Identifier: MIT
"""Read-only enrollment classification shared by adapters and effect boundaries.

Surviving identity history is evidence of prior enrollment, never a new local
workspace. This database check does not replace the independent host marker and
owner recovery ceremony required for completely lost or rolled-back metadata.
"""

import math
import re
import sqlite3

from autoontology.security.policy import AccessError

IDENTITY_TABLES = frozenset(
    {
        "identity_state",
        "identity_principals",
        "identity_domains",
        "identity_grants",
        "identity_credentials",
        "identity_delegations",
        "identity_source_domains",
        "identity_login_limits",
        "identity_events",
    }
)
STATE_COLUMNS = frozenset(
    {"id", "organization_id", "workspace_id", "workspace_name", "policy_version", "created_at"}
)


def _missing():
    return AccessError(
        "Identity configuration cannot be verified; local-owner recovery is required.",
        "identity_configuration_missing",
        503,
    )


def _mismatch():
    return AccessError(
        "An authenticated execution cannot change or lose its workspace.",
        "execution_workspace_mismatch",
        403,
    )


def enrollment_state(conn, *, bound_workspace=None):
    """Return verified state or None for an untouched local/legacy workspace.

    The caller holds one read or write transaction so metadata checks cannot
    combine observations from different commits. No schema or data is changed.
    """
    if not conn.in_transaction:
        raise RuntimeError("enrollment classification needs an active transaction")
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = (
            conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
            if "schema_version" in tables
            else None
        )
        identity_tables = {name for name in tables if name.startswith("identity_")}
        if not isinstance(version, int) or version < 1:
            raise _missing()
        if not identity_tables and version < 12:
            if bound_workspace is not None:
                raise _mismatch()
            return None
        if version < 12 or not IDENTITY_TABLES.issubset(tables):
            raise _missing()
        cursor = conn.execute("SELECT * FROM identity_state WHERE id=1")
        columns = [column[0] for column in cursor.description]
        if not STATE_COLUMNS.issubset(columns):
            raise _missing()
        row = cursor.fetchone()
        state = dict(zip(columns, row)) if row else None
        if state is not None:
            for key in ("organization_id", "workspace_id"):
                if not isinstance(state[key], str) or not re.fullmatch("[0-9a-f]{32}", state[key]):
                    raise _missing()
            if (
                not isinstance(state["workspace_name"], str)
                or not state["workspace_name"].strip()
                or len(state["workspace_name"]) > 100
                or type(state["policy_version"]) is not int
                or state["policy_version"] < 1
                or not isinstance(state["created_at"], (int, float))
                or not math.isfinite(state["created_at"])
                or state["created_at"] < 0
            ):
                raise _missing()
        if bound_workspace is not None and (
            state is None or state.get("workspace_id") != bound_workspace
        ):
            raise _mismatch()
        if state is not None:
            if not conn.execute("SELECT 1 FROM identity_principals LIMIT 1").fetchone():
                raise _missing()
            return state
        for name in identity_tables:
            quoted = '"' + name.replace('"', '""') + '"'
            if conn.execute(f"SELECT 1 FROM {quoted} LIMIT 1").fetchone():
                raise _missing()
        if (
            "sqlite_sequence" in tables
            and conn.execute(
                "SELECT 1 FROM sqlite_sequence WHERE name='identity_events' AND seq>0"
            ).fetchone()
        ):
            raise _missing()
        return None
    except sqlite3.DatabaseError:
        raise _missing() from None
