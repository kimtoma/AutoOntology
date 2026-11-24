# SPDX-License-Identifier: MIT
"""Sign-in: who is asking (:func:`current_user`) and the ``/api/auth`` routes.

Modes (``AUTOONTOLOGY_AUTH``)
-----------------------------
* ``off`` (default): no accounts. Every caller past the host guard is a synthetic
  :class:`Actor` with admin rights named after the tailnet login or ``"local"`` -- the
  behaviour from before accounts, unchanged.
* ``accounts``: a caller is the user of its ``ao_session`` cookie, else the user whose
  email is the Tailscale login (``tailscale serve`` identity header, on a configured
  tailnet hostname), else nobody (``current_user`` is None). A disabled user is nobody.

Session cookie ``ao_session``: a random id (the store keeps its SHA-256), ``HttpOnly``,
``SameSite=Strict``, ``Path=/``, ``Max-Age`` = ``AUTOONTOLOGY_SESSION_HOURS``; ``Secure``
when the request does not come to 127.0.0.1/localhost, when ``AUTOONTOLOGY_PUBLIC_URL`` is
https or when the proxy says the request was https (``X-Forwarded-Proto``). A login always
issues a new id (and ends the session it replaces).

The routes work without a session; every non-GET one still passes the service's host and
cross-origin checks (``web/api.py``), which is what stops login CSRF.

``POST /api/auth/login``
    ``{email, password}`` -> 200 the :func:`me` body + cookie; 401 ``invalid_credentials``
    for every failure (unknown email, wrong password, no password, disabled); 429
    ``too_many_attempts`` (``Retry-After``) after 5 failures a minute per client IP or per
    email; failures answer after an exponential delay. Audit ``login_ok`` /
    ``login_failed``.
``POST /api/auth/logout``
    200 ``{"ok": true}``, the session ends and the cookie is cleared.
``GET /api/auth/me``
    200 ``{auth, authenticated, user, is_admin, memberships, magic_link_enabled, actor,
    session_expires_at}`` (``user`` null when signed out).
``POST /api/auth/magic-link``
    ``{email}`` -> always 202 ``{"ok": true}``; a link is mailed only when the user exists,
    is active, SMTP is configured and the per-IP/per-email limit allows it.
``GET /auth/magic/{token}``
    a valid magic token -> session cookie + 303 ``/ui/``; anything else -> 303
    ``/ui/login?error=link``.
``POST /api/auth/accept-invite``
    ``{token, password}`` (an invite or reset token) -> sets the password, uses the token,
    ends the user's other sessions, signs in: 200 the :func:`me` body + cookie; 400
    ``invalid_token`` / ``weak_password`` (the token stays usable).
``POST /api/auth/password``
    ``{current, new}`` (signed in; ``current`` is ignored for a user without a password)
    -> 200 ``{"ok": true, "revoked_sessions": n}``, every other session ends; 401
    ``login_required``, 403 ``wrong_password``, 400 ``weak_password``.

With ``AUTOONTOLOGY_AUTH=off`` the sign-in routes answer 409 ``accounts_off`` (``me`` and
``logout`` still answer, ``/auth/magic/...`` redirects to ``/ui/``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response

from autoontology.config import LOCAL_HOSTS, host_name
from autoontology.store.accounts import PASSWORD_MAX, AccountError, Accounts, User
from autoontology.web.mailer import Mailer

if TYPE_CHECKING:  # pragma: no cover - api imports this module
    from autoontology.web.api import Api

log = logging.getLogger("autoontology.auth")

COOKIE = "ao_session"
#: Failed logins allowed per client IP and per email within :data:`LOGIN_WINDOW_S`.
LOGIN_LIMIT = 5
LOGIN_WINDOW_S = 60.0
#: A failed login answers after ``FAIL_DELAY_S * 2**(failures - 1)`` seconds, at most
#: :data:`FAIL_DELAY_MAX_S`.
FAIL_DELAY_S = 0.25
FAIL_DELAY_MAX_S = 4.0
#: Magic-link requests allowed per client IP and per email within the window.
MAGIC_LIMIT = 5
EMAIL_FIELD_MAX = 320
#: Where the browser goes after a magic link (and after a bad one).
UI_HOME = "/ui/"
UI_LOGIN_ERROR = "/ui/login?error=link"
#: The UI page an invitation / password-reset link opens; the token is in the fragment
#: (never sent to a server, never in a Referer): ``/ui/invite#token=...&purpose=invite``.
UI_INVITE = "/ui/invite"
MAGIC_PATH_RE = re.compile(r"(/auth/magic/)[^\s?\"]+")


# --- who asks ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """The caller. ``user`` None = the synthetic actor of ``AUTOONTOLOGY_AUTH=off`` (or the
    CLI), which has admin rights; ``name`` is the user's email, the tailnet login or
    ``"local"``; ``via`` is how it was recognised (``session``, ``tailscale``, ``off``)."""

    name: str
    is_admin: bool
    user: User | None = None
    via: str = "off"
    session_expires_at: str | None = None

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None


class RateLimiter:
    """Events per key in a sliding window (in memory, per process, thread-safe)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.setdefault(key, deque())
        while events and events[0] <= now - self.window_s:
            events.popleft()
        return events

    def count(self, key: str) -> int:
        with self._lock:
            return len(self._prune(key, time.monotonic()))

    def retry_after(self, key: str) -> int:
        """Seconds until *key* is under the limit again (0 = it is)."""
        with self._lock:
            now = time.monotonic()
            events = self._prune(key, now)
            if len(events) < self.limit:
                return 0
            return max(1, int(events[len(events) - self.limit] + self.window_s - now) + 1)

    def hit(self, key: str) -> int:
        """Record an event; the count in the window."""
        with self._lock:
            events = self._prune(key, time.monotonic())
            events.append(time.monotonic())
            if len(self._events) > 10_000:  # forget idle keys
                for stale in [k for k, v in self._events.items() if not v][:5000]:
                    del self._events[stale]
            return len(events)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


def client_ip(request: Request) -> str:
    """The caller's IP. Behind the local gateway (the peer is loopback) the address the
    proxy reports (``CF-Connecting-IP``, else the first ``X-Forwarded-For``)."""
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1", "localhost", "testclient", ""):
        forwarded = (request.headers.get("cf-connecting-ip") or "").strip()
        if not forwarded:
            forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded and len(forwarded) <= 64:
            return forwarded
    return peer or "-"


def _lang(request: Request) -> str:
    accept = (request.headers.get("accept-language") or "").lower()
    return "ko" if accept.startswith("ko") else "en"


# --- providers --------------------------------------------------------------------------


class PasswordProvider:
    """Email + password (PBKDF2, see :mod:`autoontology.store.accounts`)."""

    name = "password"

    def authenticate(self, accounts: Accounts, email: str, password: str) -> User | None:
        return accounts.verify_password(email, password)


class MagicLinkProvider:
    """Single-use links: ``magic`` signs in (``/auth/magic/<token>``); ``invite`` and
    ``reset`` open the UI's set-a-password page (``/ui/invite#token=...``)."""

    name = "magic_link"

    def issue(self, accounts: Accounts, user: User, purpose: str) -> tuple[str, str]:
        """``(raw token, expires_at)``."""
        return accounts.issue_token(user.id, purpose)

    @staticmethod
    def link(base_url: str, raw: str, purpose: str) -> str:
        base = base_url.rstrip("/")
        if purpose == "magic":
            return f"{base}/auth/magic/{raw}"
        return f"{base}{UI_INVITE}#token={raw}&purpose={purpose}"

    def redeem(self, accounts: Accounts, raw: str) -> User | None:
        return accounts.consume_token(raw, ("magic",))


class OIDCProvider:
    """Single sign-on (OpenID Connect) -- reserved, not implemented.

    The planned flow: ``GET /auth/oidc/start`` redirects to the issuer's authorization
    endpoint (``AUTOONTOLOGY_OIDC_ISSUER`` discovery document, ``AUTOONTOLOGY_OIDC_CLIENT_ID``,
    ``AUTOONTOLOGY_OIDC_SCOPES`` default ``openid email profile``) with ``state`` and a PKCE
    challenge kept in a short-lived signed cookie; ``GET /auth/oidc/callback`` exchanges the
    code (``AUTOONTOLOGY_OIDC_CLIENT_SECRET``), verifies the ID token (issuer, audience,
    nonce, signature from the JWKS), takes its verified ``email`` and signs in the active
    user with that email exactly like :class:`PasswordProvider` does (same session cookie,
    same audit entry with ``method: oidc``). No automatic account creation: an admin adds
    the user first, as for passwords.
    """

    name = "oidc"

    def authorization_url(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError("single sign-on (OIDC) is not available yet")

    def callback(self, *args: Any, **kwargs: Any) -> User | None:
        raise NotImplementedError("single sign-on (OIDC) is not available yet")


# --- service state ----------------------------------------------------------------------


@dataclass
class AuthService:
    """Sign-in state shared by the routes: the store's accounts, the rate limiters, the
    mailer and the providers."""

    api: Api
    mailer: Mailer = field(default_factory=Mailer)
    login_ip: RateLimiter = field(default_factory=lambda: RateLimiter(LOGIN_LIMIT, LOGIN_WINDOW_S))
    login_email: RateLimiter = field(
        default_factory=lambda: RateLimiter(LOGIN_LIMIT, LOGIN_WINDOW_S)
    )
    magic_ip: RateLimiter = field(default_factory=lambda: RateLimiter(MAGIC_LIMIT, LOGIN_WINDOW_S))
    magic_email: RateLimiter = field(
        default_factory=lambda: RateLimiter(MAGIC_LIMIT, LOGIN_WINDOW_S)
    )
    password: PasswordProvider = field(default_factory=PasswordProvider)
    magic: MagicLinkProvider = field(default_factory=MagicLinkProvider)

    @property
    def accounts(self) -> Accounts:
        return Accounts(self.api.store)

    @property
    def enabled(self) -> bool:
        return self.api.settings.accounts_enabled

    @property
    def magic_link_enabled(self) -> bool:
        return self.enabled and self.mailer.enabled

    def base_url(self, request: Request | None = None) -> str:
        """Where links point: ``AUTOONTOLOGY_PUBLIC_URL``, else the request's base URL,
        else the local service URL."""
        if self.api.settings.public_url:
            return self.api.settings.public_url
        if request is not None:
            return str(request.base_url).rstrip("/")
        return self.api.settings.service_url

    def secure_cookie(self, request: Request) -> bool:
        if self.api.settings.public_url.startswith("https://"):
            return True
        if (request.headers.get("x-forwarded-proto") or "").strip().lower() == "https":
            return True
        return host_name(request.headers.get("host") or "") not in LOCAL_HOSTS

    def set_cookie(self, response: Response, request: Request, raw: str) -> None:
        response.set_cookie(
            COOKIE,
            raw,
            max_age=int(self.api.settings.session_hours * 3600),
            path="/",
            httponly=True,
            samesite="strict",
            secure=self.secure_cookie(request),
        )

    def clear_cookie(self, response: Response, request: Request) -> None:
        response.delete_cookie(
            COOKIE,
            path="/",
            httponly=True,
            samesite="strict",
            secure=self.secure_cookie(request),
        )

    def start_session(self, request: Request, user: User) -> str:
        """A new session for *user*; the session the request's cookie names ends."""
        accounts = self.accounts
        old = request.cookies.get(COOKIE)
        if old:
            accounts.revoke_session(old)
        raw, _ = accounts.create_session(
            user.id,
            hours=self.api.settings.session_hours,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent") or "",
        )
        return raw

    def resolve(self, request: Request) -> Actor | None:
        """The caller of *request* (see the module docstring), or None."""
        settings = self.api.settings
        if not settings.accounts_enabled:
            name = self.api.decided_by(request)
            return Actor(name=name, is_admin=True, via="off")
        found = self.accounts.lookup_session(request.cookies.get(COOKIE))
        if found is not None:
            session, user = found
            return Actor(
                name=user.email,
                is_admin=user.is_admin,
                user=user,
                via="session",
                session_expires_at=session.expires_at,
            )
        login = self.api.decided_by(request)
        if login != "local":  # a tailnet login on a configured tailnet hostname
            user = self.accounts.by_email(login)
            if user is not None and user.active:
                return Actor(name=user.email, is_admin=user.is_admin, user=user, via="tailscale")
        return None

    def me(self, actor: Actor | None) -> dict[str, Any]:
        settings = self.api.settings
        body: dict[str, Any] = {
            "auth": settings.auth_mode,
            "authenticated": actor is not None,
            "actor": actor.name if actor else None,
            "via": actor.via if actor else None,
            "user": actor.user.to_dict() if actor and actor.user else None,
            "is_admin": bool(actor and actor.is_admin),
            "memberships": [],
            "magic_link_enabled": self.magic_link_enabled,
            "session_expires_at": actor.session_expires_at if actor else None,
        }
        if actor is not None and actor.user is not None:
            projects = {p.id: p for p in self.api.store.list_projects(include_archived=True)}
            body["memberships"] = [
                {
                    "project_id": pid,
                    "slug": projects[pid].slug,
                    "name": projects[pid].name,
                    "archived": projects[pid].archived,
                    "role": role,
                }
                for pid, role in self.accounts.projects_of(actor.user.id)
                if pid in projects
            ]
        return body


def service(request: Request) -> AuthService:
    return request.app.state.auth


def current_user(request: Request) -> Actor | None:
    """FastAPI dependency: the caller (cached on the request), None when signed out in
    ``accounts`` mode."""
    if not hasattr(request.state, "actor"):
        request.state.actor = service(request).resolve(request)
    return request.state.actor


def require_user(request: Request) -> Actor:
    """FastAPI dependency: the caller, 401 ``login_required`` when there is none."""
    from autoontology.web.api import ApiError

    actor = current_user(request)
    if actor is None:
        raise ApiError(401, "login_required", "sign in first")
    return actor


# --- routes -----------------------------------------------------------------------------


def _str_field(body: dict[str, Any], name: str, limit: int) -> str:
    from autoontology.web.api import ApiError

    value = body.get(name)
    if not isinstance(value, str) or len(value) > limit:
        raise ApiError(400, "invalid_request", f"{name}: a string of at most {limit} characters")
    return value


def _accounts_on(auth: AuthService) -> None:
    from autoontology.web.api import ApiError

    if not auth.enabled:
        raise ApiError(409, "accounts_off", "accounts are off (AUTOONTOLOGY_AUTH=off)")


def _too_many(retry: int) -> JSONResponse:
    return JSONResponse(
        {"error": "too_many_attempts", "detail": "too many attempts: try again later"},
        status_code=429,
        headers={"Retry-After": str(retry)},
    )


def _invalid_credentials() -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_credentials", "detail": "wrong email or password"}, status_code=401
    )


def _fail_delay(failures: int) -> float:
    return min(FAIL_DELAY_S * 2 ** max(0, failures - 1), FAIL_DELAY_MAX_S)


def build_router(api: Api) -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/login")
    async def login(body: dict[str, Any], request: Request) -> Response:
        auth = service(request)
        _accounts_on(auth)
        email = _str_field(body, "email", EMAIL_FIELD_MAX).strip().lower()
        password = _str_field(body, "password", PASSWORD_MAX)
        ip = client_ip(request)
        ip_key, email_key = f"ip:{ip}", f"email:{email}"
        retry = max(auth.login_ip.retry_after(ip_key), auth.login_email.retry_after(email_key))
        if retry:
            return _too_many(retry)
        user = await run_in_threadpool(auth.password.authenticate, auth.accounts, email, password)
        if user is None:
            failures = max(auth.login_ip.hit(ip_key), auth.login_email.hit(email_key))
            auth.accounts.record("login_failed", target=email[:200], ip=ip)
            await asyncio.sleep(_fail_delay(failures))
            return _invalid_credentials()
        auth.login_email.reset(email_key)
        raw = auth.start_session(request, user)
        accounts = auth.accounts
        accounts.record("login_ok", user_id=user.id, detail={"method": "password"}, ip=ip)
        accounts.purge_expired()
        actor = Actor(name=user.email, is_admin=user.is_admin, user=user, via="session")
        found = accounts.lookup_session(raw)
        if found is not None:
            actor = Actor(
                name=user.email,
                is_admin=user.is_admin,
                user=found[1],
                via="session",
                session_expires_at=found[0].expires_at,
            )
        response = JSONResponse(auth.me(actor))
        auth.set_cookie(response, request, raw)
        return response

    @router.post("/api/auth/logout")
    def logout(request: Request) -> Response:
        auth = service(request)
        raw = request.cookies.get(COOKIE)
        if raw and auth.enabled:
            found = auth.accounts.lookup_session(raw)
            if auth.accounts.revoke_session(raw) and found is not None:
                auth.accounts.record("logout", user_id=found[1].id, ip=client_ip(request))
        response = JSONResponse({"ok": True})
        auth.clear_cookie(response, request)
        return response

    @router.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        auth = service(request)
        return auth.me(current_user(request))

    @router.post("/api/auth/magic-link", status_code=202)
    def magic_link(
        body: dict[str, Any], request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        auth = service(request)
        _accounts_on(auth)
        email = _str_field(body, "email", EMAIL_FIELD_MAX).strip().lower()
        answer = {
            "ok": True,
            "detail": "if the address has an account, a sign-in link is on its way",
        }
        if not auth.mailer.enabled:
            return answer
        ip = client_ip(request)
        ip_key, email_key = f"ip:{ip}", f"email:{email}"
        over = auth.magic_ip.hit(ip_key) > MAGIC_LIMIT
        over = auth.magic_email.hit(email_key) > MAGIC_LIMIT or over
        if over:
            return answer
        user = auth.accounts.by_email(email)
        if user is None or not user.active:
            return answer
        raw, _ = auth.magic.issue(auth.accounts, user, "magic")
        link = auth.magic.link(auth.base_url(request), raw, "magic")
        auth.accounts.record("magic_link_sent", user_id=user.id, ip=ip)
        background.add_task(
            auth.mailer.send_link, "magic", user.email, link, lang=_lang(request), name=user.name
        )
        return answer

    @router.get("/auth/magic/{token}", include_in_schema=False)
    def redeem_magic(token: str, request: Request) -> Response:
        auth = service(request)
        if not auth.enabled:
            return RedirectResponse(UI_HOME, status_code=303)
        user = auth.magic.redeem(auth.accounts, token)
        if user is None:
            return RedirectResponse(UI_LOGIN_ERROR, status_code=303)
        raw = auth.start_session(request, user)
        auth.accounts.record(
            "login_ok", user_id=user.id, detail={"method": "magic_link"}, ip=client_ip(request)
        )
        response = RedirectResponse(UI_HOME, status_code=303)
        response.headers["Referrer-Policy"] = "no-referrer"
        auth.set_cookie(response, request, raw)
        return response

    @router.post("/api/auth/accept-invite")
    def accept_invite(body: dict[str, Any], request: Request) -> Response:
        from autoontology.web.api import ApiError

        auth = service(request)
        _accounts_on(auth)
        token = _str_field(body, "token", 200)
        password = _str_field(body, "password", PASSWORD_MAX)
        accounts = auth.accounts
        try:
            user = accounts.consume_token(token, ("invite", "reset"), new_password=password)
        except AccountError as exc:
            raise ApiError(400, exc.code, str(exc)) from exc
        if user is None:
            raise ApiError(400, "invalid_token", "this link is invalid, used or expired")
        accounts.revoke_all_for_user(user.id)
        raw = auth.start_session(request, user)
        ip = client_ip(request)
        accounts.record("password_set", user_id=user.id, detail={"method": "link"}, ip=ip)
        accounts.record("login_ok", user_id=user.id, detail={"method": "invite"}, ip=ip)
        found = accounts.lookup_session(raw)
        actor = Actor(
            name=user.email,
            is_admin=user.is_admin,
            user=user,
            via="session",
            session_expires_at=found[0].expires_at if found else None,
        )
        response = JSONResponse(auth.me(actor))
        auth.set_cookie(response, request, raw)
        return response

    @router.post("/api/auth/password")
    def change_password(body: dict[str, Any], request: Request) -> dict[str, Any]:
        from autoontology.web.api import ApiError

        auth = service(request)
        _accounts_on(auth)
        actor = require_user(request)
        assert actor.user is not None  # accounts mode: a signed-in actor is a user
        current = _str_field(body, "current", PASSWORD_MAX) if "current" in body else ""
        new = _str_field(body, "new", PASSWORD_MAX)
        accounts = auth.accounts
        email_key = f"email:{actor.user.email}"
        retry = auth.login_email.retry_after(email_key)
        if retry:
            return _too_many(retry)  # type: ignore[return-value]
        if actor.user.has_password and accounts.verify_password(actor.user.email, current) is None:
            auth.login_email.hit(email_key)
            accounts.record("password_change_failed", user_id=actor.user.id, ip=client_ip(request))
            raise ApiError(403, "wrong_password", "the current password is wrong")
        try:
            accounts.set_password(actor.user.id, new)
        except AccountError as exc:
            raise ApiError(400, exc.code, str(exc)) from exc
        revoked = accounts.revoke_all_for_user(
            actor.user.id,
            except_raw=request.cookies.get(COOKIE) if actor.via == "session" else None,
        )
        accounts.record(
            "password_change",
            user_id=actor.user.id,
            detail={"revoked_sessions": revoked},
            ip=client_ip(request),
        )
        return {"ok": True, "revoked_sessions": revoked}

    return router


# --- logging ----------------------------------------------------------------------------


class RedactMagicLinks(logging.Filter):
    """Drop the token from ``/auth/magic/<token>`` in uvicorn's access log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                MAGIC_PATH_RE.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str) and "/auth/magic/" in record.msg:
            record.msg = MAGIC_PATH_RE.sub(r"\1***", record.msg)
        return True


_FILTER = RedactMagicLinks()


def install(app: Any, api: Api) -> AuthService:
    """Mount the routes and put the :class:`AuthService` on ``app.state.auth``."""
    auth = AuthService(api, mailer=Mailer.from_settings(api.settings))
    app.state.auth = auth
    app.include_router(build_router(api))
    access = logging.getLogger("uvicorn.access")
    if _FILTER not in access.filters:
        access.addFilter(_FILTER)
    if api.settings.accounts_enabled:
        from autoontology.store.accounts import _dummy_hash

        _dummy_hash()  # the first unknown-email login must cost what the others do
    return auth
