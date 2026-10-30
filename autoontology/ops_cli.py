# SPDX-License-Identifier: MIT
"""CLI parity for the reviewed, validated publication workflow."""

from __future__ import annotations

import json
from contextlib import nullcontext

import typer

from autoontology.store.operations import Operations, OperationsError


def commands(settings_for, store_for, fail):
    app = typer.Typer(
        help="Review candidates, configure questions, validate and publish.", no_args_is_help=True
    )

    def run(action, *, edit=None):
        from autoontology.engine.build import WORKSPACE_LEASE
        from autoontology.engine.jobs import JobError, hold_sources

        try:
            settings = settings_for()
            store = store_for(settings)
            with hold_sources(store, [WORKSPACE_LEASE], edit) if edit else nullcontext():
                return action(store, settings)
        except (OperationsError, JobError) as exc:
            fail(str(exc))

    def emit(value):
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))

    @app.command("list")
    def listing():
        def action(store, settings):
            ops = Operations(store)
            return {**ops.state(), "items": ops.changes(), "checks": ops.checks()}

        emit(run(action))

    @app.command()
    def decide(
        item: str,
        revision: int = typer.Option(..., "--revision", min=1),
        decision: str = typer.Option(..., "--decision", help="accept | hold | reject"),
        note: str = typer.Option("", "--note"),
    ):
        emit(
            run(
                lambda store, settings: Operations(store).decide(
                    item, decision, candidate=revision, note=note
                ),
                edit="review",
            )
        )

    @app.command("check")
    def check(question: str, expected: str = typer.Option(..., "--expected")):
        emit(
            run(
                lambda store, settings: Operations(store).add_check(question, expected),
                edit="checks",
            )
        )

    @app.command()
    def validate(revision: int = typer.Option(..., "--revision", min=1)):
        from autoontology.engine.operations import validate as validate_candidate

        result = run(
            lambda store, settings: validate_candidate(store, settings, revision=revision),
            edit="validation",
        )
        emit(result)
        if not result["ok"]:
            raise typer.Exit(1)

    @app.command()
    def publish(
        revision: int = typer.Option(..., "--revision", min=1),
        token: str = typer.Option(
            ..., "--token", help="Content-bound validation token, not a login secret."
        ),
    ):
        from autoontology.engine.operations import run_publish

        result = run(
            lambda store, settings: run_publish(store, settings, revision=revision, token=token)
        )
        emit({"run_id": result.run_id, "status": result.status, "revision": result.revision})

    return app
