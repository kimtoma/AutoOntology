# SPDX-License-Identifier: MIT
"""CLI message catalogue: ``msg(key, **kw)`` in English or Korean.

The language comes from ``AUTOONTOLOGY_LANG`` (shell, then ``.env``), else from the
locale variables ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` (``ko*`` means Korean), else
English. A key missing from the Korean catalogue falls back to the English text.
Commands, error codes and status words (OK/WARN/FAIL/SKIP) are never translated.
"""

from __future__ import annotations

import os
import re
import unicodedata

from autoontology.i18n_catalog import en as _en
from autoontology.i18n_catalog import ko as _ko

LANGS = ("en", "ko")
ENV_VAR = "AUTOONTOLOGY_LANG"
CATALOGS: dict[str, dict[str, str]] = {"en": _en.MESSAGES, "ko": _ko.MESSAGES}


def _normalize(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    return "ko" if value.startswith("ko") else "en"


def current_lang() -> str:
    """``en`` or ``ko`` (see the module docstring for the order)."""
    explicit = _normalize(os.environ.get(ENV_VAR))
    if explicit:
        return explicit
    try:
        from autoontology.config import dotenv_layer

        explicit = _normalize(dotenv_layer().get(ENV_VAR))
    except Exception:  # noqa: BLE001 - a broken .env must never break a message
        explicit = None
    if explicit:
        return explicit
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "").strip()
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    return "en"


def msg(key: str, lang: str | None = None, /, **kw: object) -> str:
    """The message *key* in *lang* (default: :func:`current_lang`), formatted with *kw*.

    Falls back to English when the key is missing in that language; an unknown key
    returns the key itself so a typo is visible rather than fatal."""
    lang = lang or current_lang()
    text = CATALOGS.get(lang, {}).get(key)
    if text is None:
        text = CATALOGS["en"].get(key, key)
    return text.format(**kw) if kw else text


def other_lang_hint(lang: str | None = None) -> str:
    """One line naming the other language's switch (printed at the top of ``init``)."""
    return msg("lang.hint", lang)


# --- display width (Korean is double-width in a terminal) -------------------------


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def ljust(text: str, size: int) -> str:
    text = str(text)
    return text + " " * max(0, size - width(text))


# --- notices from shared modules (their text also reaches the web API) -------------

_NOTICES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^removed source (?P<name>\S+)$"), "notice.removed_source"),
    (
        re.compile(r"^removed documents source (?P<name>\S+) \(.*\)$"),
        "notice.removed_documents_source",
    ),
    (re.compile(r"^dropped database (?P<name>\S+)$"), "notice.dropped_database"),
    (re.compile(r"^ingested graph of (?P<name>\S+) deleted$"), "notice.graph_deleted"),
    (
        re.compile(r"^no upstream connection (?P<name>\S+) \(already gone\)$"),
        "notice.no_connection",
    ),
    (
        re.compile(
            r"^deleted upstream connection (?P<name>\S+); waiting for the service to "
            r"delete its ingested graph \.\.\.$"
        ),
        "notice.connection_deleted",
    ),
    (
        re.compile(r"^--drop-db ignored: only files sources own a local database$"),
        "notice.drop_db_ignored",
    ),
)


def notice(message: str, lang: str | None = None) -> str:
    """Translate a known English notice of ``structured.remove`` (else return it as is)."""
    lang = lang or current_lang()
    if lang == "en":
        return message
    for pattern, key in _NOTICES:
        m = pattern.match(message)
        if m:
            return msg(key, lang, **m.groupdict())
    return message


# --- doctor remedies (the Check objects, and the web API, keep the English text) ----


#: A CLI provider that is not installed (``base.CliProvider.status``).
_INSTALL_CLI = re.compile(r"^install the (?P<bin>[\w-]+) CLI and log in \((?P<login>.*)\)$")


def remedy(text: str, lang: str | None = None) -> str:
    """Translate a doctor remedy: each ``; ``-separated part that is a known English
    remedy description becomes the catalogue text; commands and unknown parts stay."""
    lang = lang or current_lang()
    if lang == "en" or not text:
        return text
    reverse = {v: k for k, v in CATALOGS["en"].items() if k.startswith("remedy.")}
    if text in reverse:
        return msg(reverse[text], lang)
    m = _INSTALL_CLI.match(text)
    if m:
        return msg("remedy.install_cli", lang, bin=m["bin"], login=remedy(m["login"], lang))
    return "; ".join(
        msg(reverse[part], lang) if part in reverse else part for part in text.split("; ")
    )
