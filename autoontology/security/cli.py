# SPDX-License-Identifier: MIT
"""Native CLI authentication and the same execution authority as HTTP operations."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import time
from contextvars import ContextVar
from pathlib import Path

import typer
from typer.core import TyperGroup

from autoontology.security.context import acting_as, authorize, authorize_any
from autoontology.security.credential_file import _parent, load_credential, save_credential
from autoontology.security.internal import identity_enabled
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity

ROOT = {
    "doctor": "settings.read",
    "status": "settings.read",
    "setup": "settings.manage",
    "ui": "settings.manage",
    "up": "settings.manage",
    "down": "settings.manage",
    "restart": "settings.manage",
    "init": "settings.manage",
    "demo": "settings.manage",
    "build": "candidate.prepare",
    "apply": "candidate.prepare",
    "runs": "runs.read",
    "ontology": "knowledge.read",
    "export": "exports.read",
}
GROUPS = {
    "source": {"add": "source.edit", "rm": "source.edit", "list": "source.read"},
    "review": {
        "list": "candidate.read",
        "accept": "review.decide",
        "reject": "review.decide",
        "acknowledge": "review.decide",
        "undo": "review.decide",
    },
    "tools": {
        "install-kordoc": "settings.manage",
        "build-ui": "settings.manage",
        "status": "settings.read",
        "prune-connections": "settings.manage",
    },
    "provider": {"status": "settings.read", "test": "settings.manage", "use": "settings.manage"},
    "ops": {
        "list": "candidate.read",
        "decide": "review.decide",
        "check": "checks.edit",
        "model-identity": "settings.read",
        "processing": "history.read",
        "processing-record": "history.read",
        "validate": "validation.run",
        "publish": "knowledge.publish",
        "documents": "originals.read",
        "source-revisions": "history.read",
        "publications": "history.read",
        "publication": "history.read",
        "restore-prepare": "candidate.prepare",
        "restores": "candidate.read",
        "restoration": "candidate.read",
        "restore": "knowledge.restore",
        "preserve-original": "source.edit",
        "original": "originals.read",
        "export-view": "exports.read",
        "execution": "runs.read",
        "ask": "query.run",
        "retrieve": "query.run",
        "sql": "query.run",
        "sql-result": "results.read",
        "sparql": "query.run",
        "traces": "results.read",
        "trace": "results.read",
        "forget-trace": "results.delete",
        "conversations": "results.read",
        "conversation": "results.read",
        "forget-conversation": "results.delete",
        "cases": "candidate.read",
        "case": "candidate.read",
        "collection": "collection.read",
        "collection-workers": "collection.configure",
        "collection-configure": "collection.configure",
        "collection-job": "collection.read",
        "collection-history": "collection.read",
        "collection-pause": "collection.configure",
        "collection-resume": "collection.configure",
        "collection-cancel": "collection.configure",
        "collection-retry": "collection.configure",
        "collection-ack": "collection.read",
        "collection-poll": "collection.work",
        "collection-worker": "collection.work",
        "host-roots": "identity.admin",
        "host-root-allow": "identity.admin",
        "host-root-revoke": "identity.admin",
        "ax": "ax.read",
        "ax-context": "candidate.read",
        "ax-context-set": "review.suggest",
        "ax-efforts": "ax.record",
        "ax-effort-start": "ax.record",
        "ax-effort-stop": "ax.record",
        "ax-effort-attest": "ax.record",
        "ax-baselines": "ax.read",
        "ax-baseline-add": "ax.configure",
        "ax-baseline-void": "ax.configure",
        "ax-target-set": "ax.configure",
        "feedback-case": "review.suggest",
        "feedback-resolve": "feedback.manage",
        "check-remove": "checks.edit",
        "comparison-sample": "validation.run",
        "compare": "validation.run",
        "quality": "candidate.read",
        "quality-version": "candidate.read",
        "quality-prepare": "review.suggest",
        "quality-activate": "checks.edit",
        "quality-deactivate": "checks.edit",
        "quality-runs": "candidate.read",
        "rules": "candidate.read",
        "case-summary": "candidate.read",
        "case-members": "candidate.read",
        "case-create": "review.suggest",
        "case-update": "review.suggest",
        "case-prepare": "review.suggest",
        "case-confirm": "review.decide",
        "case-rebase": "review.suggest",
        "case-reopen": "review.decide",
        "case-resolve": "review.decide",
    },
}

_CREDENTIAL_PATH: ContextVar[Path | None] = ContextVar("autoontology_cli_credential", default=None)
DOMAIN_READ_COMMANDS = {("ops", "documents"), ("ops", "original"), ("ops", "export-view")}
DOMAIN_READ_COMMANDS.update(
    ("ops", name)
    for name, action in GROUPS["ops"].items()
    if action in {"query.run", "results.read", "results.delete"}
)


def _emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _fail(error):
    _emit(
        {
            "error": error.code,
            "detail": str(error),
            **({"retry_after": error.retry_after} if error.retry_after else {}),
        }
    )
    raise typer.Exit(1)


def credential_path(settings):
    selected = _CREDENTIAL_PATH.get()
    return (
        Path(
            selected
            or os.environ.get("AUTOONTOLOGY_CREDENTIAL_FILE")
            or settings.home / "security/cli-session.json"
        )
        .expanduser()
        .absolute()
    )


def read_credential(settings, *, allow_expired=False):
    service = Identity(Store.for_home(settings.home))
    state = service.status()
    if not state["configured"]:
        raise AccessError(
            "Individual identity is not configured for this workspace.",
            "identity_unconfigured",
            409,
        )
    options = {"workspace_id": state["workspace_id"], "origin": settings.service_url}
    if allow_expired:
        options["clock"] = lambda: 0
    return service, load_credential(credential_path(settings), **options)


def request_headers(settings):
    """No authenticated CLI invocation may fall back to anonymous HTTP."""
    if not identity_enabled(settings.home):
        if os.environ.get("AUTOONTOLOGY_CREDENTIAL_FILE"):
            raise AccessError(
                "The selected workspace has no individual identity; refusing credential fallback.",
                "identity_unconfigured",
                409,
            )
        return {}
    service, saved = read_credential(settings)
    service.authenticate(saved.secret.reveal())
    return {"Authorization": "Bearer " + saved.secret.reveal()}


def _password(path, *, new=False):
    if path is None:
        if not sys.stdin.isatty():
            raise AccessError(
                "Use an owner-only --password-file without a terminal. Never pass a password as an argument or environment variable.",
                "password_input_required",
                400,
            )
        return typer.prompt(
            "New password" if new else "Password", hide_input=True, confirmation_prompt=new
        )
    try:
        with _parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                ):
                    raise ValueError
                raw = file.read(1027)
        value = raw.removesuffix(b"\n").removesuffix(b"\r")
        if len(value) > 1024:
            raise ValueError
        return value.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise AccessError(
            "Use a regular owner-only UTF-8 password file, at most 1024 bytes, in a private directory.",
            "password_file_invalid",
            400,
        ) from None


def _enrollment_session(settings, result, username, password):
    """Save/reuse only this owner session after the host pin became active."""
    service = Identity(Store.for_home(settings.home))
    path = credential_path(settings)
    if path.exists() or path.is_symlink():
        saved = load_credential(
            path, workspace_id=result["workspace_id"], origin=settings.service_url
        )
        actor = service.authenticate(saved.secret.reveal())
        if actor.principal_id != result["principal_id"] or actor.kind != "human":
            raise AccessError(
                "The existing credential file belongs to another principal.",
                "credential_file_exists",
                409,
            )
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        issued = service.login(username, password, peer="local-cli-enrollment")
        try:
            saved = save_credential(
                path, issued, workspace_id=result["workspace_id"], origin=settings.service_url
            )
        except (AccessError, OSError):
            service.revoke_credential(
                service.authenticate(issued.secret.reveal()), issued.credential_id
            )
            raise
    return {**result, "session": saved.public(), "credential_file": str(path)}


class AuthorityGroup(TyperGroup):
    """Report effect-time denials with the same stable contract as entry-time denials."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except AccessError as error:
            _fail(error)


def install(app, settings_for):
    app.info.cls = AuthorityGroup
    auth = typer.Typer(
        help="Individual login, principals, domains, delegation and audit.", no_args_is_help=True
    )
    app.add_typer(auth, name="auth")

    def call(action):
        try:
            settings = settings_for()
            service, saved = read_credential(settings)
            actor = service.authenticate(saved.secret.reveal())
            return action(service, actor, settings)
        except AccessError as error:
            _fail(error)

    @app.callback()
    def execution(
        ctx: typer.Context,
        credential_file: Path = typer.Option(
            None,
            "--credential-file",
            envvar="AUTOONTOLOGY_CREDENTIAL_FILE",
            help="Owner-only credential file; never a raw token.",
        ),
    ):
        ctx.ensure_object(dict)
        ctx.obj["credential_file"] = credential_file
        binding = _CREDENTIAL_PATH.set(credential_file)
        ctx.call_on_close(lambda: _CREDENTIAL_PATH.reset(binding))
        if ctx.invoked_subcommand in {None, "auth", "version"}:
            return
        settings = settings_for()
        try:
            configured = identity_enabled(settings.home)
            if not configured:
                if credential_file:
                    raise AccessError(
                        "The selected workspace has no individual identity; refusing local-authority fallback.",
                        "identity_unconfigured",
                        409,
                    )
                return
            service, saved = read_credential(settings)
            actor = service.authenticate(saved.secret.reveal())
            ctx.with_resource(acting_as(service.store, actor))
            if ctx.invoked_subcommand not in GROUPS:
                action = ROOT.get(ctx.invoked_subcommand)
                if not action:
                    raise AccessError(
                        "No permission is defined for this command.", "permission_not_defined", 403
                    )
                (authorize_any if ctx.invoked_subcommand == "ontology" else authorize)(
                    service.store, action
                )
        except AccessError as error:
            _fail(error)

    def group_boundary(group_name, policies):
        def check(ctx: typer.Context):
            settings = settings_for()
            if not identity_enabled(settings.home):
                return
            try:
                action = policies.get(ctx.invoked_subcommand)
                if not action:
                    raise AccessError(
                        "No permission is defined for this command.", "permission_not_defined", 403
                    )
                (
                    authorize_any
                    if (group_name, ctx.invoked_subcommand) in DOMAIN_READ_COMMANDS
                    else authorize
                )(Store.for_home(settings.home), action)
            except AccessError as error:
                _fail(error)

        return check

    for group in app.registered_groups:
        if group.name in GROUPS:
            group.typer_instance.callback()(group_boundary(group.name, GROUPS[group.name]))

    @auth.command("enroll")
    def enroll_owner(
        username: str,
        workspace: str = typer.Option(..., "--workspace"),
        name: str = typer.Option(..., "--name"),
        password_file: Path = typer.Option(None, "--password-file"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Enroll the first owner locally, pin the workspace, and save a private session."""
        from autoontology.security.owner_enrollment import enroll

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm the local OS-owner ceremony with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "An application credential cannot authorize first enrollment.",
                    "local_owner_required",
                    403,
                )
            settings = settings_for()
            password = _password(password_file, new=True)
            result = enroll(
                Store.for_home(settings.home), workspace, username, name, password, resume=resume
            )
            _emit(_enrollment_session(settings, result, username, password))
        except AccessError as error:
            _fail(error)
        except (OSError, sqlite3.DatabaseError):
            _fail(
                AccessError(
                    "Enrollment or session saving could not finish. Preserve the database and pin; use --resume for a pending enrollment or auth login for an active enrollment.",
                    "owner_enrollment_incomplete",
                    503,
                )
            )

    @auth.command("restore-read-review")
    def restore_read_review(
        destination: Path,
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
    ):
        """Inspect the exact restored local SQL target without enabling a query."""
        from autoontology.recovery.read_review import prepare

        try:
            if not confirm_local_owner or _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "Use the explicit local OS-owner review without an application credential.",
                    "local_owner_required",
                    403,
                )
            _emit(prepare(destination))
        except AccessError as error:
            _fail(error)

    @auth.command("restore-read-confirm")
    def restore_read_confirm(
        destination: Path,
        review_sha256: str = typer.Option(..., "--review-sha256"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        acknowledge_new_data_target: bool = typer.Option(False, "--acknowledge-new-data-target"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Confirm one exact physical target review; keep ingestion and business effects held."""
        from autoontology.recovery.read_review import confirm

        try:
            if (
                not confirm_local_owner
                or not acknowledge_new_data_target
                or _CREDENTIAL_PATH.get() is not None
            ):
                raise AccessError(
                    "Confirm the exact local read target with --confirm-local-owner --acknowledge-new-data-target; application credentials cannot authorize this ceremony.",
                    "local_owner_required",
                    403,
                )
            _emit(confirm(destination, review_sha256, resume=resume))
        except AccessError as error:
            _fail(error)

    @auth.command("restore-activate")
    def restore_activate(
        destination: Path,
        username: str,
        workspace_id: str = typer.Option(..., "--workspace-id"),
        password_file: Path = typer.Option(None, "--password-file"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        confirm_source_offline: bool = typer.Option(False, "--confirm-source-offline"),
        revoke_all_sessions: bool = typer.Option(False, "--revoke-all-sessions"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Retire an offline source and activate restored identity; hold unreviewed consumers."""
        from autoontology.recovery.transfer import activate

        try:
            if not confirm_local_owner or not confirm_source_offline or not revoke_all_sessions:
                raise AccessError(
                    "Confirm local ownership, the stopped original and workspace-wide session revocation with --confirm-local-owner --confirm-source-offline --revoke-all-sessions.",
                    "restore_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "Application credentials cannot authorize host transfer.",
                    "local_owner_required",
                    403,
                )
            _emit(
                activate(
                    destination,
                    username,
                    _password(password_file, new=True),
                    workspace_id=workspace_id,
                    resume=resume,
                )
            )
        except AccessError as error:
            _fail(error)

    @auth.command("restore-stage")
    def restore_stage(
        checkpoint: Path,
        destination: Path,
        workspace_id: str = typer.Option(..., "--workspace-id"),
        request_id: str = typer.Option(..., "--request-id"),
        ui_port: int = typer.Option(..., "--ui-port"),
        backend_port: int = typer.Option(..., "--backend-port"),
        postgres_port: int = typer.Option(..., "--postgres-port"),
        copy_mode: str = typer.Option("clone", "--copy-mode"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Prepare a new inactive runtime; do not transfer authority or start services."""
        from autoontology.recovery.staging import stage_restore

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm the local OS-owner ceremony with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "Application credentials cannot authorize host restoration.",
                    "local_owner_required",
                    403,
                )
            _emit(
                stage_restore(
                    checkpoint,
                    destination,
                    workspace_id=workspace_id,
                    request_id=request_id,
                    ports={"ui": ui_port, "backend": backend_port, "postgres": postgres_port},
                    copy_mode=copy_mode,
                    resume=resume,
                )
            )
        except AccessError as error:
            _fail(error)

    @auth.command("restore-status")
    def restore_status(
        destination: Path,
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
    ):
        """Inspect an inactive reservation without opening its database."""
        from autoontology.recovery.staging import restore_status as inspect_restore

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm local OS-owner diagnostics with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "Application credentials cannot authorize host restoration.",
                    "local_owner_required",
                    403,
                )
            _emit(inspect_restore(destination))
        except AccessError as error:
            _fail(error)

    @auth.command("backup-verify")
    def backup_verify(
        checkpoint: Path,
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
    ):
        """Inspect a complete offline backup; never activate or start its runtime."""
        from autoontology.recovery.checkpoint import verify_bundle

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm local OS-owner backup inspection with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "An application credential cannot authorize host backup inspection.",
                    "local_owner_required",
                    403,
                )
            # Deliberately no Settings/Store construction: the current home may be lost.
            _emit(verify_bundle(checkpoint))
        except AccessError as error:
            _fail(error)

    @auth.command("owner-status")
    def owner_status(confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner")):
        """Inspect host-local enrollment/recovery state without creating a store."""
        from autoontology.security.owner_recovery import local_status

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm local OS-owner diagnostics with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "Application credentials do not authorize local owner diagnostics.",
                    "local_owner_required",
                    403,
                )
            _emit(local_status(settings_for().home / "autoontology.db"))
        except AccessError as error:
            _fail(error)
        except (OSError, sqlite3.DatabaseError):
            _fail(
                AccessError(
                    "Preserve the host pin and database; local owner diagnostics could not verify this workspace.",
                    "identity_configuration_missing",
                    503,
                )
            )

    @auth.command("owner-recover")
    def owner_recover(
        username: str,
        workspace_id: str = typer.Option(..., "--workspace-id"),
        request_id: str = typer.Option(None, "--request-id"),
        password_file: Path = typer.Option(None, "--password-file"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        revoke_all_sessions: bool = typer.Option(False, "--revoke-all-sessions"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Recover an existing admin password locally; revoke all sessions and delegations."""
        from autoontology.security.owner_recovery import recover

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm the local OS-owner ceremony with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if not revoke_all_sessions:
                raise AccessError(
                    "Confirm workspace-wide session/delegation revocation with --revoke-all-sessions.",
                    "recovery_scope_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "An application credential cannot authorize local owner recovery.",
                    "local_owner_required",
                    403,
                )
            settings = settings_for()
            _emit(
                recover(
                    Store.for_home(settings.home),
                    username,
                    _password(password_file, new=True),
                    workspace_id=workspace_id,
                    request_id=request_id,
                    resume=resume,
                )
            )
        except AccessError as error:
            _fail(error)
        except (OSError, sqlite3.DatabaseError):
            _fail(
                AccessError(
                    "Credential recovery could not finish. Preserve the database and pin; resume the same request explicitly.",
                    "owner_recovery_incomplete",
                    503,
                )
            )

    @auth.command("owner-adopt")
    def owner_adopt(
        username: str,
        password_file: Path = typer.Option(None, "--password-file"),
        confirm_local_owner: bool = typer.Option(False, "--confirm-local-owner"),
        resume: bool = typer.Option(False, "--resume"),
    ):
        """Pin an existing complete identity under the local OS owner's registry."""
        from autoontology.security.owner_adoption import adopt

        try:
            if not confirm_local_owner:
                raise AccessError(
                    "Confirm the local OS-owner ceremony with --confirm-local-owner.",
                    "local_owner_confirmation_required",
                    400,
                )
            if _CREDENTIAL_PATH.get() is not None:
                raise AccessError(
                    "An application credential cannot authorize host-owner adoption.",
                    "local_owner_required",
                    403,
                )
            settings = settings_for()
            _emit(
                adopt(
                    Store.for_home(settings.home), username, _password(password_file), resume=resume
                )
            )
        except AccessError as error:
            _fail(error)
        except (OSError, sqlite3.DatabaseError):
            _fail(
                AccessError(
                    "Owner adoption could not finish; preserve its pending pin and retry with --resume.",
                    "identity_configuration_missing",
                    503,
                )
            )

    @auth.command("login")
    def login(username: str, password_file: Path = typer.Option(None, "--password-file")):
        settings = settings_for()
        try:
            service = Identity(Store.for_home(settings.home))
            state = service.status()
            if not state["configured"]:
                raise AccessError(
                    "Individual identity has not been enrolled by the local owner.",
                    "identity_unconfigured",
                    409,
                )
            path = credential_path(settings)
            if path.exists() or path.is_symlink():
                raise AccessError(
                    "Sign out with auth logout before replacing this file, including an expired session.",
                    "credential_file_exists",
                    409,
                )
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            issued = service.login(username, _password(password_file), peer="local-cli")
            try:
                saved = save_credential(
                    path, issued, workspace_id=state["workspace_id"], origin=settings.service_url
                )
            except (AccessError, OSError):
                service.revoke_credential(
                    service.authenticate(issued.secret.reveal()), issued.credential_id
                )
                raise
            _emit({**saved.public(), "principal": issued.principal, "credential_file": str(path)})
        except AccessError as error:
            _fail(error)
        except OSError:
            _fail(
                AccessError(
                    "The credential could not be saved; the issued session was revoked.",
                    "credential_save_failed",
                    500,
                )
            )

    @auth.command("status")
    def status():
        settings = settings_for()
        try:
            configured = identity_enabled(settings.home)
            _emit(
                {
                    "configured": configured,
                    "credential_file": str(credential_path(settings)),
                    "credential_file_exists": credential_path(settings).is_file(),
                }
            )
        except AccessError as error:
            _fail(error)

    @auth.command("whoami")
    def whoami():
        _emit(call(lambda service, actor, settings: service.describe(actor)))

    @auth.command("logout")
    def logout():
        settings = settings_for()
        try:
            service, saved = read_credential(settings, allow_expired=True)
            already_invalid = saved.expires_at <= time.time()
            try:
                actor = service.authenticate(saved.secret.reveal())
            except AccessError as error:
                if error.status != 401:
                    raise
                already_invalid = True
            else:
                service.revoke_credential(actor, saved.credential_id)
            path = credential_path(settings)
            current = load_credential(
                path, workspace_id=saved.workspace_id, origin=saved.origin, clock=lambda: 0
            )
            if current.credential_id != saved.credential_id:
                raise AccessError(
                    "The local session file changed; its newer credential was preserved.",
                    "credential_file_changed",
                    409,
                )
            path.unlink()
            _emit({"signed_out": True, "already_invalid": already_invalid})
        except AccessError as error:
            _fail(error)

    @auth.command("people")
    def people():
        _emit(call(lambda service, actor, settings: {"items": service.principals(actor)}))

    @auth.command("administration")
    def administration():
        _emit(call(lambda service, actor, settings: service.administration(actor)))

    @auth.command("domains")
    def domains():
        _emit(
            call(
                lambda service, actor, settings: {"items": service.administration(actor)["domains"]}
            )
        )

    @auth.command("sessions")
    def sessions(
        principal: str = typer.Option(
            None, "--principal", help="Principal ID, or * for all (human admin only)."
        ),
    ):
        _emit(
            call(
                lambda service, actor, settings: {
                    "items": service.credentials(actor, principal_id=principal)
                }
            )
        )

    @auth.command("revoke-session")
    def revoke_session(credential: str):
        def revoke(service, actor, settings):
            service.revoke_credential(actor, credential)
            return {"revoked": credential}

        _emit(call(revoke))

    @auth.command("reset-password")
    def reset_password(principal: str, password_file: Path = typer.Option(None, "--password-file")):
        def change(service, actor, settings):
            service.require(actor, "identity.admin")
            service.change_password(actor, principal, _password(password_file, new=True))
            return {"changed": principal, "sessions_invalidated": True}

        _emit(call(change))

    @auth.command("add-person")
    def add_person(
        username: str,
        name: str = typer.Option(..., "--name"),
        role: list[str] = typer.Option(..., "--role"),
        domain: list[str] = typer.Option(None, "--domain"),
        password_file: Path = typer.Option(None, "--password-file"),
    ):
        _emit(
            call(
                lambda service, actor, settings: service.create_human(
                    actor, username, name, _password(password_file, new=True), role, domains=domain
                )
            )
        )

    @auth.command("roles")
    def roles(
        principal: str,
        role: list[str] = typer.Option(..., "--role"),
        domain: list[str] = typer.Option(None, "--domain"),
    ):
        _emit(
            call(
                lambda service, actor, settings: service.set_roles(
                    actor, principal, role, domains=domain
                )
            )
        )

    @auth.command("disable")
    def disable(principal: str):
        _emit(call(lambda service, actor, settings: service.disable(actor, principal)))

    @auth.command("enable")
    def enable(principal: str):
        _emit(call(lambda service, actor, settings: service.enable(actor, principal)))

    @auth.command("domain")
    def domain(slug: str, name: str = typer.Option(..., "--name")):
        _emit(call(lambda service, actor, settings: service.create_domain(actor, slug, name)))

    @auth.command("classify-source")
    def classify_source(source: str, domain: str):
        _emit(
            call(
                lambda service, actor, settings: {
                    "version": service.assign_source(actor, source, domain)
                }
            )
        )

    @auth.command("delegate")
    def delegate(
        username: str,
        name: str = typer.Option(..., "--name"),
        action: list[str] = typer.Option(..., "--action"),
        domain: list[str] = typer.Option(None, "--domain"),
        ttl: int = typer.Option(3600, "--ttl", min=1, max=7 * 24 * 3600),
        out: Path = typer.Option(..., "--out"),
    ):
        def issue(service, actor, settings):
            issued = service.delegate(actor, username, name, action, domains=domain, ttl=ttl)
            try:
                saved = save_credential(
                    out,
                    issued,
                    workspace_id=service.status()["workspace_id"],
                    origin=settings.service_url,
                )
            except (AccessError, OSError):
                service.revoke_delegation(actor, issued.principal["id"])
                raise AccessError(
                    "The agent credential could not be saved; its delegation was revoked. Inspect auth people before retrying with a new name.",
                    "credential_save_failed",
                    409,
                ) from None
            return {
                **saved.public(),
                "principal": issued.principal,
                "credential_file": str(out.absolute()),
            }

        _emit(call(issue))

    @auth.command("revoke-agent")
    def revoke_agent(principal: str):
        def revoke(service, actor, settings):
            service.revoke_delegation(actor, principal)
            return {"revoked": principal}

        _emit(call(revoke))

    @auth.command("audit")
    def audit():
        _emit(
            call(
                lambda service, actor, settings: {
                    "verification": service.verify_audit(actor),
                    "items": service.audit(actor),
                }
            )
        )
