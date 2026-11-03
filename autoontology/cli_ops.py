# SPDX-License-Identifier: MIT
"""CLI parity with the web UI: every page's function as a command (``--json`` for scripts).

Two ways to reach the same handlers the UI calls:

* :class:`LocalApi` -- the ``/api`` router of :mod:`autoontology.web.api` mounted in this
  process over the store (no service needed): ontology revisions / diff / concepts /
  concept / graph, sources freshness, samples, exports. The answers are the UI's, byte for
  byte, because they come from the same route functions.
* :class:`ServiceClient` -- HTTP to the running service (``settings.service_url``) for
  what only the service holds: run details and cancellation (its run manager), live run
  events, the playground (its index, conversations and LLM), the restart. Unreachable ->
  exit 2 with the ``up`` hint.

``settings show|test|set`` use :mod:`autoontology.web.settings_api` (the Settings page's
own functions: origin rule, shell-override refusal, dimension guard); ``show`` asks the
service first so ``running``/``restart_required`` describe the running processes.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import typer

from autoontology.i18n import msg


class _Cli:
    """``autoontology.cli`` on first use (it imports this module at its end)."""

    def __getattr__(self, name: str) -> Any:
        from autoontology import cli

        return getattr(cli, name)


base = _Cli()

PREFIX = "/api"
#: Seconds `settings restart --wait` waits for the restart to finish.
RESTART_WAIT_S = 240.0
RESTART_POLL_S = 1.0
#: Rows of each diff section printed in text mode (``--json`` has them all).
DIFF_ROWS = 20


class ServiceDown(Exception):
    """The service does not answer (connection refused / connect timeout)."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


class CallError(Exception):
    """An API error answer: ``{"error": code, "detail": text, ...}`` with its status."""

    def __init__(self, status: int, error: str, detail: str, body: Any = None) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail
        self.body = body if isinstance(body, dict) else {"error": error, "detail": detail}


def _decode(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = body.get("detail")
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False, default=str)[:600]
            raise CallError(
                resp.status_code, str(body.get("error") or "invalid_request"), detail, body
            )
        raise CallError(resp.status_code, "http_error", resp.text[:300])
    return resp


def parse_sse(lines: Iterator[str]) -> Iterator[tuple[str, Any, str | None]]:
    """``(event, data, id)`` per SSE event; ``data`` is decoded JSON when it parses."""
    event, data, ev_id = "message", [], None
    for line in lines:
        if line == "":
            if data:
                raw = "\n".join(data)
                try:
                    value: Any = json.loads(raw)
                except ValueError:
                    value = raw
                yield event, value, ev_id
            event, data, ev_id = "message", [], None
            continue
        if line.startswith(":"):
            continue  # keep-alive comment
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
        elif name == "id":
            ev_id = value
    if data:
        raw = "\n".join(data)
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        yield event, value, ev_id


class _Caller:
    client: httpx.Client
    url: str

    def raw(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """The response of a successful call (an error answer raises :class:`CallError`)."""
        try:
            resp = self.client.request(method, PREFIX + path, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServiceDown(self.url) from exc
        except httpx.HTTPError as exc:
            raise CallError(0, "connection_error", type(exc).__name__) from exc
        _decode(resp)
        return resp

    def call(self, method: str, path: str, **kw: Any) -> Any:
        """The decoded JSON body of a successful call."""
        return self.raw(method, path, **kw).json()

    def events(self, method: str, path: str, **kw: Any) -> Iterator[tuple[str, Any, str | None]]:
        try:
            with self.client.stream(method, PREFIX + path, **kw) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _decode(resp)
                yield from parse_sse(resp.iter_lines())
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServiceDown(self.url) from exc
        except httpx.HTTPError as exc:
            raise CallError(0, "stream_broken", type(exc).__name__) from exc


def http_client(settings: Any) -> httpx.Client:
    """The HTTP client of :class:`ServiceClient` (tests replace this function)."""
    return httpx.Client(
        base_url=settings.service_url, timeout=httpx.Timeout(600.0, connect=5.0), trust_env=False
    )


class ServiceClient(_Caller):
    """Calls to the running service (local only: ``127.0.0.1:<port>``, no proxy)."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.url = settings.service_url
        self.client = http_client(settings)


def local_app(settings: Any) -> Any:
    """A FastAPI app with only the UI's ``/api`` router over *settings*' store."""
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from autoontology.web.api import Api, ApiError, build_router

    app = FastAPI()
    app.state.api = Api(settings)
    app.include_router(build_router(app.state.api))

    @app.exception_handler(ApiError)
    async def _api_error(_request: Any, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            {"error": exc.error, "detail": exc.detail, **exc.extra}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Any, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )[:600]
        return JSONResponse({"error": "invalid_request", "detail": detail}, status_code=400)

    return app


class LocalApi(_Caller):
    """The UI's ``/api`` route functions run in this process (the service may be down)."""

    def __init__(self, settings: Any) -> None:
        from fastapi.testclient import TestClient

        self.url = "local"
        self.client = TestClient(local_app(settings), base_url="http://127.0.0.1")


# --- shared helpers -----------------------------------------------------------------


def _json(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _json_mode() -> bool:
    """True when the running command (or its group) was given ``--json``."""
    try:  # typer >= 0.2x runs its own copy of click
        from typer._click.globals import get_current_context
    except ImportError:  # pragma: no cover - older typer on the real click
        from click import get_current_context

    ctx = get_current_context(silent=True)
    while ctx is not None:
        if ctx.params.get("as_json"):
            return True
        ctx = ctx.parent
    return False


def _error(
    error: str, detail: str, code: int, *, extra: dict[str, Any] | None = None, text: str = ""
) -> None:
    """Exit *code* with ``error: detail`` on stderr; under ``--json`` also the envelope
    ``{"error", "detail", ...}`` on stdout (every command, every failure)."""
    if _json_mode():
        _json({"error": error, "detail": detail, **(extra or {})})
    base._fail(text or f"{error}: {detail}", code=code)


def _call_error(exc: CallError) -> None:
    extra = {k: v for k, v in exc.body.items() if k not in ("error", "detail")}
    _error(exc.error, exc.detail, 1, extra={**extra, "status": exc.status})


def _service_down(exc: ServiceDown) -> None:
    text = msg("ops.service_down", url=exc.url)
    _error("service_down", text, 2, text=text)


def _guard(fn: Callable[[], Any], *, as_json: bool = False) -> Any:
    """*fn*'s result; a service that is down exits 2, an API error exits 1 (``--json``:
    the error envelope on stdout)."""
    try:
        return fn()
    except ServiceDown as exc:
        _service_down(exc)
    except CallError as exc:
        _call_error(exc)


def _run_seg(run_id: str) -> str:
    """*run_id* as one URL path segment (anything but a run id: exit 2)."""
    from autoontology.web.runs import RUN_ID_RE

    if not RUN_ID_RE.fullmatch(run_id):
        _error("invalid_run_id", f"invalid run id {run_id!r}", 2)
    return quote(run_id, safe="")


def _concept_seg(concept_id: str) -> str:
    from autoontology.web.api import CONCEPT_ID_RE

    if not CONCEPT_ID_RE.fullmatch(concept_id):
        _error("invalid_concept_id", f"invalid concept id {concept_id!r}", 2)
    return quote(concept_id, safe="")


def _export_path(path: str) -> str:
    """An export file path as quoted URL segments; refuses what could leave the folder
    or change the request (``.``/``..``, empty segments, absolute, ``\\``, ``?``, ``#``)."""
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or any(c in path for c in "\\?#\x00")
        or any(p in ("", ".", "..") for p in parts)
    ):
        _error("invalid_path", f"invalid export path {path!r}", 2)
    return "/".join(quote(p, safe="") for p in parts)


def _local(settings: Any | None = None) -> LocalApi:
    return LocalApi(settings or base._settings())


def _service(settings: Any | None = None) -> ServiceClient:
    return ServiceClient(settings or base._settings())


def _short(value: Any, size: int = 60) -> str:
    text = " ".join(str(value if value is not None else "-").split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _jsonable(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return "-" if value is None else str(value)


# --- ontology -----------------------------------------------------------------------

ontology_app = typer.Typer(help=msg("help.ontology"), invoke_without_command=True)


@ontology_app.callback()
def ontology_main(
    ctx: typer.Context,
    revision: int = typer.Option(None, "--revision", min=1, help=msg("help.opt.revision")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if as_json:
        params = {"revision": revision} if revision else {}
        _json(_guard(lambda: _local().call("GET", "/ontology", params=params), as_json=True))
        return
    base.ontology_summary(revision)


@ontology_app.command("revisions", help=msg("help.ontology.revisions"))
def ontology_revisions(
    limit: int = typer.Option(50, "--limit", min=1, max=500, help=msg("help.opt.limit")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    body = _guard(lambda: _local().call("GET", "/ontology/revisions", params={"limit": limit}))
    if as_json:
        _json(body)
        return
    rows = [
        (
            f"r{r['revision']}",
            (r["created_at"] or "-")[:19],
            r["run_id"],
            r.get("run_kind") or "-",
            str(r["counts"].get("classes", 0)),
            str(r["counts"].get("individuals", 0)),
            str(r["counts"].get("merged_concepts", 0)),
            ", ".join(s["name"] for s in r["sources"] if s.get("included")) or "-",
        )
        for r in body["revisions"]
    ]
    if not rows:
        typer.echo(msg("ops.no_snapshot"))
        return
    header = ("REVISION", "CREATED", "RUN", "KIND", "CLASSES", "INDIVIDUALS", "MERGED", "SOURCES")
    typer.echo(base._table(header, rows))


def _diff_section(title: str, part: dict[str, Any], label: Callable[[dict[str, Any]], str]) -> None:
    totals = part.get("totals", {})
    keys = [k for k in ("added", "removed", "changed") if k in part]
    typer.echo(f"{title}: " + ", ".join(f"{k} {totals.get(k, 0)}" for k in keys))
    for key in keys:
        sign = {"added": "+", "removed": "-", "changed": "~"}[key]
        rows = part.get(key) or []
        for entry in rows[:DIFF_ROWS]:
            typer.echo(f"  {sign} {label(entry)}")
        if totals.get(key, len(rows)) > DIFF_ROWS:
            typer.echo(f"  {sign} … {msg('ops.more', count=totals[key] - DIFF_ROWS)}")


@ontology_app.command("diff", help=msg("help.ontology.diff"))
def ontology_diff(
    from_: int = typer.Option(None, "--from", min=1, help=msg("help.ontology.diff.from")),
    to: int = typer.Option(None, "--to", min=1, help=msg("help.ontology.diff.to")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    params = {k: v for k, v in (("from", from_), ("to", to)) if v}
    body = _guard(lambda: _local().call("GET", "/ontology/diff", params=params), as_json=as_json)
    if as_json:
        _json(body)
        return
    typer.echo(f"r{body['from']} -> r{body['to']}" if body["from"] else f"(none) -> r{body['to']}")

    def node(e: dict[str, Any]) -> str:
        fields = f"  [{', '.join(e['fields'])}]" if e.get("fields") else ""
        kind = f" ({e['kind']})" if e.get("kind") else ""
        return f"{e.get('name') or e.get('id')}{kind}  {e.get('id', '')}{fields}"

    def axiom(e: dict[str, Any]) -> str:
        parts = [e.get(k) for k in ("subject_name", "kind", "object_name")]
        names = " ".join(str(p) for p in parts) if all(parts) else _jsonable(e)
        return names + ("  (accepted)" if e.get("accepted") else "  (proposed)")

    _diff_section("classes", body["classes"], node)
    _diff_section("axioms", body["axioms"], axiom)
    _diff_section(
        "merges",
        body["merges"],
        lambda e: f"{e.get('concept_name')} -> {e.get('term_name')}",
    )
    src = body.get("sources", {})
    typer.echo(
        f"sources: added {', '.join(src.get('added') or []) or '-'}; "
        f"removed {', '.join(src.get('removed') or []) or '-'}"
    )
    delta = body.get("counts_delta") or {}
    typer.echo(
        "counts: " + (" ".join(f"{k}{v:+d}" for k, v in sorted(delta.items())) or "unchanged")
    )


@ontology_app.command("concepts", help=msg("help.ontology.concepts"))
def ontology_concepts(
    q: str = typer.Option("", "-q", "--query", help=msg("help.ontology.concepts.q")),
    kind: str = typer.Option("", "--kind", help=msg("help.ontology.concepts.kind")),
    origin: str = typer.Option("", "--origin", help=msg("help.ontology.concepts.origin")),
    source: str = typer.Option("", "--source", help=msg("help.ontology.concepts.source")),
    revision: int = typer.Option(None, "--revision", min=1, help=msg("help.opt.revision")),
    page: int = typer.Option(1, "--page", min=1, help=msg("help.opt.page")),
    size: int = typer.Option(50, "--size", min=1, max=200, help=msg("help.opt.size")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    params: dict[str, Any] = {"q": q, "kind": kind, "origin": origin, "source": source}
    params = {k: v for k, v in params.items() if v}
    params.update(page=page, size=size, **({"revision": revision} if revision else {}))
    body = _guard(
        lambda: _local().call("GET", "/ontology/concepts", params=params), as_json=as_json
    )
    if as_json:
        _json(body)
        return
    rows = []
    for item in body["items"]:
        best = (item.get("alignment") or {}).get("best")
        align = f"{best['relation']} {best['label']}" if best else "-"
        if item.get("folded_into"):
            align = f"folded into {item['folded_into']['name']}"
        rows.append(
            (
                item["id"],
                _short(item["name"], 40),
                item["kind"],
                item["origin"],
                item.get("source") or "-",
                _short(align, 40),
                str(item.get("evidence_count", 0)),
            )
        )
    if rows:
        header = ("ID", "NAME", "KIND", "ORIGIN", "SOURCE", "ALIGNMENT", "EVIDENCE")
        typer.echo(base._table(header, rows))
    else:
        typer.echo(msg("ops.no_concepts"))
    typer.echo(
        msg(
            "ops.page",
            page=body["page"],
            pages=body["pages"],
            total=body["total"],
            revision=body["revision"],
        )
    )


@ontology_app.command("concept", help=msg("help.ontology.concept"))
def ontology_concept(
    concept_id: str = typer.Argument(..., help=msg("help.ontology.concept.id")),
    revision: int = typer.Option(None, "--revision", min=1, help=msg("help.opt.revision")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    params = {"revision": revision} if revision else {}
    seg = _concept_seg(concept_id)
    body = _guard(
        lambda: _local().call("GET", f"/ontology/concepts/{seg}", params=params),
        as_json=as_json,
    )
    if as_json:
        _json(body)
        return
    echo = typer.echo
    echo(f"{body['name']}  ({body['id']}, r{body['revision']})")
    echo(f"kind {body['kind']}  origin {body['origin']}  source {body.get('source') or '-'}")
    if body.get("folded_into"):
        echo(f"folded into: {body['folded_into']['name']} ({body['folded_into']['id']})")
    if body.get("enriched_by"):
        echo(f"enriched by: {body['enriched_by']}")
    for d in body.get("definitions") or ([body["definition"]] if body.get("definition") else []):
        echo(f"definition: {d}")
    if body.get("description"):
        echo(f"description: {body['description']}")
    if body.get("tables"):
        echo(f"tables: {', '.join(body['tables'])}")
    echo(f"synonyms: {', '.join(body.get('synonyms') or []) or '-'}")
    rejected = body.get("rejected_aliases") or []
    echo(
        "rejected aliases: "
        + (", ".join(str(r.get("alias") or r) for r in rejected) if rejected else "-")
    )
    if body.get("merged_concepts"):
        echo(f"merged concepts: {', '.join(body['merged_concepts'])}")
    links = body.get("alignments") or []
    echo(f"alignments ({len(links)}):")
    for a in links:
        conf = a.get("confidence")
        status = a.get("status") or ("accepted" if a.get("accepted") else "proposed")
        echo(
            f"  {a['relation']:<8} {a.get('label') or a['with']}  ({a['with']})  "
            f"{f'{conf:.2f}' if isinstance(conf, (int, float)) else '-'}  {status}"
        )
    for title, key in (("attributes", "attributes"), ("relations", "relations")):
        entries = body.get(key) or []
        if entries:
            echo(f"{title} ({len(entries)}):")
            for e in entries[:30]:
                echo(f"  {_short(e.get('label') or e.get('id'), 100)}")
    evidence = body.get("evidence") or []
    echo(f"evidence ({len(evidence)}):")
    for e in evidence:
        where = e.get("document") or e.get("path") or "-"
        page = f" p.{e['page']}" if e.get("page") else ""
        mark = "" if e.get("verified") else " (unverified)"
        echo(f'  "{_short(e.get("quote"), 160)}"  {where}{page}{mark}')
    decisions = body.get("decisions") or []
    echo(f"decisions ({len(decisions)}):")
    for d in decisions:
        rel = f" {d['relation']}" if d.get("relation") and d["relation"] != "none" else ""
        applied = f"applied r{d['applied_revision']}" if d.get("applied_revision") else "unapplied"
        note = f"  note: {d['note']}" if d.get("note") else ""
        echo(
            f"  {d.get('kind', '-')}: {d['decision']}{rel} by {d.get('decided_by') or '-'} "
            f"{(d.get('decided_at') or '')[:19]} ({applied}){note}"
        )


@ontology_app.command("graph", help=msg("help.ontology.graph"))
def ontology_graph(
    layer: str = typer.Option("all", "--layer", help=msg("help.ontology.graph.layer")),
    evidence: bool = typer.Option(False, "--evidence", help=msg("help.ontology.graph.evidence")),
    revision: int = typer.Option(None, "--revision", min=1, help=msg("help.opt.revision")),
    out: Path = typer.Option(None, "--out", help=msg("help.ontology.graph.out")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    params: dict[str, Any] = {"layer": layer, "evidence": evidence}
    if revision:
        params["revision"] = revision
    body = _guard(lambda: _local().call("GET", "/ontology/graph", params=params), as_json=as_json)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), "utf-8")
    if as_json:
        _json(body)
        return
    counts = body.get("counts") or {}
    typer.echo(
        f"r{body['revision']} layer {body['layer']}: {len(body['nodes'])} nodes, "
        f"{len(body['edges'])} edges"
        + (f" (truncated at {body['max_nodes']})" if body.get("truncated") else "")
    )
    if counts:
        typer.echo("counts: " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if out is not None:
        typer.echo(msg("ops.written", path=out))


# --- runs ---------------------------------------------------------------------------

runs_app = typer.Typer(help=msg("help.runs"), invoke_without_command=True)


@runs_app.callback()
def runs_main(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", min=1, max=200, help=msg("help.opt.limit")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if as_json:
        _json(_guard(lambda: _local().call("GET", "/runs", params={"limit": limit})))
        return
    base.runs_table(limit)


@runs_app.command("show", help=msg("help.runs.show"))
def runs_show(
    run_id: str = typer.Argument(..., help=msg("help.runs.id")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    seg = _run_seg(run_id)
    body = _guard(lambda: _service().call("GET", f"/runs/{seg}"), as_json=as_json)
    if as_json:
        _json(body)
        return
    typer.echo(
        f"run {body['id']}  {body['kind']}  {body['status']}"
        + (f"  source {body['source']}" if body.get("source") else "")
        + ("  (managed by the service)" if body.get("managed") else "")
    )
    typer.echo(
        f"started {body.get('started_at') or '-'}  finished {body.get('finished_at') or '-'}"
    )
    if body.get("error"):
        typer.echo(f"error: {body['error']}")
    if body.get("params"):
        typer.echo(f"params: {_jsonable(body['params'])}")
    rows = [
        (
            s["name"],
            s["status"],
            (s.get("started_at") or "-")[:19],
            (s.get("finished_at") or "-")[:19],
            _short(_jsonable(s.get("detail") or {}), 60) if s.get("detail") else "-",
        )
        for s in body.get("steps") or []
    ]
    if rows:
        typer.echo(base._table(("STEP", "STATUS", "STARTED", "FINISHED", "DETAIL"), rows))
    for label, bucket in (body.get("progress") or {}).items():
        typer.echo(f"{label}: " + " ".join(f"{k}={v}" for k, v in sorted(bucket.items())))


@runs_app.command("logs", help=msg("help.runs.logs"))
def runs_logs(
    run_id: str = typer.Argument(..., help=msg("help.runs.id")),
    follow: bool = typer.Option(False, "--follow", "-f", help=msg("help.runs.logs.follow")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.runs.logs.json")),
) -> None:
    from autoontology.web.api import mask
    from autoontology.web.runs import run_log_path

    _run_seg(run_id)
    if follow:
        status = None
        for event, data, _id in _guard(lambda: list_or_stream(run_id)):
            if as_json:
                typer.echo(json.dumps({"event": event, "data": data}, ensure_ascii=False))
            elif event == "log":
                typer.echo(data)
            elif event == "status":
                status = data.get("status") if isinstance(data, dict) else data
                typer.echo(f"[status] {status}", err=True)
            elif event == "step" and isinstance(data, dict):
                typer.echo(f"[step] {data.get('name')} {data.get('status')}", err=True)
        if status in ("failed", "partial", "cancelled"):
            raise typer.Exit(code=1)
        return
    settings = base._settings()
    if base._store(settings).get_run(run_id) is None:
        _error("unknown_run", f"unknown run {run_id!r}", 1)
    path = run_log_path(settings, run_id)
    if not path.is_file():
        from autoontology.engine.jobs import jobs_log_path

        text = msg("ops.no_run_log", run=run_id, log=jobs_log_path(settings))
        _error("no_run_log", text, 1, text=text)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = mask(line.rstrip("\n"))
            if as_json:
                typer.echo(json.dumps({"event": "log", "data": text}, ensure_ascii=False))
            else:
                typer.echo(text)


def list_or_stream(run_id: str) -> Iterator[tuple[str, Any, str | None]]:
    """The run's live events (``GET /api/runs/{id}/events``) until ``end``, consumed eagerly
    per event so a service error surfaces before any output."""
    stream = _service().events("GET", f"/runs/{_run_seg(run_id)}/events")
    first = next(stream, None)  # raises ServiceDown / CallError here, inside _guard

    def rest() -> Iterator[tuple[str, Any, str | None]]:
        if first is None:
            return
        yield first
        try:
            for item in stream:
                yield item
                if item[0] == "end":
                    return
        except CallError as exc:
            typer.echo(f"{exc.error}: {exc.detail}", err=True)
            raise typer.Exit(code=1) from None

    return rest()


@runs_app.command("cancel", help=msg("help.runs.cancel"))
def runs_cancel(
    run_id: str = typer.Argument(..., help=msg("help.runs.id")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    seg = _run_seg(run_id)
    body = _guard(lambda: _service().call("POST", f"/runs/{seg}/cancel"), as_json=as_json)
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.cancel_requested", run=run_id))


# --- sources freshness ----------------------------------------------------------------


def _reasons(row: dict[str, Any]) -> str:
    if row.get("error"):
        return f"error {row['error']}"
    parts = []
    for r in row.get("reasons") or []:
        examples = ", ".join(Path(x).name or x for x in r.get("examples") or [])
        parts.append(f"{r['kind']}({r.get('count', 0)})" + (f": {examples}" if examples else ""))
    return "; ".join(parts) or "-"


def sources_freshness(
    refresh: bool = typer.Option(False, "--refresh", help=msg("help.sources.refresh")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    body = _guard(
        lambda: _local().call("GET", "/sources/freshness", params={"refresh": True}),
        as_json=as_json,
    )
    if as_json:
        _json(body)
        return
    rows = [
        (
            r["name"],
            r["kind"],
            r["state"],
            "yes" if r.get("stale") else ("?" if r.get("stale") is None else "no"),
            (r.get("last_built_at") or "-")[:19],
            _short(_reasons(r), 90),
        )
        for r in body["sources"]
    ]
    if not rows:
        typer.echo(msg("ops.no_sources"))
        return
    typer.echo(base._table(("SOURCE", "KIND", "STATE", "STALE", "LAST BUILT", "REASONS"), rows))
    typer.echo(msg("ops.stale_count", stale=body["stale"], total=len(rows)))


# --- samples ------------------------------------------------------------------------

samples_app = typer.Typer(help=msg("help.samples"), no_args_is_help=True)


@samples_app.command("list", help=msg("help.samples.list"))
def samples_list(as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json"))) -> None:
    settings = base._settings()
    body = _guard(lambda: _local(settings).call("GET", "/samples"))
    if as_json:
        _json(body)
        return
    rows = []
    for s in body:
        expected = (s.get("durations") or {}).get(settings.chat_provider) or {}
        rows.append(
            (
                s["set"],
                ", ".join(f"{x['name']} ({x['kind']}, {x['files']} files)" for x in s["sources"]),
                "yes" if s.get("registered") else "no",
                expected.get("text") or "-",
            )
        )
    header = ("SET", "SOURCES", "REGISTERED", f"BUILD TIME ({settings.chat_provider})")
    typer.echo(base._table(header, rows))


@samples_app.command("load", help=msg("help.samples.load"))
def samples_load(
    set_name: str = typer.Argument(..., help=msg("help.samples.load.set")),
    replace: bool = typer.Option(False, "--replace", help=msg("help.samples.load.replace")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    payload = {"set": set_name, "replace": replace}
    try:
        try:  # the running service holds the workspace claim (no build starts meanwhile)
            body = _service().call("POST", "/samples/load", json=payload)
        except ServiceDown:
            body = _local().call("POST", "/samples/load", json=payload)
    except CallError as exc:
        extra = {k: v for k, v in exc.body.items() if k not in ("error", "detail")}
        extra["status"] = exc.status
        if exc.error == "sample_conflict":
            for c in exc.body.get("conflicts") or []:
                typer.echo(msg("demo.conflict", **c), err=True)
            detail = exc.detail.split('; send "replace"')[0]
            hint = msg("ops.sample_replace_hint")
            _error(
                "sample_conflict", detail, 2, extra=extra, text=f"sample_conflict: {detail}\n{hint}"
            )
        _error(exc.error, exc.detail, 2 if exc.status == 400 else 1, extra=extra)
    if as_json:
        _json(body)
        return
    for m in body.get("messages") or []:
        typer.echo(f"  {m}")
    rows = [
        (s["name"], s["kind"], s.get("action") or "-", s.get("locator") or "-")
        for s in body["sources"]
    ]
    typer.echo(base._table(("SOURCE", "KIND", "ACTION", "LOCATOR"), rows))
    typer.echo(msg("ops.samples_next"))


# --- exports ------------------------------------------------------------------------

exports_app = typer.Typer(help=msg("help.exports"), no_args_is_help=True)


def _rev_dir(rev: str) -> str:
    raw = rev.strip()
    if raw.lower().startswith("r") and raw[1:].isdigit():
        raw = raw[1:]
    if not raw.isdigit():
        _error("invalid_revision", msg("ops.bad_revision", value=rev), 2)
    from autoontology.engine.export_job import revision_dir_name

    return revision_dir_name(int(raw))


@exports_app.command("list", help=msg("help.exports.list"))
def exports_list(as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json"))) -> None:
    body = _guard(lambda: _local().call("GET", "/exports"))
    if as_json:
        _json(body)
        return
    rows = [
        (
            f"r{e['revision']}",
            e["dir"],
            e["created_at"][:19],
            str(len(e["files"])),
            str(sum(f["bytes"] for f in e["files"])),
            ", ".join(f"{d['name']}/ ({len(d['files'])})" for d in e["dirs"]) or "-",
        )
        for e in body["exports"]
    ]
    if not rows:
        typer.echo(msg("ops.no_exports"))
        return
    typer.echo(base._table(("REVISION", "DIR", "CREATED", "FILES", "BYTES", "FOLDERS"), rows))


@exports_app.command("show", help=msg("help.exports.show"))
def exports_show(
    rev: str = typer.Argument(..., help=msg("help.exports.rev")),
    path: str = typer.Argument(None, help=msg("help.exports.path")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    folder = _rev_dir(rev)
    segs = _export_path(path) if path is not None else ""
    api = _local()
    if path is not None:
        resp = _guard(
            lambda: api.raw("GET", f"/exports/{folder}/{segs}", params={"inline": True}),
            as_json=as_json,
        )
        text = resp.text
        if as_json:
            _json({"dir": folder, "path": path, "text": text})
            return
        typer.echo(text.rstrip("\n"))
        return
    exports = _guard(lambda: api.call("GET", "/exports"))["exports"]
    entry = next((e for e in exports if e["dir"] == folder), None)
    if entry is None:
        _error("unknown_export", f"no export {folder}", 1)
    if as_json:
        _json(entry)
        return
    typer.echo(f"{folder}  ({entry['created_at'][:19]}, {len(entry['files'])} files)")
    typer.echo(
        base._table(("FILE", "BYTES"), [(f["name"], str(f["bytes"])) for f in entry["files"]])
    )


@exports_app.command("get", help=msg("help.exports.get"))
def exports_get(
    rev: str = typer.Argument(..., help=msg("help.exports.rev")),
    path: str = typer.Argument(..., help=msg("help.exports.path")),
    out: str = typer.Option(..., "--out", help=msg("help.exports.out")),
) -> None:
    import os

    folder = _rev_dir(rev)
    segs = _export_path(path)
    target = Path(out).expanduser()
    if target.is_dir():
        target = target / Path(path).name
    elif out.endswith(("/", os.sep)):
        _error("no_such_directory", msg("ops.no_such_dir", path=target), 2)
    data = _guard(lambda: _local().raw("GET", f"/exports/{folder}/{segs}")).content
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    typer.echo(msg("ops.saved", path=target, bytes=len(data)))


# --- playground ---------------------------------------------------------------------

playground_app = typer.Typer(help=msg("help.playground"), no_args_is_help=True)


def _stream_or_fail(
    method: str, path: str, body: dict[str, Any]
) -> Iterator[tuple[str, Any, str | None]]:
    stream = _service().events(method, path, json=body)
    first = _guard(lambda: next(stream, None))
    if first is None:
        return iter(())

    def rest() -> Iterator[tuple[str, Any, str | None]]:
        yield first
        try:
            yield from stream
        except CallError as exc:
            typer.echo(f"{exc.error}: {exc.detail}", err=True)
            raise typer.Exit(code=1) from None

    return rest()


def _event_error(data: Any) -> str:
    if isinstance(data, dict):
        return f"{data.get('error') or 'error'}: {data.get('detail') or data.get('message') or ''}"
    return str(data)


@playground_app.command("chat", help=msg("help.playground.chat"))
def playground_chat(
    question: str = typer.Argument(..., help=msg("help.playground.question")),
    conversation: str = typer.Option(
        None, "--conversation", help=msg("help.playground.conversation")
    ),
    top_k: int = typer.Option(None, "--top-k", min=1, max=40, help=msg("help.playground.top_k")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.playground.json")),
) -> None:
    body: dict[str, Any] = {"question": question}
    if conversation:
        body["conversation_id"] = conversation
    if top_k:
        body["top_k"] = top_k
    refs: dict[str, dict[str, Any]] = {}
    failed = False
    answered = False
    for event, data, _id in _stream_or_fail("POST", "/playground/chat", body):
        if as_json:
            typer.echo(json.dumps({"event": event, "data": data}, ensure_ascii=False))
            failed = failed or event in ("error", "stream_truncated")
            continue
        if event == "status" and isinstance(data, dict):
            typer.echo(f"[{data.get('provider')} {data.get('model')}]", err=True)
        elif event == "retrieved" and isinstance(data, dict):
            refs = {e["ref"]: e for e in data.get("elements") or []}
            for warning in data.get("warnings") or []:
                typer.echo(f"warning: {warning}", err=True)
        elif event == "delta" and isinstance(data, dict):
            typer.echo(data.get("text", ""), nl=False)
            answered = True
        elif event == "citations" and isinstance(data, dict):
            if answered:
                typer.echo("")
            cited = data.get("citations") or []
            typer.echo(f"\ncitations ({len(cited)}):")
            for ref in cited:
                e = refs.get(ref, {})
                typer.echo(
                    f"  [{ref}] {e.get('label', '?')}  ({e.get('type', '-')} {e.get('id', '')})"
                )
            if data.get("unknown"):
                typer.echo(f"  unknown references: {', '.join(data['unknown'])}")
        elif event == "done" and isinstance(data, dict):
            cid = data.get("conversation_id")
            if cid:
                typer.echo(msg("ops.conversation", id=cid))
        elif event in ("error", "stream_truncated"):
            if answered:
                typer.echo("")
            typer.echo(_event_error(data) if event == "error" else "stream_truncated", err=True)
            failed = True
    if failed:
        raise typer.Exit(code=1)


@playground_app.command("sql", help=msg("help.playground.sql"))
def playground_sql(
    question: str = typer.Argument(..., help=msg("help.playground.question")),
    source: str = typer.Option(None, "--source", help=msg("help.playground.sql.source")),
    conversation: str = typer.Option(
        None, "--conversation", help=msg("help.playground.sql.conversation")
    ),
    as_json: bool = typer.Option(False, "--json", help=msg("help.playground.json")),
) -> None:
    body: dict[str, Any] = {"question": question}
    if source:
        body["source"] = source
    if conversation:
        body["conversation_id"] = conversation
    failed = False
    for event, data, _id in _stream_or_fail("POST", "/playground/sql", body):
        if as_json:
            typer.echo(json.dumps({"event": event, "data": data}, ensure_ascii=False))
            failed = failed or event == "error"
            continue
        d = data if isinstance(data, dict) else {}
        if event == "meta":
            for notice in d.get("notices") or []:
                typer.echo(f"notice: {notice}", err=True)
        elif event == "step" and d.get("phase") != "end":
            typer.echo(f"[{d.get('label') or d.get('node')}]", err=True)
        elif event == "sql":
            typer.echo(f"SQL: {d.get('sql', '')}")
        elif event == "result":
            answer = d.get("answer") or {}
            if answer.get("sql_code"):
                typer.echo(f"SQL: {answer['sql_code']}")
            rows = answer.get("sql_response_from_db")
            cols = answer.get("sql_columns") or []
            if isinstance(rows, list) and rows and cols:
                table = [
                    tuple(_short(v, 40) for v in (r if isinstance(r, (list, tuple)) else [r]))
                    for r in rows[:50]
                ]
                width = len(cols)
                table = [
                    tuple(list(r)[:width] + ["" for _ in range(width - len(r))]) for r in table
                ]
                typer.echo(base._table(tuple(cols), table))
            if answer.get("response"):
                typer.echo(str(answer["response"]))
        elif event == "error":
            typer.echo(_event_error(data), err=True)
            failed = True
    if failed:
        raise typer.Exit(code=1)


@playground_app.command("sparql", help=msg("help.playground.sparql"))
def playground_sparql(
    query: str = typer.Argument(None, help=msg("help.playground.sparql.query")),
    file: Path = typer.Option(None, "--file", help=msg("help.playground.sparql.file")),
    limit: int = typer.Option(500, "--limit", min=1, help=msg("help.playground.sparql.limit")),
    examples: bool = typer.Option(False, "--examples", help=msg("help.playground.sparql.examples")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    if examples:
        listing = _guard(lambda: _service().call("GET", "/playground/sparql/examples"))
        if as_json:
            _json(listing)
            return
        for example in listing.get("examples") or []:
            typer.echo(f"# {example.get('title') or example.get('label') or '-'}")
            typer.echo(str(example.get("query", "")).rstrip("\n") + "\n")
        return
    if (query is None) == (file is None):
        _error("invalid_query", msg("ops.sparql_one_of"), 2)
    if file is not None:
        try:
            query = file.read_text(encoding="utf-8")
        except OSError as exc:
            _error("unreadable_file", f"{file}: {exc.strerror or exc}", 2)
    body = _guard(
        lambda: _service().call(
            "POST", "/playground/sparql", json={"query": query, "limit": limit}
        ),
        as_json=as_json,
    )
    if as_json:
        _json(body)
        return
    if body["type"] == "ASK":
        typer.echo("true" if body.get("boolean") else "false")
    elif body.get("turtle") is not None and not body.get("columns"):
        typer.echo(body["turtle"].rstrip("\n"))
    else:
        rows = [tuple(_short(_jsonable(v), 60) for v in r) for r in body.get("rows") or []]
        if rows:
            typer.echo(base._table(tuple(body["columns"]), rows))
    typer.echo(
        f"{body['type']}: {body.get('total', 0)} result(s)"
        + (" (truncated)" if body.get("truncated") else "")
        + f"; r{body['revision']} {body.get('ttl')}",
        err=True,
    )


@playground_app.command("index", help=msg("help.playground.index"))
def playground_index(
    rebuild: bool = typer.Option(False, "--rebuild", help=msg("help.playground.index.rebuild")),
    as_json: bool = typer.Option(False, "--json", help=msg("help.opt.json")),
) -> None:
    method = "POST" if rebuild else "GET"
    body = _guard(lambda: _service().call(method, "/playground/index"), as_json=as_json)
    if as_json:
        _json(body)
        return
    meta = body.get("index") or {}
    typer.echo(
        f"scope {', '.join(body.get('scope') or []) or '-'}  r{body.get('revision')}  "
        + ("stale" if body.get("stale") else "current")
    )
    if meta:
        typer.echo("index: " + " ".join(f"{k}={_jsonable(v)}" for k, v in sorted(meta.items())))
    else:
        typer.echo(msg("ops.no_index"))
    for warning in body.get("warnings") or []:
        typer.echo(f"warning: {warning}", err=True)


# --- settings -----------------------------------------------------------------------

settings_app = typer.Typer(help=msg("help.settings"), no_args_is_help=True)


def _settings_state(settings: Any) -> Any:
    from fastapi import FastAPI

    from autoontology.web.api import Api
    from autoontology.web.settings_api import SettingsApi

    return SettingsApi(Api(settings), FastAPI())


def _settings_call(fn: Callable[[], Any], *, as_json: bool) -> Any:
    from autoontology.web.api import ApiError

    try:
        return fn()
    except ApiError as exc:
        _error(exc.error, exc.detail, 1, extra={**exc.extra, "status": exc.status})


def _settings_request(method: str, path: str, body: Any, local: Callable[[Any], Any]) -> Any:
    """The Settings page's call through the running service (its shell decides which keys
    are locked, its run manager whether a save may run), else *local* in this process."""
    payload = body.model_dump(mode="json", exclude_unset=True)
    if body.api_key is not None:  # over loopback in the JSON body, as the UI sends it
        payload["api_key"] = body.api_key.get_secret_value()
    try:
        return _service().call(method, path, json=payload)
    except ServiceDown:
        return _settings_call(lambda: local(_settings_state(base._settings())), as_json=False)
    except CallError as exc:
        _call_error(exc)


def _read_key(api_key_stdin: bool) -> str | None:
    """One line from stdin (a terminal: typed without echo); never from argv or the env."""
    if not api_key_stdin:
        return None
    if sys.stdin.isatty():
        import getpass

        key = getpass.getpass(msg("ops.key_prompt"))
    else:
        key = sys.stdin.readline()
    key = key.strip()
    if not key:
        _error("invalid_settings", msg("ops.key_empty"), 2)
    return key


def _settings_body(
    *,
    chat_provider: str | None,
    chat_model: str | None,
    embed_provider: str | None,
    embed_model: str | None,
    base_url: str | None,
    key: str | None,
    **extra: Any,
) -> Any:
    from pydantic import ValidationError

    from autoontology.web.settings_api import SettingsIn

    raw: dict[str, Any] = {
        "chat_provider": chat_provider,
        "chat_model": chat_model,
        "embed_provider": embed_provider,
        "embed_model": embed_model,
        "openai_base_url": base_url,
        **extra,
    }
    raw = {k: v for k, v in raw.items() if v is not None}
    if key:
        raw["api_key"] = key
    try:
        return SettingsIn.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"--{'.'.join(str(p) for p in e['loc']).replace('_', '-')}: {e['msg']}"
            for e in exc.errors()
        )
        _error("invalid_settings", problems, 2)


_CHAT_PROVIDER = typer.Option(None, "--chat-provider", help=msg("help.settings.chat_provider"))
_CHAT_MODEL = typer.Option(None, "--chat-model", help=msg("help.settings.chat_model"))
_EMBED_PROVIDER = typer.Option(None, "--embed-provider", help=msg("help.settings.embed_provider"))
_EMBED_MODEL = typer.Option(None, "--embed-model", help=msg("help.settings.embed_model"))
_BASE_URL = typer.Option(None, "--base-url", help=msg("help.settings.base_url"))
_KEY_STDIN = typer.Option(False, "--api-key-stdin", help=msg("help.settings.api_key_stdin"))
_JSON = typer.Option(False, "--json", help=msg("help.opt.json"))


@settings_app.command("show", help=msg("help.settings.show"))
def settings_show(as_json: bool = _JSON) -> None:
    settings = base._settings()
    note = None
    try:
        body = _service(settings).call("GET", "/settings")
    except ServiceDown:
        note = msg("ops.settings_offline")
        body = _settings_call(lambda: _settings_state(settings).get(), as_json=as_json)
        body["service_running"] = False
    except CallError as exc:
        _call_error(exc)
    else:
        body["service_running"] = True
    if as_json:
        _json(body)
        return
    echo = typer.echo
    chat, embed, running = body["chat"], body["embed"], body["running"]
    echo(f"chat       {chat['provider']}  {chat['model']}  (from {chat['origin']})")
    echo(f"embedding  {embed['provider']}  {embed['model']}  (from {embed['origin']})")
    echo(
        f"running    chat {running['chat_provider']} {running['chat_model']}; "
        f"embedding {running['embed_provider']} {running['embed_model']}"
    )
    openai = body.get("openai") or {}
    echo(
        f"openai     base URL {openai.get('base_url') or '-'}  "
        f"API key {'set' if openai.get('api_key_set') else 'not set'}"
    )
    rows = [
        (
            name,
            "yes" if c.get("found") else "no",
            "yes" if c.get("logged_in") else "no",
            c.get("model") or "-",
            c.get("login_command") or "-",
        )
        for name, c in (body.get("cli") or {}).items()
    ]
    if rows:
        echo(base._table(("CLI", "FOUND", "LOGGED IN", "MODEL", "LOGIN"), rows))
    oll = body.get("ollama") or {}
    echo(
        f"ollama     {oll.get('endpoint')}  "
        + (f"{len(oll.get('models') or [])} models" if oll.get("reachable") else "unreachable")
    )
    vec = body.get("vectors") or {}
    if vec:
        echo(f"vectors    {vec.get('rows', 0)} rows, dimension {vec.get('dimension') or '-'}")
    if body.get("locked_keys"):
        echo(f"set in the shell (locked): {', '.join(body['locked_keys'])}")
    if body.get("restart_required"):
        echo(msg("ops.restart_required"))
    if body.get("restart_in_progress"):
        echo(msg("ops.restart_in_progress"))
    if note:
        echo(note, err=True)


@settings_app.command("test", help=msg("help.settings.test"))
def settings_test(
    chat_provider: str = _CHAT_PROVIDER,
    chat_model: str = _CHAT_MODEL,
    embed_provider: str = _EMBED_PROVIDER,
    embed_model: str = _EMBED_MODEL,
    base_url: str = _BASE_URL,
    api_key_stdin: bool = _KEY_STDIN,
    use_saved_key: bool = typer.Option(
        True, "--use-saved-key/--no-saved-key", help=msg("help.settings.use_saved_key")
    ),
    as_json: bool = _JSON,
) -> None:
    import anyio

    key = _read_key(api_key_stdin)
    body = _settings_body(
        chat_provider=chat_provider,
        chat_model=chat_model,
        embed_provider=embed_provider,
        embed_model=embed_model,
        base_url=base_url,
        key=key,
        use_saved_key=use_saved_key,
    )
    result = _settings_request(
        "POST", "/settings/test", body, lambda state: anyio.run(state.test, body)
    )
    if as_json:
        _json(result)
    else:
        chat, embed = result["chat"], result["embed"]
        mark = "OK  " if chat.get("ok") else "FAIL"
        answer = chat.get("answer") or chat.get("error") or "-"
        typer.echo(
            f"chat       {mark} {chat['provider']} {chat['model']}  {answer!r}  "
            f"{chat.get('seconds')}s"
        )
        if not chat.get("ok") and chat.get("remedy"):
            typer.echo(f"           {chat['remedy']}")
        emark = "OK  " if embed.get("ok") else "FAIL"
        dim = embed.get("dimension")
        stored = embed.get("stored_dimension")
        typer.echo(
            f"embedding  {emark} {embed.get('provider')} {embed.get('model', '')}  "
            + (f"dimension {dim}" if dim else str(embed.get("error") or "-"))
            + (f" (stored vectors: {stored})" if stored else "")
        )
        if embed.get("mismatch_with_store"):
            typer.echo(msg("ops.dimension_mismatch", new=dim, old=stored), err=True)
    if not (result["chat"].get("ok") and result["embed"].get("ok")):
        raise typer.Exit(code=1)


@settings_app.command("set", help=msg("help.settings.set"))
def settings_set(
    chat_provider: str = _CHAT_PROVIDER,
    chat_model: str = _CHAT_MODEL,
    embed_provider: str = _EMBED_PROVIDER,
    embed_model: str = _EMBED_MODEL,
    base_url: str = _BASE_URL,
    api_key_stdin: bool = _KEY_STDIN,
    clear_api_key: bool = typer.Option(
        False, "--clear-api-key", help=msg("help.provider.use.clear_api_key")
    ),
    confirm_reingest: bool = typer.Option(
        False, "--confirm-reingest", help=msg("help.settings.confirm_reingest")
    ),
    as_json: bool = _JSON,
) -> None:
    if api_key_stdin and clear_api_key:
        _error("invalid_settings", msg("ops.key_and_clear"), 2)
    key = _read_key(api_key_stdin)
    body = _settings_body(
        chat_provider=chat_provider,
        chat_model=chat_model,
        embed_provider=embed_provider,
        embed_model=embed_model,
        base_url=base_url,
        key=key,
        clear_api_key=clear_api_key or None,
        confirm_reingest=confirm_reingest or None,
    )
    if not body.model_fields_set - {"confirm_reingest"}:
        _error("invalid_settings", msg("ops.settings_nothing"), 2)
    result = _settings_request("PUT", "/settings", body, lambda state: state.put(body))
    if as_json:
        _json(result)
        return
    from autoontology.config import env_file_path

    if result["written"]:
        typer.echo(
            msg("ops.settings_written", keys=", ".join(result["written"]), path=env_file_path())
        )
    else:
        typer.echo(msg("ops.settings_unchanged"))
    if result.get("backup"):
        typer.echo(msg("provider.backup", path=result["backup"]))
    if result.get("restart_required"):
        typer.echo(msg("ops.restart_hint"))


@settings_app.command("restart", help=msg("help.settings.restart"))
def settings_restart(
    wait: bool = typer.Option(False, "--wait", help=msg("help.settings.restart.wait")),
    timeout: float = typer.Option(
        RESTART_WAIT_S, "--timeout", min=1, help=msg("help.settings.restart.timeout")
    ),
    as_json: bool = _JSON,
) -> None:
    settings = base._settings()
    started = _guard(lambda: _service(settings).call("POST", "/settings/restart"), as_json=as_json)
    restart_id = started["restart_id"]
    if not wait:
        if as_json:
            _json(started)
        else:
            typer.echo(msg("ops.restart_started", id=restart_id))
        return
    deadline = time.monotonic() + timeout
    record: dict[str, Any] = {"id": restart_id, "state": "running"}
    while time.monotonic() < deadline:
        time.sleep(RESTART_POLL_S)
        try:
            record = _service(settings).call(
                "GET", f"/settings/restart/{quote(str(restart_id), safe='')}"
            )
        except (ServiceDown, CallError):  # the service is between stop and start
            continue
        if record.get("state") != "running":
            break
    if as_json:
        _json(record)
    elif record.get("state") == "done":
        typer.echo(msg("ops.restart_done", id=restart_id, pid=record.get("new_pid") or "-"))
    else:
        if record.get("log_tail"):
            typer.echo(record["log_tail"], err=True)
        typer.echo(
            msg(
                "ops.restart_not_done",
                id=restart_id,
                state=record.get("state"),
                log=settings.logs_dir,
            ),
            err=True,
        )
    if record.get("state") != "done":
        raise typer.Exit(code=1)


# --- registration -------------------------------------------------------------------


def register(app: typer.Typer, source_app: typer.Typer) -> None:
    """Add the parity commands to the CLI (called at the end of :mod:`autoontology.cli`)."""
    app.add_typer(ontology_app, name="ontology")
    app.add_typer(runs_app, name="runs")
    app.add_typer(samples_app, name="samples")
    app.add_typer(exports_app, name="exports")
    app.add_typer(playground_app, name="playground")
    app.add_typer(settings_app, name="settings")
    source_app.command("freshness", help=msg("help.sources.freshness"))(sources_freshness)
    # `sources freshness` (the UI's page name) is `source freshness`
    app.add_typer(source_app, name="sources", hidden=True)
