# SPDX-License-Identifier: MIT
"""Saved-question ownership shared by HTTP, CLI and producer write transactions.

Unknown historical ownership stays unknown. A workspace administrator may inspect
it, but cannot continue a conversation as its original author. OS-level database
access is a trusted maintenance boundary, not an untrusted agent permission.
"""

from autoontology.security.context import authorize, current_actor
from autoontology.security.policy import AccessError


def selection(store, *, all_users=False, conn):
    """Default to the current author; cross-author lists need explicit admin access."""
    actor = current_actor(store, conn=conn)
    if actor is None:
        return None
    authorize(store, "results.read", conn=conn)
    if all_users:
        authorize(store, "identity.admin", conn=conn)
        return None
    state = conn.execute("SELECT workspace_id FROM identity_state WHERE id=1").fetchone()
    return actor.principal_id, state[0]


def require_result(store, principal_id, workspace_id, *, action="results.read", conn):
    actor = current_actor(store, conn=conn)
    if actor is None:
        return None
    workspace = conn.execute("SELECT workspace_id FROM identity_state WHERE id=1").fetchone()[0]
    if workspace_id is not None and workspace_id != workspace:
        raise AccessError("No saved result with this identifier.", "result_missing", 404)
    if principal_id != actor.principal_id:
        if action == "query.run":
            raise AccessError("No conversation owned by this principal.", "result_missing", 404)
        try:
            authorize(store, "identity.admin", conn=conn)
        except AccessError as error:
            if error.status != 403:
                raise
            raise AccessError(
                "No saved result with this identifier.", "result_missing", 404
            ) from None
    return authorize(store, action, conn=conn)
