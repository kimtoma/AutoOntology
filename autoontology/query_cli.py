# SPDX-License-Identifier: MIT
"""Named CLI operations use the same local query service and receipts as the UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

import typer

from autoontology.security.cli import request_headers


def install(app, settings_for):
    def emit(value):
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))

    def request(method, path, body=None, on_error=None, service_root="/api/playground"):
        import httpx

        report = on_error or emit
        settings = settings_for()
        try:
            response = httpx.request(
                method,
                f"http://127.0.0.1:{settings.port}{service_root}{path}",
                json=body,
                timeout=360,
                headers=request_headers(settings),
            )
        except httpx.HTTPError as exc:
            report(
                {
                    "error": "service_unavailable",
                    "detail": "Start the local UI service before running a query.",
                }
            )
            raise typer.Exit(1) from exc
        try:
            data = response.json()
        except ValueError as exc:
            report({"error": "invalid_service_response"})
            raise typer.Exit(1) from exc
        if not response.is_success:
            report(data)
            raise typer.Exit(1)
        return data

    def identifier(value):
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            emit(
                {
                    "error": "invalid_identifier",
                    "detail": "Use the identifier returned by the query service.",
                }
            )
            raise typer.Exit(1)
        return value

    def output_path(output, watch=False):
        output = output.expanduser().absolute()
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            value = {
                "error": "output_exists"
                if output.exists() or output.is_symlink()
                else "output_directory_missing",
                "detail": "Choose a new file in an existing directory.",
            }
            if watch:
                typer.echo(json.dumps({"event": "error", "data": value}))
            else:
                emit(value)
            raise typer.Exit(1)
        return output

    def save_result(trace_id, output, reject):
        import httpx

        settings = settings_for()
        try:
            response = httpx.request(
                "GET",
                f"http://127.0.0.1:{settings.port}/api/playground/traces/{identifier(trace_id)}/result.csv",
                timeout=30,
                headers=request_headers(settings),
            )
            response.raise_for_status()
            payload = response.content
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError("file exceeds supported size")
            fd, temporary = tempfile.mkstemp(prefix=".autoontology-result-", dir=output.parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                # Exclusive installation: a concurrent file is never overwritten.
                os.link(temporary, output)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {
                "path": str(output),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "trace_id": trace_id,
            }
        except (OSError, ValueError, httpx.HTTPError) as exc:
            reject(
                {
                    "error": "result_download_failed",
                    "trace_id": trace_id,
                    "detail": "The query completed; its result file could not be saved. Inspect the receipt before retrying the query.",
                }
            )
            raise typer.Exit(1) from exc

    def stream(kind, body, watch, output=None):
        import httpx

        def reject(value):
            if watch:
                typer.echo(json.dumps({"event": "error", "data": value}, ensure_ascii=False))
            else:
                emit(value)

        settings = settings_for()
        trace_id, error, done = None, None, False
        try:
            with httpx.stream(
                "POST",
                f"http://127.0.0.1:{settings.port}/api/playground/{kind}",
                json=body,
                timeout=360,
                headers=request_headers(settings),
            ) as response:
                if not response.is_success:
                    reject(json.loads(response.read()))
                    raise typer.Exit(1)
                name, lines = "message", []
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        lines.append(line[5:].lstrip())
                    elif not line and lines:
                        data = json.loads("\n".join(lines))
                        trace_id = data.get("trace_id") or trace_id
                        if name == "error":
                            error = data
                        done = done or name == "done"
                        if watch:
                            typer.echo(
                                json.dumps({"event": name, "data": data}, ensure_ascii=False)
                            )
                        name, lines = "message", []
        except (httpx.HTTPError, ValueError) as exc:
            reject(
                {
                    "error": "query_transport_failed",
                    "trace_id": trace_id,
                    "detail": "Inspect the usage record; do not assume the query completed.",
                }
            )
            raise typer.Exit(1) from exc
        if not trace_id:
            reject(
                error
                or {
                    "error": "trace_missing",
                    "detail": "The request did not return a usage receipt.",
                }
            )
            raise typer.Exit(1)
        if error and (
            error.get("status") in (401, 403)
            or error.get("error")
            in {
                "authority_changed",
                "view_authority_changed",
                "result_scope_changed",
                "result_scope_unknown",
            }
        ):
            if not watch:
                reject({**error, "trace_id": trace_id})
            raise typer.Exit(1)
        receipt = request("GET", f"/traces/{identifier(trace_id)}", on_error=reject)
        successful = done and not error and receipt["status"] == "succeeded"
        download = (
            save_result(trace_id, output, reject) if output is not None and successful else None
        )
        if watch:
            typer.echo(json.dumps({"event": "receipt", "data": receipt}, ensure_ascii=False))
            if download:
                typer.echo(json.dumps({"event": "download", "data": download}, ensure_ascii=False))
        else:
            emit({"receipt": receipt, "download": download} if download else receipt)
        if not successful:
            raise typer.Exit(1)

    @app.command("execution")
    def execution(
        run_id: str,
        cancel: bool = typer.Option(False, "--cancel"),
        watch: bool = typer.Option(False, "--watch"),
    ):
        from autoontology.web.runs import RUN_ID_RE

        def report(value, event="execution"):
            if watch:
                typer.echo(json.dumps({"event": event, "data": value}, ensure_ascii=False))
            else:
                emit(value)

        if not RUN_ID_RE.fullmatch(run_id):
            report({"error": "invalid_run_id"}, "error")
            raise typer.Exit(1)
        options = {"service_root": "/api", "on_error": lambda value: report(value, "error")}
        if cancel:
            report(request("POST", f"/runs/{run_id}/cancel", **options), "cancel_requested")
            if not watch:
                return
        previous = None
        while True:
            result = request("GET", f"/runs/{run_id}", **options)
            if result != previous:
                report(result)
                previous = result
            if result["status"] not in ("running", "pending"):
                if (
                    watch
                    and result["status"] != "succeeded"
                    and not (cancel and result["status"] == "cancelled")
                ):
                    raise typer.Exit(1)
                return
            if not watch:
                return
            time.sleep(1)

    @app.command("ask")
    def ask(
        question: str,
        top_k: int = typer.Option(12, "--top-k", min=1, max=40),
        conversation: str | None = typer.Option(None, "--conversation"),
        watch: bool = typer.Option(
            False, "--watch", help="Emit NDJSON progress and the final receipt."
        ),
    ):
        stream(
            "chat", {"question": question, "top_k": top_k, "conversation_id": conversation}, watch
        )

    @app.command("retrieve")
    def retrieve(question: str, top_k: int = typer.Option(12, "--top-k", min=1, max=40)):
        emit(request("POST", "/retrieve", {"question": question, "top_k": top_k}))

    @app.command("sql")
    def sql(
        question: str,
        source: str | None = typer.Option(None, "--source"),
        watch: bool = typer.Option(False, "--watch"),
        conversation_id: uuid.UUID | None = typer.Option(None, "--conversation-id"),
        output: Path | None = typer.Option(
            None, "--output", help="Save the completed receipt's CSV without overwriting a file."
        ),
    ):
        if output is not None:
            output = output_path(output, watch)
        stream(
            "sql",
            {
                "question": question,
                "source": source,
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
            watch,
            output,
        )

    @app.command("sql-result")
    def sql_result(trace_id: str, output: Path = typer.Option(..., "--output")):
        """Download a completed query without running its model or SQL again."""
        output = output_path(output)
        emit(save_result(identifier(trace_id), output, emit))

    @app.command("sparql")
    def sparql(query: str, limit: int = typer.Option(100, "--limit", min=1, max=500)):
        emit(request("POST", "/sparql", {"query": query, "limit": limit}))

    @app.command("traces")
    def traces(
        limit: int = typer.Option(50, "--limit", min=1, max=500),
        all_users: bool = typer.Option(
            False,
            "--all-users",
            help="Explicit human-admin inspection of other owners and legacy records.",
        ),
    ):
        emit(request("GET", f"/traces?limit={limit}" + ("&all_users=true" if all_users else "")))

    @app.command("trace")
    def trace(trace_id: str):
        emit(request("GET", f"/traces/{identifier(trace_id)}"))

    @app.command("forget-trace")
    def forget_trace(trace_id: str):
        emit(request("DELETE", f"/traces/{identifier(trace_id)}"))

    @app.command("conversations")
    def conversations(
        all_users: bool = typer.Option(
            False,
            "--all-users",
            help="Explicit human-admin inspection; does not permit replying as another owner.",
        ),
    ):
        emit(request("GET", "/conversations" + ("?all_users=true" if all_users else "")))

    @app.command("conversation")
    def conversation(conversation_id: str):
        emit(request("GET", f"/conversations/{identifier(conversation_id)}"))

    @app.command("forget-conversation")
    def forget_conversation(conversation_id: str):
        emit(request("DELETE", f"/conversations/{identifier(conversation_id)}"))
