# SPDX-License-Identifier: MIT
"""Native JSON evaluation suite and rule operations."""

import json
from pathlib import Path

import typer

from autoontology.security.policy import AccessError
from autoontology.store.quality import Quality


def install(app, settings_for, store_for):
    def service():
        return Quality(store_for(settings_for()))

    def emit(value):
        typer.echo(json.dumps({"schema": 1, **value}, ensure_ascii=False, indent=2))

    @app.command("quality")
    def listing():
        quality = service()
        emit({"active": quality.active(), "versions": quality.versions()})

    @app.command("quality-version")
    def version(ident: str):
        emit(service().get(ident))

    @app.command("quality-prepare")
    def prepare(
        key: str,
        input_file: Path = typer.Option(..., "--input"),
        previous: str = typer.Option(None, "--previous"),
    ):
        try:
            if (
                input_file.is_symlink()
                or not input_file.is_file()
                or input_file.stat().st_size > 1024 * 1024
            ):
                raise ValueError
            payload = json.loads(input_file.read_text())
        except (OSError, ValueError):
            raise AccessError(
                "Use a regular UTF-8 JSON suite file smaller than one MiB.",
                "quality_input_invalid",
                400,
            ) from None
        emit(service().prepare(key, payload, previous=previous))

    @app.command("quality-activate")
    def activate(
        ident: str,
        sha256: str = typer.Option(..., "--sha256"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().activate(ident, sha256, confirm=confirm))

    @app.command("quality-deactivate")
    def deactivate(
        ident: str,
        sha256: str = typer.Option(..., "--sha256"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().deactivate(ident, sha256, confirm=confirm))

    @app.command("quality-runs")
    def runs(ident: str = typer.Option(None, "--id")):
        emit({"items": service().runs(ident)})

    @app.command("rules")
    def rules(revision: int = typer.Option(None, "--revision", min=1)):
        emit(service().rules(revision))

    @app.command("check-remove")
    def remove_check(ident: int):
        from autoontology.store.operations import Operations

        emit(Operations(service().store).remove_check(ident))
