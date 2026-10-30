# SPDX-License-Identifier: MIT
"""Named CLI operations use the same local query service and receipts as the UI."""

from __future__ import annotations

import json
import re

import httpx
import typer


def install(app, settings_for):
    def emit(value):
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))

    def request(method, path, body=None, on_error=None):
        report = on_error or emit
        settings = settings_for()
        try:
            response = httpx.request(
                method,
                f"http://127.0.0.1:{settings.port}/api/playground{path}",
                json=body,
                timeout=360,
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

    def stream(kind, body, watch):
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
        receipt = request("GET", f"/traces/{identifier(trace_id)}", on_error=reject)
        if watch:
            typer.echo(json.dumps({"event": "receipt", "data": receipt}, ensure_ascii=False))
        else:
            emit(receipt)
        if not done or error or receipt["status"] != "succeeded":
            raise typer.Exit(1)

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
    ):
        stream("sql", {"question": question, "source": source}, watch)

    @app.command("sparql")
    def sparql(query: str, limit: int = typer.Option(100, "--limit", min=1, max=500)):
        emit(request("POST", "/sparql", {"query": query, "limit": limit}))

    @app.command("traces")
    def traces(limit: int = typer.Option(50, "--limit", min=1, max=500)):
        emit(request("GET", f"/traces?limit={limit}"))

    @app.command("trace")
    def trace(trace_id: str):
        emit(request("GET", f"/traces/{identifier(trace_id)}"))

    @app.command("forget-trace")
    def forget_trace(trace_id: str):
        emit(request("DELETE", f"/traces/{identifier(trace_id)}"))

    @app.command("conversations")
    def conversations():
        emit(request("GET", "/conversations"))

    @app.command("conversation")
    def conversation(conversation_id: str):
        emit(request("GET", f"/conversations/{identifier(conversation_id)}"))

    @app.command("forget-conversation")
    def forget_conversation(conversation_id: str):
        emit(request("DELETE", f"/conversations/{identifier(conversation_id)}"))
