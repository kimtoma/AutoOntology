# SPDX-License-Identifier: MIT
"""A stored API key is bound to its normalized HTTP origin, never raw log text."""

from urllib.parse import urlsplit

from autoontology.config import PLACEHOLDER_KEY_MARKERS
from autoontology.security.policy import AccessError


def url_origin(url):
    try:
        parts = urlsplit((url or "").strip())
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            return ""
        port = parts.port or {"http": 80, "https": 443}[scheme]
        host = parts.hostname.lower()
        return f"{scheme}://{'[' + host + ']' if ':' in host else host}:{port}"
    except (ValueError, TypeError):
        return ""


def guard_saved_key(old_url, old_key, new_url, *, key_replaced=False):
    if (
        old_key
        and not any(marker in old_key for marker in PLACEHOLDER_KEY_MARKERS)
        and url_origin(old_url) != url_origin(new_url)
        and not key_replaced
    ):
        raise AccessError(
            "The saved API key belongs to its previous origin. Provide a private key file for the new origin or explicitly clear the saved key.",
            "key_required",
            400,
        )
