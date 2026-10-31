# SPDX-License-Identifier: MIT
"""Named, JSON-native operations for cases and human review confirmation."""

import json
from pathlib import Path

import typer

from autoontology.security.policy import AccessError
from autoontology.store.cases import Cases
from autoontology.store.operations import OperationsError


def install(app, settings_for, store_for):
    def read(path):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AccessError(
                "Use a regular UTF-8 JSON input file smaller than one MiB.",
                "case_input_invalid",
                400,
            ) from None

    def call(action):
        try:
            value = action(Cases(store_for(settings_for())))
            typer.echo(json.dumps({"schema": 1, **value}, ensure_ascii=False, indent=2))
        except OperationsError as error:
            raise AccessError(str(error), "case_plan_invalid", 409) from error

    @app.command("cases")
    def listing(status: str = typer.Option(None, "--status")):
        call(lambda service: {"items": service.listing(status=status)})

    @app.command("case-summary")
    def summary():
        call(lambda service: service.summary())

    @app.command("case-members")
    def members():
        call(lambda service: {"items": service.members()})

    @app.command("case")
    def detail(ident: str):
        call(lambda service: {**service.get(ident), "history": service.history(ident)})

    @app.command("case-create")
    def create(
        input_file: Path = typer.Option(
            ...,
            "--input",
            help="Case title/reason, owner, due date, decisions, relations and request_key JSON.",
        ),
    ):
        data = read(input_file)
        if (
            not isinstance(data, dict)
            or set(data)
            - {
                "title",
                "reason",
                "owner_id",
                "priority",
                "due_at",
                "changes",
                "links",
                "request_key",
            }
            or not {"title", "reason"} <= set(data)
        ):
            raise AccessError("Use the case-create JSON schema.", "case_input_invalid", 400)
        call(lambda service: service.create(**data))

    @app.command("case-update")
    def update(
        ident: str,
        version: int = typer.Option(..., "--version", min=1),
        input_file: Path = typer.Option(..., "--input"),
    ):
        data = read(input_file)
        if not isinstance(data, dict):
            raise AccessError("Use a case field object.", "case_input_invalid", 400)
        call(lambda service: service.update(ident, version=version, **data))

    @app.command("case-prepare")
    def prepare(
        ident: str,
        version: int = typer.Option(..., "--version", min=1),
        reviewer: str = typer.Option(None, "--reviewer"),
        ttl: int = typer.Option(1800, "--ttl", min=10, max=86400),
    ):
        call(lambda service: service.prepare(ident, version=version, executor_id=reviewer, ttl=ttl))

    @app.command("case-confirm")
    def confirm(
        packet: str,
        sha256: str = typer.Option(..., "--sha256"),
        confirm: bool = typer.Option(
            False,
            "--confirm",
            help="Apply only this exact prepared review packet as the named human.",
        ),
    ):
        if not confirm:
            raise AccessError(
                "Pass --confirm after reviewing the exact packet.",
                "case_confirmation_required",
                400,
            )
        call(lambda service: service.confirm(packet, sha256))

    @app.command("case-rebase")
    def rebase(
        ident: str,
        version: int = typer.Option(..., "--version", min=1),
        reason: str = typer.Option(..., "--reason"),
        proposal: Path = typer.Option(..., "--proposal"),
    ):
        call(
            lambda service: service.rebase(
                ident, version=version, reason=reason, changes=read(proposal)
            )
        )

    @app.command("case-resolve")
    def resolve(
        ident: str,
        version: int = typer.Option(..., "--version", min=1),
        reason: str = typer.Option(..., "--reason"),
        publication: int = typer.Option(None, "--publication", min=1),
    ):
        call(
            lambda service: service.resolve(
                ident, version=version, reason=reason, publication=publication
            )
        )

    @app.command("case-reopen")
    def reopen(
        ident: str,
        version: int = typer.Option(..., "--version", min=1),
        reason: str = typer.Option(..., "--reason"),
    ):
        call(lambda service: service.reopen(ident, version=version, reason=reason))
