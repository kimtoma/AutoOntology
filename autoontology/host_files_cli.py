# SPDX-License-Identifier: MIT
"""Named host-input administration commands; no file content or credentials in output."""

import json
from pathlib import Path

import typer

from autoontology.security.host_files import HostFiles
from autoontology.security.policy import AccessError


def install(app, settings_for, store_for):
    def service():
        return HostFiles(store_for(settings_for()))

    def emit(value):
        typer.echo(json.dumps({"schema": 1, **value}, ensure_ascii=False, indent=2))

    @app.command("host-roots")
    def listing():
        emit(service().listing())

    def change(path, reason, confirm, operation):
        if not confirm:
            raise AccessError(
                "Confirm the exact host root with --confirm.",
                "host_root_confirmation_required",
                400,
            )
        emit(operation(path, reason=reason))

    @app.command("host-root-allow")
    def allow(
        path: Path,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(path, reason, confirm, service().allow)

    @app.command("host-root-revoke")
    def revoke(
        path: Path,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(path, reason, confirm, service().revoke)
