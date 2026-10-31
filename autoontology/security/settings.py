# SPDX-License-Identifier: MIT
"""Shared native/UI settings-file boundary; values never enter effect audit metadata."""

from autoontology.security.context import authorize
from autoontology.security.policy import AccessError


def save_env(store, env_file, path, *, expected_policy=None, key_replaced=False):
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
        import os

        from autoontology.config import env_origin
        from autoontology.security.endpoints import guard_saved_key

        url_var, key_var = "AUTOONTOLOGY_OPENAI_BASE_URL", "AUTOONTOLOGY_OPENAI_API_KEY"
        if key_replaced and env_origin(key_var, path) == "shell":
            raise AccessError(
                "The API key is set in the shell. Change or unset it there before rebinding its origin.",
                "shell_override",
                400,
            )
        if any(key in (url_var, key_var) and env_origin(key, path) == "shell" for key in written):
            raise AccessError(
                "A sensitive setting is owned by the shell. Change or unset it there first.",
                "shell_override",
                400,
            )
        guard_saved_key(
            env_file.baseline.get(url_var, ""),
            env_file.baseline.get(key_var, ""),
            env_file.get(url_var) or "",
            key_replaced=key_replaced or key_var in written,
        )
        effective_url = (
            os.environ.get(url_var, "")
            if env_origin(url_var, path) == "shell"
            else env_file.baseline.get(url_var, "")
        )
        effective_key = (
            os.environ.get(key_var, "")
            if env_origin(key_var, path) == "shell"
            else env_file.baseline.get(key_var, "")
        )
        candidate_url = (
            effective_url if env_origin(url_var, path) == "shell" else env_file.get(url_var) or ""
        )
        guard_saved_key(
            effective_url,
            effective_key,
            candidate_url,
            key_replaced=key_replaced or key_var in written,
        )

        audit_effect(
            store,
            decision,
            "model-selection",
            {"operation": "save_settings", "keys": written},
            conn=conn,
        )
        return env_file.write(path) if written else None
