# SPDX-License-Identifier: MIT
"""Shared native/UI settings-file boundary; values never enter effect audit metadata."""

from autoontology.security.context import authorize
from autoontology.security.policy import AccessError


def save_env(store, env_file, path, *, expected_policy=None):
    """Serialize participating workspace writers and reject stale preparation.

    The file uses EnvFile's private atomic replacement and scrubbed backup. This
    boundary does not claim a crash-atomic transaction across SQLite and the file;
    durable approval/effect recovery remains a separate requirement.
    """
    from autoontology.security.context import audit_effect

    with store._immediate() as conn:
        decision = authorize(store, "settings.manage", expected_policy=expected_policy, conn=conn)
        if path.exists() != env_file.existed or (
            env_file.existed and path.read_text(encoding="utf-8") != env_file.original
        ):
            raise AccessError(
                "Settings changed after preparation. Refresh before saving.",
                "settings_changed",
                409,
            )
        written = [key for key, _old, _new in env_file.changes()]
        audit_effect(
            store,
            decision,
            "model-selection",
            {"operation": "save_settings", "keys": written},
            conn=conn,
        )
        return env_file.write(path) if written else None
