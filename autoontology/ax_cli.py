# SPDX-License-Identifier: MIT
"""Native semantic CLI for workflow context, measured baseline and human intervals."""

import json
from pathlib import Path

import typer

from autoontology.security.policy import AccessError
from autoontology.store.ax import AX


def install(app, settings_for, store_for):
    def service():
        return AX(store_for(settings_for()))

    def emit(value):
        typer.echo(json.dumps({"schema": 1, **value}, ensure_ascii=False, indent=2))

    def read(path):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError):
            raise AccessError(
                "Use a regular bounded UTF-8 JSON object file.", "ax_input_invalid", 400
            ) from None

    @app.command("ax")
    def overview():
        emit(service().overview())

    @app.command("ax-context")
    def context(ident: str):
        emit(service().context(ident))

    @app.command("ax-context-set")
    def set_context(
        ident: str,
        input_file: Path = typer.Option(..., "--input"),
        reason: str = typer.Option(..., "--reason"),
        previous: str = typer.Option(None, "--previous"),
    ):
        emit(service().set_context(ident, read(input_file), previous=previous, reason=reason))

    @app.command("ax-efforts")
    def efforts():
        emit({"items": service().efforts()})

    @app.command("ax-effort-start")
    def start(ident: str, note: str = typer.Option(..., "--note")):
        emit(service().start_effort(ident, note=note))

    @app.command("ax-effort-stop")
    def stop(
        ident: str,
        note: str = typer.Option(..., "--note"),
        discard: bool = typer.Option(False, "--discard"),
    ):
        emit(service().stop_effort(ident, note=note, discard=discard))

    @app.command("ax-effort-attest")
    def attest(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().attest_effort(ident, reason=reason, confirm=confirm))

    @app.command("ax-baselines")
    def baselines():
        emit({"items": service().baselines()})

    @app.command("ax-baseline-add")
    def baseline(
        input_file: Path = typer.Option(..., "--input"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().add_baseline(read(input_file), confirm=confirm))

    @app.command("ax-baseline-void")
    def void(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().void_baseline(ident, reason=reason, confirm=confirm))

    @app.command("ax-target-set")
    def target(
        input_file: Path = typer.Option(..., "--input"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().set_target(read(input_file), confirm=confirm))

    @app.command("feedback-case")
    def feedback_case(
        ident: int,
        title: str = typer.Option(..., "--title"),
        reason: str = typer.Option(..., "--reason"),
        owner: str = typer.Option(None, "--owner"),
    ):
        emit(service().feedback_case(ident, title=title, reason=reason, owner_id=owner))

    @app.command("feedback-resolve")
    def feedback_resolve(
        ident: int,
        case: str = typer.Option(..., "--case"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        emit(service().resolve_feedback(ident, case, confirm=confirm))
