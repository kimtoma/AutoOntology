# SPDX-License-Identifier: MIT
"""Native file/stdout interface to the same frozen-input comparison operation."""

import json
from pathlib import Path

import typer

from autoontology.evaluation import MAX_BYTES, Comparison
from autoontology.security.host_files import HostFiles
from autoontology.security.policy import AccessError


def read_input(path, store):
    try:
        with HostFiles(store).open(path) as stream:
            value = stream.read(MAX_BYTES + 1)
            if len(value) > MAX_BYTES:
                raise ValueError
        return json.loads(value)
    except AccessError:
        raise
    except (OSError, ValueError, RecursionError):
        raise AccessError(
            "Use a regular bounded JSON comparison input, without links or secret paths.",
            "comparison_input_invalid",
            400,
        ) from None


def install(app, settings_for, store_for):
    @app.command("comparison-sample")
    def sample():
        value = Comparison(store_for(settings_for())).sample()
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))

    @app.command("compare")
    def compare(input_file: Path = typer.Option(..., "--input")):
        service = Comparison(store_for(settings_for()))
        service.gate()
        typer.echo(
            json.dumps(
                service.compare(read_input(input_file, service.store)), ensure_ascii=False, indent=2
            )
        )
