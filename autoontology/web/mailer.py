# SPDX-License-Identifier: MIT
"""Outgoing mail for login links (stdlib :mod:`smtplib`).

Configured by ``AUTOONTOLOGY_SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS``; without a host
(:attr:`Mailer.enabled` False) nothing is sent and the UI hides "email me a link" -- an
admin copies the link from the CLI (``ao user link``) or the users page instead.

The SMTP password and the links themselves (they carry a login token) are never logged:
a failure is logged as the exception type and the recipient's domain only.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from autoontology.config import Settings

log = logging.getLogger("autoontology.mail")
SMTP_TIMEOUT_S = 15.0

#: ``(subject, body)`` per kind and language; ``{link}``, ``{minutes}``/``{days}``,
#: ``{name}`` are filled in.
TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "magic": {
        "en": (
            "Your AutoOntology sign-in link",
            "Hello{name},\n\n"
            "Use this link to sign in to AutoOntology. It works once and expires in "
            "{minutes} minutes:\n\n{link}\n\n"
            "If you did not ask for it, ignore this message: nobody can sign in without "
            "the link.\n",
        ),
        "ko": (
            "AutoOntology 로그인 링크",
            "안녕하세요{name},\n\n"
            "아래 링크로 AutoOntology에 로그인하세요. 한 번만 쓸 수 있고 {minutes}분 뒤에 "
            "만료됩니다.\n\n{link}\n\n"
            "요청하지 않으셨다면 이 메일을 무시하세요. 링크 없이는 아무도 로그인할 수 "
            "없습니다.\n",
        ),
    },
    "invite": {
        "en": (
            "You are invited to AutoOntology",
            "Hello{name},\n\n"
            "An account was created for you on AutoOntology. Open this link to set your "
            "password and sign in. It works once and expires in {days} days:\n\n{link}\n",
        ),
        "ko": (
            "AutoOntology 초대",
            "안녕하세요{name},\n\n"
            "AutoOntology 계정이 만들어졌습니다. 아래 링크에서 비밀번호를 정하고 "
            "로그인하세요. 한 번만 쓸 수 있고 {days}일 뒤에 만료됩니다.\n\n{link}\n",
        ),
    },
    "reset": {
        "en": (
            "Reset your AutoOntology password",
            "Hello{name},\n\n"
            "Open this link to choose a new AutoOntology password. It works once and "
            "expires in {minutes} minutes:\n\n{link}\n",
        ),
        "ko": (
            "AutoOntology 비밀번호 재설정",
            "안녕하세요{name},\n\n"
            "아래 링크에서 새 AutoOntology 비밀번호를 정하세요. 한 번만 쓸 수 있고 "
            "{minutes}분 뒤에 만료됩니다.\n\n{link}\n",
        ),
    },
}


def render(kind: str, lang: str, *, link: str, name: str = "") -> tuple[str, str]:
    """``(subject, body)`` of a *kind* message in *lang* (``ko``/``en``, else English)."""
    from autoontology.store.accounts import TOKEN_TTL

    subject, body = TEMPLATES[kind].get(lang) or TEMPLATES[kind]["en"]
    ttl = TOKEN_TTL[kind]
    filled = body.format(
        link=link,
        name=f" {name}" if name else "",
        minutes=int(ttl.total_seconds() // 60),
        days=ttl.days,
    )
    return subject, filled


@dataclass
class Mailer:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = ""
    starttls: bool = True
    timeout: float = SMTP_TIMEOUT_S

    def __repr__(self) -> str:  # never the password
        return f"Mailer(host={self.host!r}, port={self.port}, enabled={self.enabled})"

    @classmethod
    def from_settings(cls, settings: Settings) -> Mailer:
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            sender=settings.smtp_from or settings.smtp_user,
            starttls=settings.smtp_starttls,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender)

    def send(self, to: str, subject: str, body: str) -> bool:
        """Send a plain-text message; True when the server accepted it. Never raises (the
        caller's answer must not depend on the mail server)."""
        if not self.enabled:
            return False
        message = EmailMessage()
        message["From"] = formataddr(("AutoOntology", self.sender))
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2] or None)
        message.set_content(body)
        try:
            if self.port == 465:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
                )
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with client:
                if self.starttls and self.port != 465:
                    client.starttls(context=ssl.create_default_context())
                if self.user:
                    client.login(self.user, self.password)
                client.send_message(message)
        except Exception as exc:  # noqa: BLE001 - logged without the message or credentials
            log.warning(
                "mail to a recipient at %s failed: %s",
                to.rpartition("@")[2] or "?",
                type(exc).__name__,
            )
            return False
        return True

    def send_link(self, kind: str, to: str, link: str, *, lang: str = "en", name: str = "") -> bool:
        subject, body = render(kind, lang, link=link, name=name)
        return self.send(to, subject, body)
