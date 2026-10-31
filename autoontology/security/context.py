# SPDX-License-Identifier: MIT
"""Authenticated execution context shared by HTTP, native CLI and worker threads.

Binding an actor authenticates it but grants no operation. Every adapter and effect
boundary still calls ``authorize`` with server-owned resource scope. Contexts never
cross workspace databases or fall back to legacy authority when configuration is lost.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from autoontology.security.policy import CAPABILITIES, AccessError, Actor


def _identity(store):
    # Identity's stable digest utility currently lives with Operations. Import lazily
    # so both application services can depend on this execution boundary.
    from autoontology.store.identity import Identity

    return Identity(store)


@dataclass(frozen=True)
class ExecutionIdentity:
    database: Path
    workspace_id: str
    actor: Actor


_IDENTITY: ContextVar[ExecutionIdentity | None] = ContextVar("autoontology_identity", default=None)


@contextmanager
def acting_as(store, actor):
    """Bind only a verified credential; worker threads must explicitly copy context."""
    service = _identity(store)
    with service._read() as conn:
        verified = service._actor(conn, actor)
        workspace = service._state(conn)["workspace_id"]
    token = _IDENTITY.set(ExecutionIdentity(store.path.resolve(), workspace, verified))
    try:
        yield verified
    finally:
        _IDENTITY.reset(token)


def current_actor(store, *, conn=None):
    service = _identity(store)
    if conn is None:
        with service._read() as read:
            return current_actor(store, conn=read)
    execution = _IDENTITY.get()
    row = conn.execute("SELECT workspace_id FROM identity_state WHERE id=1").fetchone()
    if execution is not None:
        if (
            execution.database != store.path.resolve()
            or row is None
            or execution.workspace_id != row[0]
        ):
            raise AccessError(
                "An authenticated execution cannot change or lose its workspace.",
                "execution_workspace_mismatch",
                403,
            )
        return service._actor(conn, execution.actor)
    if row is not None:
        raise AccessError(
            "Sign in with an individual credential before this operation.",
            "authentication_required",
            401,
        )
    if conn.execute("SELECT 1 FROM identity_principals LIMIT 1").fetchone():
        raise AccessError(
            "Identity configuration is missing; local-owner recovery is required.",
            "identity_configuration_missing",
            503,
        )
    return None


def authorize(store, action, *, source_ids=None, domains=None, expected_policy=None, conn=None):
    """Use the current principal and live policy; legacy local stores remain explicit."""
    service = _identity(store)
    if conn is None:
        with service._read() as read:
            return authorize(
                store,
                action,
                source_ids=source_ids,
                domains=domains,
                expected_policy=expected_policy,
                conn=read,
            )
    if not conn.in_transaction:
        raise RuntimeError("authorization at an effect boundary needs an active transaction")
    actor = current_actor(store, conn=conn)
    if actor is None:
        return None
    if source_ids is not None and domains is not None:
        raise ValueError("derive either source or domain scope, never both")
    if source_ids is not None:
        domains = service.source_domains(actor, source_ids, action=action, conn=conn)
    return service.require(
        actor, action, domains=domains, expected_policy=expected_policy, conn=conn
    )


def requires(action):
    """Declare an entry gate; consequential methods also recheck inside their write."""
    if action not in CAPABILITIES:
        raise ValueError("undefined application capability")

    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            target = args[0] if args else kwargs["store"]
            authorize(getattr(target, "store", target), action)
            return function(*args, **kwargs)

        run.required_capability = action
        return run

    return decorate


def authorize_any(store, action, *, conn=None):
    """Enter a resource-selecting read; this alone never authorizes whole data."""
    service = _identity(store)
    if conn is None:
        with service._read() as read:
            return authorize_any(store, action, conn=read)
    actor = current_actor(store, conn=conn)
    return service.require_any(actor, action, conn=conn) if actor is not None else None


def audit_effect(store, decision, target, detail, *, conn):
    """Append a controlled effect receipt in the same transaction as its mutation."""
    if decision is not None:
        _identity(store)._event(
            conn,
            decision.actor,
            decision.action,
            str(target),
            {"policy_version": decision.policy_version, **detail},
        )


async def await_authorized(store, action, call, *, on_revoked=None, interval=0.1):
    """Check while waiting, cancel the awaited provider/read, and retain its auth error.

    No cancellation scope spans a generator yield. The caller supplies cleanup for
    its own producer receipt; monitoring never grants access to another request.
    Synchronous worker threads remain bounded by their own transport deadlines.
    """
    import anyio

    authorize(store, action)
    denied = None
    failure = None
    value = None

    async def monitor(scope):
        nonlocal denied
        while True:
            await anyio.sleep(interval)
            try:
                authorize(store, action)
            except AccessError as error:
                denied = error
                if on_revoked:
                    on_revoked(error)
                scope.cancel()
                return

    async with anyio.create_task_group() as group:
        group.start_soon(monitor, group.cancel_scope)
        try:
            value = await call()
        except BaseException as error:
            failure = error
        finally:
            group.cancel_scope.cancel()
    if denied is not None:
        raise denied
    if failure is not None:
        raise failure
    authorize(store, action)
    return value
