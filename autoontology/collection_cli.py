# SPDX-License-Identifier: MIT
"""Named native operations for observation, preparation, recovery and local outbox."""

import json
import time
from pathlib import Path

import typer

from autoontology.security.policy import AccessError


def install(app, settings_for, store_for):
    def service():
        from autoontology.store.collection import Collection

        return Collection(store_for(settings_for()))

    def emit(value):
        typer.echo(json.dumps({"schema": 1, **value}, ensure_ascii=False, indent=2))

    def confirmed(confirm):
        if not confirm:
            raise AccessError(
                "Confirm this collection change with --confirm.",
                "collection_confirmation_required",
                400,
            )

    @app.command("collection")
    def listing():
        queue = service()
        emit(
            {
                "schedules": queue.listing(),
                "jobs": queue.jobs(),
                "notices": queue.notices(),
                "workers": queue.worker_status(),
            }
        )

    @app.command("collection-workers")
    def workers():
        emit({"items": service().workers()})

    @app.command("collection-configure")
    def configure(
        input_file: Path = typer.Option(..., "--input"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        confirmed(confirm)
        try:
            if (
                input_file.is_symlink()
                or not input_file.is_file()
                or input_file.stat().st_size > 16000
            ):
                raise ValueError
            data = json.loads(input_file.read_text())
            if not isinstance(data, dict) or set(data) - {
                "source",
                "worker_id",
                "interval",
                "mode",
                "reason",
                "max_attempts",
                "ttl",
            }:
                raise ValueError
        except (OSError, ValueError):
            raise AccessError(
                "Use a bounded collection configuration JSON file.", "collection_input", 400
            ) from None
        emit(service().configure(**data))

    @app.command("collection-job")
    def job(ident: str):
        emit(service().job(ident))

    @app.command("collection-history")
    def history(ident: str):
        emit({"items": service().history(ident)})

    def change(ident, reason, confirm, method):
        confirmed(confirm)
        emit(getattr(service(), method)(ident, reason=reason))

    @app.command("collection-pause")
    def pause(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(ident, reason, confirm, "pause")

    @app.command("collection-resume")
    def resume(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(ident, reason, confirm, "resume")

    @app.command("collection-cancel")
    def cancel(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(ident, reason, confirm, "cancel")

    @app.command("collection-retry")
    def retry(
        ident: str,
        reason: str = typer.Option(..., "--reason"),
        confirm: bool = typer.Option(False, "--confirm"),
    ):
        change(ident, reason, confirm, "retry")

    @app.command("collection-ack")
    def ack(ident: str):
        emit(service().ack(ident))

    @app.command("collection-poll")
    def poll(ident: str):
        emit(service().poll(ident))

    @app.command("collection-worker")
    def worker(
        watch: bool = typer.Option(False, "--watch"),
        poll: int = typer.Option(2, "--poll", min=1, max=60),
    ):
        from autoontology.engine.collection import cycle

        settings = settings_for()
        store = store_for(settings)
        while True:
            emit(cycle(store, settings))
            if not watch:
                break
            time.sleep(poll)
