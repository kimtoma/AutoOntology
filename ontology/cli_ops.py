# SPDX-License-Identifier: MIT
"""CLI parity with the web UI: every page's function as a command (``--json`` for scripts).

Two ways to reach the same handlers the UI calls:

* :class:`LocalApi` -- the ``/api`` router of :mod:`ontology.web.api` mounted in this
  process over the store (no service needed): ontology revisions / diff / concepts /
  concept / graph, sources freshness, samples, exports. The answers are the UI's, byte for
  byte, because they come from the same route functions.
* :class:`ServiceClient` -- HTTP to the running service (``settings.service_url``) for
  what only the service holds: run details and cancellation (its run manager), live run
  events, the playground (its index, conversations and LLM), the restart. Unreachable ->
  exit 2 with the ``up`` hint.

``settings show|test|set`` use :mod:`ontology.web.settings_api` (the Settings page's
own functions: origin rule, shell-override refusal, dimension guard); ``show`` asks the
service first so ``running``/``restart_required`` describe the running processes.

Projects: with ``--project``/``-p`` (or ``ONTOLOGY_PROJECT``) every project-scoped
call goes to ``/api/projects/<project>/...``; without it, to the ``/api/...`` aliases of
the default project. ``project list|show|create|rename|archive|unarchive`` manage them.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import typer

from ontology.i18n import msg

if TYPE_CHECKING:  # httpx is imported on first use: ``onto version``/``--help`` skip it
    import httpx


class _Cli:
    """``ontology.cli`` on first use (it imports this module at its end)."""

    def __getattr__(self, name: str) -> Any:
        from ontology import cli

        return getattr(cli, name)


base = _Cli()

PREFIX = "/api"
#: API paths without a project (never under ``/api/projects/<project>``).
INSTALLATION_PATHS = ("/settings", "/status", "/providers", "/projects")


def api_path(path: str, project: str | None) -> str:
    """The URL path of the API call *path* for *project* (None = the default project's
    alias): ``/api/projects/<project>/runs`` or ``/api/runs``; installation-wide calls
    (:data:`INSTALLATION_PATHS`) always ``/api/...``."""
    if project is None or path.split("?", 1)[0].startswith(INSTALLATION_PATHS):
        return PREFIX + path
    return f"{PREFIX}/projects/{quote(project, safe='')}{path}"


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
    #: the project the calls go to (None = the default project's ``/api`` aliases)
    project: str | None = None

    def raw(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """The response of a successful call (an error answer raises :class:`CallError`)."""
        import httpx

        try:
            resp = self.client.request(method, api_path(path, self.project), **kw)
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
        import httpx

        try:
            with self.client.stream(method, api_path(path, self.project), **kw) as resp:
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
    import httpx

    return httpx.Client(
        base_url=settings.service_url, timeout=httpx.Timeout(600.0, connect=5.0), trust_env=False
    )


class ServiceClient(_Caller):
    """Calls to the running service (local only: ``127.0.0.1:<port>``, no proxy)."""

    def __init__(self, settings: Any, project: str | None = None) -> None:
        from ontology.config import internal_headers

        self.settings = settings
        self.url = settings.service_url
        self.project = project
        self.client = http_client(settings)
        # accounts on: the service trusts this installation's local key (admin, ``cli``)
        headers = getattr(self.client, "headers", None)
        if headers is not None:
            headers.update(internal_headers(settings, base._as_user()))


def local_app(settings: Any) -> Any:
    """A FastAPI app with only the UI's ``/api`` router over *settings*' store."""
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from ontology.web.api import Api, ApiError, build_router

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

    def __init__(self, settings: Any, project: str | None = None) -> None:
        from fastapi.testclient import TestClient

        from ontology.config import INTERNAL_AS_HEADER

        self.url = "local"
        self.project = project
        self.client = TestClient(local_app(settings), base_url="http://127.0.0.1")
        if base._as_user():
            self.client.headers[INTERNAL_AS_HEADER] = base._as_user()


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
    key = f"ops.error.{exc.error}"  # an error with a message of its own (ko/en)
    text = msg(key) if msg(key) != key else ""
    _error(exc.error, exc.detail, 1, extra={**extra, "status": exc.status}, text=text)


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
    from ontology.web.runs import RUN_ID_RE

    if not RUN_ID_RE.fullmatch(run_id):
        _error("invalid_run_id", f"invalid run id {run_id!r}", 2)
    return quote(run_id, safe="")


def _concept_seg(concept_id: str) -> str:
    from ontology.web.api import CONCEPT_ID_RE

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
    return LocalApi(settings or base._settings(), project=base._project())


def _service(settings: Any | None = None) -> ServiceClient:
    return ServiceClient(settings or base._settings(), project=base._project())


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
    from ontology.web.api import mask
    from ontology.web.runs import run_log_path

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
        from ontology.engine.jobs import jobs_log_path

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
    from ontology.engine.export_job import revision_dir_name

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


@exports_app.command("zip", help=msg("help.exports.zip"))
def exports_zip(
    rev: str = typer.Argument(..., help=msg("help.exports.rev")),
    out: str = typer.Option(..., "--out", help=msg("help.exports.zip.out")),
) -> None:
    import os

    folder = _rev_dir(rev)
    target = Path(out).expanduser()
    if target.is_dir():
        target = target / f"ontology-{folder}.zip"
    elif out.endswith(("/", os.sep)):
        _error("no_such_directory", msg("ops.no_such_dir", path=target), 2)
    data = _guard(lambda: _local().raw("GET", f"/exports/{folder}.zip")).content
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


def _sql_parse(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def _sql_rows(value: Any, columns: list[str] | None = None) -> tuple[list[str], list[list]] | None:
    """Upstream's executed-SQL result (``sql_response_from_db``) as (columns, rows), or None
    when it is not tabular. Upstream stores ``QueryResponse.result``: a list of JSON-records
    strings (``['[{"n": 40}]']``); the value may also be one JSON string, records or
    positional rows. Records take their headers from their keys (first-seen order);
    *columns* names positional rows only when it has exactly their width (``col1..n``
    otherwise), since upstream's ``sql_columns`` are semantic-layer column ids. Mirrors
    ``tabulate`` in ``ui/src/lib/playground.ts``."""
    columns = list(columns or [])
    v = value
    if isinstance(v, str):
        ok, v = _sql_parse(v)
        if not ok:
            return None
    if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
        parts = [_sql_parse(x) for x in v]
        if all(ok and isinstance(p, list) for ok, p in parts):
            v = [row for _, p in parts for row in p]
    if not isinstance(v, list):
        return None
    if not v:
        return columns, []
    if all(isinstance(r, (list, tuple)) for r in v):
        width = max(len(r) for r in v)
        cols = columns if len(columns) == width else [f"col{i + 1}" for i in range(width)]
        return cols, [list(r) for r in v]
    if all(isinstance(r, dict) for r in v):
        cols: list[str] = []
        for r in v:
            cols.extend(k for k in r if k not in cols)
        return cols, [[r.get(c) for c in cols] for r in v]
    return (columns if len(columns) == 1 else ["value"]), [[x] for x in v]


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
    answer: dict[str, Any] | None = None
    kind = ""
    response_ko = ""
    for event, data, _id in _stream_or_fail("POST", "/playground/sql", body):
        if as_json:
            typer.echo(json.dumps({"event": event, "data": data}, ensure_ascii=False))
            failed = failed or event == "error"
            continue
        d = data if isinstance(data, dict) else {}
        if event == "meta":
            codes = d.get("notice_codes") or []
            for i, notice in enumerate(d.get("notices") or []):
                code = codes[i] if i < len(codes) else None
                key = f"ops.notice.{code}"
                text = msg(key) if code and msg(key) != key else notice
                typer.echo(f"notice: {text}", err=True)
        elif event == "step" and d.get("phase") != "end":
            node = str(d.get("node") or "")
            key = f"ops.sql.node.{node}"
            label = msg(key) if node and msg(key) != key else d.get("label") or node
            typer.echo(f"[{label}]", err=True)
            if node == "unconstructable_sql_response":
                kind = "unconstructable"
        elif event == "sql":
            typer.echo(f"SQL: {d.get('sql', '')}")
        elif event == "result":
            answer = d.get("answer") or {}
            kind = str(d.get("answer_kind") or kind) or (
                "sql" if answer.get("sql_code") else "information"
            )
            if answer.get("sql_code"):
                typer.echo(f"SQL: {answer['sql_code']}")
            # sql_columns are semantic-layer column ids: they only name positional rows
            # of exactly their width (see _sql_rows)
            shaped = _sql_rows(answer.get("sql_response_from_db"), answer.get("sql_columns") or [])
            if shaped and shaped[0] and shaped[1]:
                cols, rows = shaped
                table = [
                    tuple(
                        _short(v if v is not None else "", 40)
                        for v in (list(r)[: len(cols)] + [""] * (len(cols) - len(r)))
                    )
                    for r in rows[:50]
                ]
                typer.echo(base._table(tuple(cols), table))
        elif event == "translation":
            if d.get("phase") == "start":
                typer.echo(f"[{msg('ops.sql.translating')}]", err=True)
            else:
                response_ko = str((d.get("answer") or {}).get("response_ko") or "")
        elif event == "error":
            typer.echo(_event_error(data), err=True)
            failed = True
    # the answer last: a Korean translation (its own event) follows the result
    if answer is not None:
        if kind == "information":
            typer.echo(msg("ops.sql.no_sql"), err=True)
        elif kind == "unconstructable":
            typer.echo(msg("ops.sql.unconstructable"), err=True)
        text = response_ko or str(answer.get("response") or "")
        if text:
            typer.echo(text)
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

    from ontology.web.api import Api
    from ontology.web.settings_api import SettingsApi

    return SettingsApi(Api(settings), FastAPI())


def _settings_call(fn: Callable[[], Any], *, as_json: bool) -> Any:
    from ontology.web.api import ApiError

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

    from ontology.web.settings_api import SettingsIn

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
    from ontology.config import env_file_path

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


# --- outside agents -----------------------------------------------------------------

agent_app = typer.Typer(help=msg("help.agent"), no_args_is_help=True)
agent_tokens_app = typer.Typer(help=msg("help.agent.tokens"), no_args_is_help=True)
agent_app.add_typer(agent_tokens_app, name="tokens")


def _token_seg(token_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", token_id):
        _error("unknown_token", f"unknown token {token_id!r}", 2)
    return quote(token_id, safe="")


def _token_rows(tokens: list[dict[str, Any]]) -> str:
    rows = [
        (
            t["id"],
            t["name"],
            t["status"],
            f"r{t['revision_pin']}" if t["revision_pin"] else "latest",
            (t["expires_at"] or "-")[:19],
            (t["created_at"] or "-")[:19],
            (t["last_used_at"] or "-")[:19],
            _short(",".join(t.get("scopes") or ["read"]), 40),
        )
        for t in tokens
    ]
    header = ("ID", "NAME", "STATUS", "REVISION", "EXPIRES", "CREATED", "LAST USED", "SCOPES")
    return base._table(header, rows)


def _print_new_token(body: dict[str, Any]) -> None:
    typer.echo(_token_rows([body["record"]]))
    typer.echo("")
    typer.echo(body["token"])
    typer.echo("")
    typer.echo(msg("ops.agent_token_once"))
    typer.echo(msg("ops.agent_connect_hint"))


@agent_tokens_app.command("list", help=msg("help.agent.tokens.list"))
def agent_tokens_list(as_json: bool = _JSON) -> None:
    body = _guard(lambda: _local().call("GET", "/agent/tokens"))
    if as_json:
        _json(body)
        return
    if not body["tokens"]:
        typer.echo(msg("ops.agent_no_tokens"))
        return
    typer.echo(_token_rows(body["tokens"]))


@agent_tokens_app.command("create", help=msg("help.agent.tokens.create"))
def agent_tokens_create(
    name: str = typer.Argument(..., help=msg("help.agent.tokens.create.name")),
    revision: int = typer.Option(
        None, "--revision", min=1, metavar="N", help=msg("help.agent.tokens.create.revision")
    ),
    expires_days: int = typer.Option(
        None, "--expires-days", metavar="DAYS", help=msg("help.agent.tokens.create.expires")
    ),
    scopes: list[str] = typer.Option(
        None, "--scope", metavar="SCOPE", help=msg("help.agent.tokens.create.scope")
    ),
    allowed: list[str] = typer.Option(
        None, "--action", metavar="NAME", help=msg("help.agent.tokens.create.action")
    ),
    as_json: bool = _JSON,
) -> None:
    if expires_days is not None and expires_days not in (30, 90):
        _error("invalid_request", msg("ops.agent_bad_expiry", value=expires_days), 2)
    payload = {
        "name": name,
        "revision_pin": revision,
        "expires_days": expires_days,
        "scopes": list(scopes or ["read"]),
        "actions": list(allowed or []),
    }
    body = _guard(lambda: _local().call("POST", "/agent/tokens", json=payload))
    if as_json:
        _json(body)
        return
    _print_new_token(body)


@agent_tokens_app.command("rotate", help=msg("help.agent.tokens.rotate"))
def agent_tokens_rotate(
    token_id: str = typer.Argument(..., help=msg("help.agent.tokens.id")),
    as_json: bool = _JSON,
) -> None:
    seg = _token_seg(token_id)
    body = _guard(lambda: _local().call("POST", f"/agent/tokens/{seg}/rotate"))
    if as_json:
        _json(body)
        return
    _print_new_token(body)


@agent_tokens_app.command("revoke", help=msg("help.agent.tokens.revoke"))
def agent_tokens_revoke(
    token_id: str = typer.Argument(..., help=msg("help.agent.tokens.id")),
    as_json: bool = _JSON,
) -> None:
    seg = _token_seg(token_id)
    body = _guard(lambda: _local().call("DELETE", f"/agent/tokens/{seg}"))
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.agent_revoked", id=body["id"], name=body["name"]))


@agent_app.command("usage", help=msg("help.agent.usage"))
def agent_usage(
    days: int = typer.Option(None, "--days", min=1, max=365, help=msg("help.agent.usage.days")),
    as_json: bool = _JSON,
) -> None:
    params = {"days": days} if days else {}
    body = _guard(lambda: _local().call("GET", "/agent/usage", params=params))
    if as_json:
        _json(body)
        return
    if not body["total"]:
        typer.echo(msg("ops.agent_no_calls"))
        return
    typer.echo(msg("ops.agent_usage_total", total=body["total"], ok=body["ok"]))
    typer.echo("")
    rows = [(r["tool"], str(r["calls"]), str(r["ok"]), str(r["avg_ms"])) for r in body["by_tool"]]
    typer.echo(base._table(("TOOL", "CALLS", "OK", "AVG MS"), rows))
    typer.echo("")
    rows = [
        (
            t["token_id"],
            t["name"] or "-",
            str(t["calls"]),
            str(t["ok"]),
            str(t["by_surface"]["mcp"]),
            str(t["by_surface"]["rest"]),
            (t["last_call_at"] or "-")[:19],
        )
        for t in body["by_token"]
    ]
    typer.echo(base._table(("TOKEN", "NAME", "CALLS", "OK", "MCP", "REST", "LAST CALL"), rows))
    typer.echo("")
    typer.echo(base._table(("DAY", "CALLS"), [(d["day"], str(d["calls"])) for d in body["by_day"]]))


# --- projects -----------------------------------------------------------------------

project_app = typer.Typer(help=msg("help.project"), no_args_is_help=True)


def _project_seg(ref: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ref or ""):
        _error("unknown_project", f"unknown project {ref!r}", 2)
    return quote(ref, safe="")


def _project_write(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """A project change through the running service (its run manager sees the change),
    or in this process when the service is down."""
    kw = {"json": payload} if payload is not None else {}
    try:
        try:
            return _service().call(method, path, **kw)
        except ServiceDown:
            return _local().call(method, path, **kw)
    except CallError as exc:
        _call_error(exc)


def _project_rows(projects: list[dict[str, Any]], default: str | None = None) -> str:
    rows = [
        (
            ("* " if p["slug"] == default or p.get("default") else "  ") + p["slug"],
            p["name"],
            f"r{p['latest_revision']}" if p["latest_revision"] else "-",
            str(p["sources"]),
            (
                f"{p['last_build']['status']} {(p['last_build']['finished_at'] or '')[:19]}"
                if p.get("last_build")
                else "-"
            ).strip(),
            "archived" if p["archived"] else ("running" if p.get("active") else "-"),
        )
        for p in projects
    ]
    header = ("  PROJECT", "NAME", "REVISION", "SOURCES", "LAST BUILD", "STATE")
    return base._table(header, rows)


@project_app.command("list", help=msg("help.project.list"))
def project_list(
    archived: bool = typer.Option(
        True, "--archived/--no-archived", help=msg("help.project.archived")
    ),
    as_json: bool = _JSON,
) -> None:
    params = {"include_archived": archived}
    body = _guard(lambda: _local().call("GET", "/projects", params=params))
    if as_json:
        _json(body)
        return
    typer.echo(_project_rows(body["projects"], body.get("default")))


@project_app.command("show", help=msg("help.project.show"))
def project_show(
    ref: str = typer.Argument(None, metavar="PROJECT", help=msg("help.project.ref_default")),
    as_json: bool = _JSON,
) -> None:
    ref = ref or base._project()
    if ref is None:
        body = _guard(lambda: _local().call("GET", "/projects"))
        ref = body["default"]
    seg = _project_seg(ref)
    p = _guard(lambda: _local().call("GET", f"/projects/{seg}"))
    if as_json:
        _json(p)
        return
    typer.echo(f"{p['slug']}  ({p['name']}, {p['id']})")
    if p["description"]:
        typer.echo(f"  {p['description']}")
    typer.echo(msg("ops.project_revision", revision=p["latest_revision"] or "-"))
    typer.echo(msg("ops.project_sources", sources=p["sources"]))
    build = p.get("last_build")
    typer.echo(
        msg(
            "ops.project_last_build",
            build=f"{build['id']} {build['status']}" if build else "-",
        )
    )
    if p["archived"]:
        typer.echo(msg("ops.project_archived_at", at=p["archived_at"]))
    typer.echo(f"API: {p['api_base']}")


@project_app.command("create", help=msg("help.project.create"))
def project_create(
    slug: str = typer.Argument(..., help=msg("help.project.slug")),
    name: str = typer.Option("", "--name", help=msg("help.project.name")),
    description: str = typer.Option("", "--description", help=msg("help.project.description")),
    as_json: bool = _JSON,
) -> None:
    payload = {"slug": slug, "name": name, "description": description}
    body = _project_write("POST", "/projects", payload)
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.project_created", slug=body["slug"], name=body["name"]))


@project_app.command("rename", help=msg("help.project.rename"))
def project_rename(
    ref: str = typer.Argument(..., metavar="PROJECT", help=msg("help.project.ref")),
    name: str = typer.Option(None, "--name", help=msg("help.project.name")),
    slug: str = typer.Option(None, "--slug", help=msg("help.project.new_slug")),
    description: str = typer.Option(None, "--description", help=msg("help.project.description")),
    as_json: bool = _JSON,
) -> None:
    payload = {
        k: v
        for k, v in {"name": name, "slug": slug, "description": description}.items()
        if v is not None
    }
    if not payload:
        _error("invalid_request", msg("ops.project_nothing_to_change"), 2)
    body = _project_write("PATCH", f"/projects/{_project_seg(ref)}", payload)
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.project_renamed", slug=body["slug"], name=body["name"]))


@project_app.command("archive", help=msg("help.project.archive"))
def project_archive(
    ref: str = typer.Argument(..., metavar="PROJECT", help=msg("help.project.ref")),
    as_json: bool = _JSON,
) -> None:
    body = _project_write("POST", f"/projects/{_project_seg(ref)}/archive")
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.project_archived", slug=body["slug"]))


@project_app.command("unarchive", help=msg("help.project.unarchive"))
def project_unarchive(
    ref: str = typer.Argument(..., metavar="PROJECT", help=msg("help.project.ref")),
    as_json: bool = _JSON,
) -> None:
    body = _project_write("POST", f"/projects/{_project_seg(ref)}/unarchive")
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.project_unarchived", slug=body["slug"]))


# --- users (server-side admin) --------------------------------------------------------

user_app = typer.Typer(help=msg("help.user"), no_args_is_help=True)
#: Link purposes ``onto user link`` issues.
LINK_PURPOSES = ("magic", "reset", "invite")


def _accounts(settings: Any) -> Any:
    """The store's accounts. The CLI runs on the server: it is trusted as an admin and
    writes the store directly (the service need not run)."""
    from ontology.store.accounts import Accounts
    from ontology.store.db import Store

    return Accounts(Store.for_home(settings.home, default_project_slug=settings.workspace))


def _cli_record(accounts: Any, action: str, **kw: Any) -> None:
    """An audit entry by the CLI (``actor: cli``, ``via: cli``); ``--as <email>`` attributes
    it to that user."""
    detail = dict(kw.pop("detail", None) or {})
    detail["via"] = "cli"
    email = base._as_user()
    user = accounts.by_email(email) if email else None
    if user is not None:
        accounts.record(action, user_id=user.id, detail=detail, **kw)
    else:
        accounts.record(action, actor="cli", detail=detail, **kw)


def _account_error(exc: Any) -> None:
    code = 2 if exc.code in ("invalid_email", "weak_password", "invalid_purpose") else 1
    _error(exc.code, str(exc), code)


def _user_by_email(accounts: Any, email: str) -> Any:
    user = accounts.by_email(email)
    if user is None:
        _error("unknown_user", msg("ops.user_unknown", email=email.strip()), 1)
    return user


def _issue_link(settings: Any, accounts: Any, user: Any, purpose: str) -> dict[str, Any]:
    from ontology.web.auth import MagicLinkProvider

    provider = MagicLinkProvider()
    raw, expires = provider.issue(accounts, user, purpose)
    base_url = settings.public_url or settings.service_url
    return {
        "email": user.email,
        "purpose": purpose,
        "link": provider.link(base_url, raw, purpose),
        "expires_at": expires,
    }


def _print_link(settings: Any, issued: dict[str, Any]) -> None:
    typer.echo("")
    typer.echo(issued["link"])
    typer.echo("")
    typer.echo(msg("ops.user_link_once", expires=issued["expires_at"][:16].replace("T", " ")))
    if not settings.accounts_enabled:
        typer.echo(msg("ops.user_accounts_off"), err=True)


def _user_rows(users: list[Any]) -> str:
    rows = [
        (
            u.email,
            u.name or "-",
            "admin" if u.is_admin else "-",
            "active" if u.active else "disabled",
            "yes" if u.has_password else "no",
            (u.last_login_at or "-")[:19],
        )
        for u in users
    ]
    return base._table(("EMAIL", "NAME", "ADMIN", "STATE", "PASSWORD", "LAST LOGIN"), rows)


@user_app.command("create", help=msg("help.user.create"))
def user_create(
    email: str = typer.Argument(..., help=msg("help.user.email")),
    name: str = typer.Option("", "--name", help=msg("help.user.name")),
    admin: bool = typer.Option(False, "--admin", help=msg("help.user.admin")),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help=msg("help.user.password_stdin")
    ),
    as_json: bool = _JSON,
) -> None:
    from ontology.store.accounts import AccountError

    settings = base._settings()
    accounts = _accounts(settings)
    password = sys.stdin.readline().rstrip("\r\n") if password_stdin else None
    try:
        user = accounts.create_user(email, name=name, is_admin=admin, password=password)
    except AccountError as exc:
        _account_error(exc)
    _cli_record(accounts, "user.create", target=user.email, detail={"admin": user.is_admin})
    issued = None if password_stdin else _issue_link(settings, accounts, user, "invite")
    if as_json:
        _json({"user": user.to_dict(), **({"invite": issued} if issued else {})})
        return
    typer.echo(msg("ops.user_created", email=user.email, admin=" (admin)" if user.is_admin else ""))
    if issued:
        typer.echo(msg("ops.user_invite_hint"))
        _print_link(settings, issued)


@user_app.command("list", help=msg("help.user.list"))
def user_list(as_json: bool = _JSON) -> None:
    users = _accounts(base._settings()).list_users()
    if as_json:
        _json({"users": [u.to_dict() for u in users]})
        return
    if not users:
        typer.echo(msg("ops.user_none"))
        return
    typer.echo(_user_rows(users))


def _set_active(email: str, active: bool, as_json: bool) -> None:
    accounts = _accounts(base._settings())
    user = _user_by_email(accounts, email)
    user = accounts.enable(user.id) if active else accounts.disable(user.id)
    _cli_record(accounts, "user.enable" if active else "user.disable", target=user.email)
    if as_json:
        _json(user.to_dict())
        return
    key = "ops.user_enabled" if active else "ops.user_disabled"
    typer.echo(msg(key, email=user.email))


@user_app.command("disable", help=msg("help.user.disable"))
def user_disable(
    email: str = typer.Argument(..., help=msg("help.user.email")), as_json: bool = _JSON
) -> None:
    _set_active(email, False, as_json)


@user_app.command("enable", help=msg("help.user.enable"))
def user_enable(
    email: str = typer.Argument(..., help=msg("help.user.email")), as_json: bool = _JSON
) -> None:
    _set_active(email, True, as_json)


@user_app.command("link", help=msg("help.user.link"))
def user_link(
    email: str = typer.Argument(..., help=msg("help.user.email")),
    purpose: str = typer.Option("magic", "--purpose", help=msg("help.user.purpose")),
    as_json: bool = _JSON,
) -> None:
    if purpose not in LINK_PURPOSES:
        _error("invalid_purpose", msg("ops.user_bad_purpose", purpose=purpose), 2)
    settings = base._settings()
    accounts = _accounts(settings)
    user = _user_by_email(accounts, email)
    if not user.active:
        _error("user_disabled", msg("ops.user_is_disabled", email=user.email), 1)
    issued = _issue_link(settings, accounts, user, purpose)
    _cli_record(accounts, "user.link", target=user.email, detail={"purpose": purpose})
    if as_json:
        _json(issued)
        return
    _print_link(settings, issued)


# --- members and audit (server-side admin) ---------------------------------------------

member_app = typer.Typer(help=msg("help.member"), no_args_is_help=True)
MEMBER_ROLES = ("owner", "editor", "reviewer", "viewer")


def _member_project(accounts: Any) -> Any:
    from ontology.store.db import ProjectNotFound

    try:
        return accounts.store.project(base._project())
    except ProjectNotFound:
        _error("unknown_project", msg("ops.member_unknown_project", ref=base._project()), 1)


def _member_role_arg(role: str) -> str:
    if role not in MEMBER_ROLES:
        _error("invalid_role", msg("ops.member_bad_role", role=role), 2)
    return role


def _member_rows(rows: list[dict[str, Any]]) -> str:
    table = [
        (
            r["email"],
            r["name"] or "-",
            r["role"],
            "active" if r["active"] else "disabled",
            (r["member_since"] or "-")[:19],
        )
        for r in rows
    ]
    return base._table(("EMAIL", "NAME", "ROLE", "STATE", "SINCE"), table)


@member_app.command("list", help=msg("help.member.list"))
def member_list(as_json: bool = _JSON) -> None:
    accounts = _accounts(base._settings())
    view = _member_project(accounts)
    rows = accounts.list_members(view.id)
    if as_json:
        _json({"project": view.slug, "members": rows})
        return
    if not rows:
        typer.echo(msg("ops.member_none", slug=view.slug))
        return
    typer.echo(_member_rows(rows))


@member_app.command("add", help=msg("help.member.add"))
def member_add(
    email: str = typer.Argument(..., help=msg("help.user.email")),
    role: str = typer.Option("viewer", "--role", help=msg("help.member.role")),
    as_json: bool = _JSON,
) -> None:
    from ontology.store.accounts import AccountError

    settings = base._settings()
    accounts = _accounts(settings)
    view = _member_project(accounts)
    role = _member_role_arg(role)
    user = accounts.by_email(email)
    issued = None
    try:
        if user is None:
            user = accounts.create_user(email)
            _cli_record(accounts, "user.invite", target=user.email, project_id=view.id)
            issued = _issue_link(settings, accounts, user, "invite")
        accounts.add_member(view.id, user.id, role)
    except AccountError as exc:
        _account_error(exc)
    _cli_record(
        accounts, "member.add", target=user.email, project_id=view.id, detail={"role": role}
    )
    if as_json:
        _json(
            {
                "project": view.slug,
                "email": user.email,
                "role": role,
                **({"invite": issued} if issued else {}),
            }
        )
        return
    typer.echo(msg("ops.member_added", email=user.email, slug=view.slug, role=role))
    if issued:
        typer.echo(msg("ops.user_invite_hint"))
        _print_link(settings, issued)


@member_app.command("role", help=msg("help.member.set_role"))
def member_role(
    email: str = typer.Argument(..., help=msg("help.user.email")),
    role: str = typer.Argument(..., help=msg("help.member.role")),
    as_json: bool = _JSON,
) -> None:
    from ontology.store.accounts import AccountError

    accounts = _accounts(base._settings())
    view = _member_project(accounts)
    role = _member_role_arg(role)
    user = _user_by_email(accounts, email)
    before = accounts.role_of(view.id, user.id)
    try:
        accounts.set_role(view.id, user.id, role)
    except AccountError as exc:
        _account_error(exc)
    if before != role:
        _cli_record(
            accounts,
            "member.role",
            target=user.email,
            project_id=view.id,
            detail={"from": before, "to": role},
        )
    if as_json:
        _json({"project": view.slug, "email": user.email, "role": role})
        return
    typer.echo(msg("ops.member_role_set", email=user.email, slug=view.slug, role=role))


@member_app.command("remove", help=msg("help.member.remove"))
def member_remove(
    email: str = typer.Argument(..., help=msg("help.user.email")), as_json: bool = _JSON
) -> None:
    from ontology.store.accounts import AccountError

    accounts = _accounts(base._settings())
    view = _member_project(accounts)
    user = _user_by_email(accounts, email)
    before = accounts.role_of(view.id, user.id)
    try:
        accounts.remove_member(view.id, user.id)
    except AccountError as exc:
        _account_error(exc)
    _cli_record(
        accounts, "member.remove", target=user.email, project_id=view.id, detail={"role": before}
    )
    if as_json:
        _json({"project": view.slug, "removed": user.email})
        return
    typer.echo(msg("ops.member_removed", email=user.email, slug=view.slug))


def audit_cmd(
    project: str = typer.Option(None, "--project", help=msg("help.audit.project")),
    user: str = typer.Option(None, "--user", help=msg("help.audit.user")),
    action: str = typer.Option(None, "--action", help=msg("help.audit.action")),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help=msg("help.audit.limit")),
    as_json: bool = _JSON,
) -> None:
    from ontology.store.db import ProjectNotFound

    accounts = _accounts(base._settings())
    store = accounts.store
    project_id = None
    ref = project or base._project()
    if ref:
        try:
            project_id = store.project(ref).id
        except ProjectNotFound:
            _error("unknown_project", msg("ops.member_unknown_project", ref=ref), 1)
    user_id = None
    if user:
        user_id = _user_by_email(accounts, user).id
    entries = accounts.list_audit(
        project_id=project_id, user_id=user_id, action=action or None, limit=limit
    )
    slugs = {p.id: p.slug for p in store.list_projects(include_archived=True)}
    for entry in entries:
        entry["project"] = slugs.get(entry["project_id"]) if entry["project_id"] else None
    if as_json:
        _json({"entries": entries})
        return
    if not entries:
        typer.echo(msg("ops.audit_none"))
        return
    rows = [
        (
            e["at"][:19].replace("T", " "),
            _short(e["actor"], 32),
            e["action"],
            e["project"] or "-",
            _short(e["target"] or "-", 40),
            e["ip"] or "-",
        )
        for e in entries
    ]
    typer.echo(base._table(("AT (UTC)", "ACTOR", "ACTION", "PROJECT", "TARGET", "IP"), rows))


# --- registration -------------------------------------------------------------------


# --- objects ------------------------------------------------------------------------

object_app = typer.Typer(help=msg("help.object"), no_args_is_help=True)


def _object_type(name: str) -> str:
    """*name* (a type's name ``term-...``, id, label or table; the service resolves it)
    as one URL path segment."""
    if not name or len(name) > 200:
        _error("unknown_object_type", f"no object type {name[:80]!r}", 2)
    return quote(name.strip(), safe="")


def _type_rows(types: list[dict[str, Any]]) -> str:
    rows = [
        (
            t["name"],
            _short(t["label"], 30),
            t["status"] + (f" ({_short(t['broken_reason'], 40)})" if t["broken_reason"] else ""),
            _short(f"{t['source']}:{t['schema'] + '.' if t['schema'] else ''}{t['table']}", 40),
            t["key"] or "-",
            t["title_column"] or "-",
            "yes" if t["key_checked"] else "no",
        )
        for t in types
    ]
    return base._table(("NAME", "LABEL", "STATUS", "TABLE", "KEY", "TITLE", "CHECKED"), rows)


@object_app.command("types", help=msg("help.object.types"))
def object_types(
    status: str = typer.Option(None, "--status", help=msg("help.object.types.status")),
    as_json: bool = _JSON,
) -> None:
    params = {"status": status} if status else {}
    body = _guard(lambda: _local().call("GET", "/objects/types", params=params))
    if as_json:
        _json(body)
        return
    if not body["types"]:
        typer.echo(msg("ops.object_no_types"))
        return
    typer.echo(_type_rows(body["types"]))


@object_app.command("bind", help=msg("help.object.bind"))
def object_bind(
    name: str = typer.Argument(..., help=msg("help.object.type")),
    key: str = typer.Option(None, "--key", help=msg("help.object.bind.key")),
    title: str = typer.Option(None, "--title", help=msg("help.object.bind.title")),
    as_json: bool = _JSON,
) -> None:
    payload = {k: v for k, v in (("key", key), ("title", title)) if v is not None}
    seg = _object_type(name)
    body = _guard(lambda: _local().call("PUT", f"/objects/types/{seg}", json=payload))
    if as_json:
        _json(body)
        return
    typer.echo(_type_rows([body["type"]]))


@object_app.command("list", help=msg("help.object.list"))
def object_list(
    name: str = typer.Argument(..., help=msg("help.object.type")),
    q: str = typer.Option("", "-q", "--query", help=msg("help.object.list.q")),
    filters: list[str] = typer.Option(None, "--filter", help=msg("help.object.list.filter")),
    limit: int = typer.Option(50, "--limit", min=1, max=200, help=msg("help.opt.size")),
    offset: int = typer.Option(0, "--offset", min=0, help=msg("help.object.list.offset")),
    as_json: bool = _JSON,
) -> None:
    params: list[tuple[str, Any]] = [("limit", limit), ("offset", offset)]
    if q:
        params.append(("q", q))
    params += [("filter", f) for f in filters or []]
    seg = _object_type(name)
    body = _guard(lambda: _local().call("GET", f"/objects/{seg}", params=params))
    if as_json:
        _json(body)
        return
    rows = [
        (
            item["key"],
            _short(item["title"], 50),
            ", ".join(p + ("!" if m["conflict"] else "") for p, m in sorted(item["edits"].items()))
            or "-",
        )
        for item in body["items"]
    ]
    if rows:
        typer.echo(base._table(("KEY", "TITLE", "EDITED"), rows))
    else:
        typer.echo(msg("ops.object_none"))
    typer.echo(msg("ops.object_page", shown=len(rows), total=body["total"], offset=body["offset"]))


@object_app.command("get", help=msg("help.object.get"))
def object_get(
    name: str = typer.Argument(..., help=msg("help.object.type")),
    key: str = typer.Argument(..., help=msg("help.object.get.key")),
    as_json: bool = _JSON,
) -> None:
    seg = _object_type(name)
    body = _guard(lambda: _local().call("GET", f"/objects/{seg}/{quote(key, safe='')}"))
    if as_json:
        _json(body)
        return
    typer.echo(f"{body['type']['label']} {body['key']}: {body['title']}")
    for prop, value in body["values"].items():
        mark = body["edits"].get(prop)
        note = ""
        if mark:
            note = "  (" + ("operational" if mark["operational"] else "edited")
            note += (
                f", source: {_jsonable(mark['source_value'])}" if not mark["operational"] else ""
            )
            note += ", CONFLICT" if mark["conflict"] else ""
            note += ")"
        typer.echo(f"  {prop}: {_jsonable(value)}{note}")
    for link in body["links"]:
        typer.echo(
            f"  -> {link['name']} ({link['direction']}): {link['target_label']} "
            f"[{link['target_status']}]"
        )


# --- logic --------------------------------------------------------------------------

logic_app = typer.Typer(help=msg("help.logic"), no_args_is_help=True)
_LOGIC_FILE = typer.Option(..., "-f", "--file", help=msg("help.logic.file"))


def _logic_kind(kind: str) -> str:
    if kind not in ("function", "action", "workflow"):
        _error("invalid_kind", f"kind must be function, action or workflow, not {kind!r}", 2)
    return kind


def _logic_docs(path: Path) -> list[dict[str, Any]]:
    """The spec mappings of a file (unreadable: exit 2)."""
    from ontology.logic.specs import SpecError, load_documents

    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        _error("unreadable_file", f"cannot read {path}: {exc.strerror or exc}", 2)
    try:
        return load_documents(text)
    except SpecError as exc:
        _error("invalid_spec", str(exc), 2, extra={"errors": exc.errors})
    return []  # pragma: no cover - _error exits


def _spec_errors(errors: list[dict[str, Any]]) -> None:
    for e in errors:
        pos = f" (at {e['pos']})" if e.get("pos") is not None else ""
        typer.echo(f"  {e.get('path') or '-'}: {e['message']}{pos} [{e['code']}]", err=True)


def _def_rows(defs: list[dict[str, Any]]) -> str:
    rows = [
        (
            d["kind"],
            d["name"],
            _short(d["label"] or "-", 24),
            _short(d["object_type"] or "-", 20),
            f"v{d['version']}",
            d["status"] + (f" ({_short(d['broken_reason'], 40)})" if d["broken_reason"] else ""),
        )
        for d in defs
    ]
    return base._table(("KIND", "NAME", "LABEL", "OBJECT TYPE", "VER", "STATUS"), rows)


@logic_app.command("list", help=msg("help.logic.list"))
def logic_list(
    kind: str = typer.Option(None, "--kind", help=msg("help.logic.kind")),
    status: str = typer.Option(None, "--status", help=msg("help.logic.status")),
    as_json: bool = _JSON,
) -> None:
    params = {k: v for k, v in (("kind", kind), ("status", status)) if v}
    body = _guard(lambda: _local().call("GET", "/logic", params=params))
    if as_json:
        _json(body)
        return
    if not body["definitions"]:
        typer.echo(msg("ops.logic_none"))
        return
    typer.echo(_def_rows(body["definitions"]))


@logic_app.command("show", help=msg("help.logic.show"))
def logic_show(
    kind: str = typer.Argument(..., help=msg("help.logic.kind")),
    name: str = typer.Argument(..., help=msg("help.logic.name")),
    as_json: bool = _JSON,
) -> None:
    seg = f"/logic/{_logic_kind(kind)}/{quote(name, safe='')}"
    body = _guard(lambda: _local().call("GET", seg))
    if as_json:
        _json(body)
        return
    typer.echo(_def_rows([body["definition"]]))
    import yaml

    typer.echo(yaml.safe_dump(body["definition"]["spec"], allow_unicode=True, sort_keys=False))
    for h in body["history"][:20]:
        typer.echo(
            f"  v{h['version']} {h['status']:<8} {h['changed_at']} {h['changed_by']} {h['note']}"
        )


@logic_app.command("apply", help=msg("help.logic.apply"))
def logic_apply(file: Path = _LOGIC_FILE, as_json: bool = _JSON) -> None:
    docs = _logic_docs(file)
    saved: list[dict[str, Any]] = []
    for doc in docs:
        body = _guard(lambda doc=doc: _local().call("POST", "/logic", json={"spec": doc}))
        saved.append(body["definition"])
        if not as_json:
            d = body["definition"]
            typer.echo(
                msg(
                    "ops.logic_saved",
                    kind=d["kind"],
                    name=d["name"],
                    version=d["version"],
                    status=d["status"],
                )
            )
    if as_json:
        _json({"definitions": saved})


@logic_app.command("validate", help=msg("help.logic.validate"))
def logic_validate(file: Path = _LOGIC_FILE, as_json: bool = _JSON) -> None:
    docs = _logic_docs(file)
    body = _guard(lambda: _local().call("POST", "/logic/validate", json={"specs": docs}))
    if as_json:
        _json(body)
    else:
        for doc, r in zip(docs, body["results"], strict=False):
            kind, name = r["kind"] or doc.get("kind", "?"), r["name"] or doc.get("name", "?")
            if r["valid"]:
                typer.echo(msg("ops.logic_valid", kind=kind, name=name))
            else:
                typer.echo(msg("ops.logic_invalid", kind=kind, name=name, count=len(r["errors"])))
                _spec_errors(r["errors"])
    if not body["valid"]:
        raise typer.Exit(1)


def _params(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            _error("invalid_param", f"a parameter is name=value, got {pair!r}", 2)
        out[name] = value
    return out


@logic_app.command("test", help=msg("help.logic.test"))
def logic_test(
    name: str = typer.Argument(None, help=msg("help.logic.test.name")),
    file: Path = typer.Option(None, "-f", "--file", help=msg("help.logic.file")),
    keys: list[str] = typer.Option(None, "--key", help=msg("help.logic.test.key")),
    params: list[str] = typer.Option(None, "--param", help=msg("help.logic.test.param")),
    as_json: bool = _JSON,
) -> None:
    if (name is None) == (file is None) or not keys:
        _error("invalid_arguments", msg("ops.logic_test_args"), 2)
    payload: dict[str, Any] = {"keys": keys, "params": _params(params)}
    if file is not None:
        docs = _logic_docs(file)
        if len(docs) != 1:
            _error("invalid_arguments", "-f must hold exactly one function spec", 2)
        body = _guard(
            lambda: _local().call("POST", "/logic/test", json={**payload, "spec": docs[0]})
        )
    else:
        seg = f"/logic/functions/{quote(name, safe='')}/test"
        body = _guard(lambda: _local().call("POST", seg, json=payload))
    if as_json:
        _json(body)
    else:
        if body["errors"]:
            typer.echo(
                msg(
                    "ops.logic_invalid",
                    kind="function",
                    name=body["function"] or "?",
                    count=len(body["errors"]),
                )
            )
            _spec_errors(body["errors"])
        rows = [
            (
                r["key"],
                _short(r["title"], 40),
                _jsonable(r["value"])
                if not r.get("error")
                else f"ERROR {r['error']['code']}: {_short(r['error']['message'], 50)}",
            )
            for r in body["results"]
        ]
        if rows:
            typer.echo(base._table(("KEY", "TITLE", body.get("function") or "VALUE"), rows))
    if body["errors"]:
        raise typer.Exit(1)


def _logic_status(kind: str, name: str, status: str, as_json: bool) -> None:
    seg = f"/logic/{_logic_kind(kind)}/{quote(name, safe='')}/status"
    body = _guard(lambda: _local().call("POST", seg, json={"status": status}))
    if as_json:
        _json(body)
        return
    d = body["definition"]
    typer.echo(
        msg(
            "ops.logic_saved",
            kind=d["kind"],
            name=d["name"],
            version=d["version"],
            status=d["status"],
        )
    )


@logic_app.command("activate", help=msg("help.logic.activate"))
def logic_activate(
    kind: str = typer.Argument(..., help=msg("help.logic.kind")),
    name: str = typer.Argument(..., help=msg("help.logic.name")),
    as_json: bool = _JSON,
) -> None:
    _logic_status(kind, name, "active", as_json)


@logic_app.command("retire", help=msg("help.logic.retire"))
def logic_retire(
    kind: str = typer.Argument(..., help=msg("help.logic.kind")),
    name: str = typer.Argument(..., help=msg("help.logic.name")),
    as_json: bool = _JSON,
) -> None:
    _logic_status(kind, name, "retired", as_json)


#: Seconds between two polls of `logic extract --wait` against the service.
LOGIC_POLL_S = 1.0
_CANDIDATE_RE = re.compile(r"^(?:lg:)?(lc_[0-9a-f]{1,32})$")


def _candidate_seg(ref: str) -> str:
    m = _CANDIDATE_RE.fullmatch(ref or "")
    if m is None:
        _error("unknown_candidate", f"no logic candidate {ref[:80]!r}", 2)
    return "lg:" + m.group(1)


def _logic_counts(counts: dict[str, Any]) -> str:
    keys = ("concepts", "calls", "failed_calls", "candidates", "needs_edit", "stored", "ready")
    text = " ".join(f"{k}={counts.get(k, 0)}" for k in keys)
    dropped = counts.get("dropped") or {}
    if dropped:
        text += " dropped=" + ",".join(f"{k}:{v}" for k, v in sorted(dropped.items()))
    return text


def _logic_local_run() -> dict[str, Any]:
    """`logic extract` without a service: the job in this process (it waits)."""
    from ontology.engine import jobs
    from ontology.logic.extract import run_logic

    settings = base._settings()
    view = base._store(settings, write=True)
    jobs.setup_job_logging(settings, console=not _json_mode())
    try:
        result = run_logic(view, settings)
    except jobs.JobError as exc:
        _error("run_not_started", str(exc), 1)
    run = view.get_run(result.run_id)
    return {
        "id": result.run_id,
        "kind": "logic",
        "status": result.status,
        "error": result.error,
        "params": {k: v for k, v in (run.params if run else {}).items() if k != "pid"},
    }


@logic_app.command("extract", help=msg("help.logic.extract"))
def logic_extract(
    wait: bool = typer.Option(False, "--wait", help=msg("help.logic.extract.wait")),
    as_json: bool = _JSON,
) -> None:
    from ontology.store.db import ACTIVE_RUN_STATUSES

    run: dict[str, Any] | None = None
    try:  # the running service holds the run manager (one run per project, cancel)
        client = _service()
        run_id = client.call("POST", "/runs", json={"kind": "logic"})["run_id"]
        run = {"id": run_id, "kind": "logic", "status": "running"}
        while wait:
            run = client.call("GET", f"/runs/{_run_seg(run_id)}")
            if run["status"] not in ACTIVE_RUN_STATUSES:
                break
            time.sleep(LOGIC_POLL_S)
    except ServiceDown:
        run = None
    except CallError as exc:
        _call_error(exc)
    if run is None:
        run = _logic_local_run()
    if as_json:
        _json({"run": run})
    else:
        typer.echo(msg("ops.logic_run", id=run["id"], status=run["status"]))
        counts = (run.get("params") or {}).get("counts")
        if counts:
            typer.echo("  " + _logic_counts(counts))
        if run.get("error"):
            typer.echo(f"  {run['error']}")
        if run["status"] == "succeeded":
            typer.echo(msg("ops.logic_run_next"))
    if run["status"] == "failed":
        raise typer.Exit(1)


@logic_app.command("candidates", help=msg("help.logic.candidates"))
def logic_candidates(
    ready: bool = typer.Option(False, "--ready", help=msg("help.logic.candidates.ready")),
    status: str = typer.Option("pending", "--status", help=msg("help.logic.candidates.status")),
    as_json: bool = _JSON,
) -> None:
    params: dict[str, Any] = {"status": status}
    if ready:
        params["ready"] = "true"
    body = _guard(lambda: _local().call("GET", "/logic/candidates", params=params))
    if as_json:
        _json(body)
        return
    if not body["candidates"]:
        typer.echo(msg("ops.logic_no_candidates"))
        return
    rows = []
    for item in body["candidates"]:
        lg = item["logic"]
        decision = (item.get("decision") or {}).get("decision") or "pending"
        conf = item.get("confidence")
        rows.append(
            (
                lg["candidate_id"],
                lg["kind"],
                _short(lg["name"], 24),
                _short(lg["label"] or "-", 24),
                "ready" if lg["ready"] else f"{len(lg['errors'])} error(s)",
                "-" if conf is None else f"{conf:.2f}",
                decision,
                _short(lg["document"] or "-", 24),
            )
        )
    typer.echo(
        base._table(("ID", "KIND", "NAME", "LABEL", "STATE", "CONF", "DECISION", "DOCUMENT"), rows)
    )


def _candidate_decision(ref: str, payload: dict[str, Any], as_json: bool) -> None:
    item_id = _candidate_seg(ref)
    try:
        body = _local().call("POST", f"/review/{quote(item_id, safe=':')}/decision", json=payload)
    except CallError as exc:
        if not as_json and exc.body.get("errors"):
            _spec_errors(exc.body["errors"])
        _call_error(exc)
    except ServiceDown as exc:  # pragma: no cover - local calls need no service
        _service_down(exc)
    if as_json:
        _json(body)
        return
    d = body["decision"]
    typer.echo(msg("ops.logic_decided", id=item_id[3:], decision=d["decision"]))
    if d["decision"] == "accept":
        p = d["payload"]
        typer.echo(
            "  "
            + msg(
                "ops.logic_saved",
                kind=p.get("kind"),
                name=p.get("def_name"),
                version=p.get("version"),
                status="draft",
            )
        )


@logic_app.command("accept", help=msg("help.logic.accept"))
def logic_accept(
    candidate: str = typer.Argument(..., help=msg("help.logic.candidate")),
    file: Path = typer.Option(None, "-f", "--file", help=msg("help.logic.accept.file")),
    note: str = typer.Option("", "--note", help=msg("help.logic.note")),
    as_json: bool = _JSON,
) -> None:
    payload: dict[str, Any] = {"decision": "accept", "note": note}
    if file is not None:
        docs = _logic_docs(file)
        if len(docs) != 1:
            _error("invalid_spec", "give one spec in the file", 2)
        payload["spec"] = docs[0]
    _candidate_decision(candidate, payload, as_json)


@logic_app.command("reject", help=msg("help.logic.reject"))
def logic_reject(
    candidate: str = typer.Argument(..., help=msg("help.logic.candidate")),
    note: str = typer.Option("", "--note", help=msg("help.logic.note")),
    as_json: bool = _JSON,
) -> None:
    _candidate_decision(candidate, {"decision": "reject", "note": note}, as_json)


# --- actions ------------------------------------------------------------------------

action_app = typer.Typer(help=msg("help.action"), no_args_is_help=True)


def _action_seg(name: str) -> str:
    if not name or len(name) > 200:
        _error("unknown_action", f"no action {name[:80]!r}", 2)
    return quote(name, safe="")


def _run_rows(runs: list[dict[str, Any]]) -> str:
    rows = [
        (
            r["id"],
            _short(r["action"], 24),
            _short(r["object_key"], 20),
            r["status"] + (f" ({r['approval_role']})" if r["status"] == "pending_approval" else ""),
            r["via"],
            _short(r["requested_by"], 24),
            (r["created_at"] or "-")[:19],
        )
        for r in runs
    ]
    return base._table(("ID", "ACTION", "KEY", "STATUS", "VIA", "BY", "CREATED"), rows)


def _print_preview(p: dict[str, Any]) -> None:
    typer.echo(
        f"{p['label'] or p['action']} (v{p['version']}) -> {p['object_type_label']} {p['key']}: {p['title']}"
    )
    if p["params"]:
        typer.echo("  params: " + ", ".join(f"{k}={_jsonable(v)}" for k, v in p["params"].items()))
    for c in p["preconditions"]:
        mark = "ok" if c["ok"] else "FAILED"
        extra = f" ({c['error']['message']})" if c.get("error") else ""
        typer.echo(f"  [{mark}] {c['message']}{extra}")
    for e in p["effects"]:
        if e.get("error"):
            typer.echo(f"  {e['property']}: ERROR {e['error']['message']}")
        else:
            note = " (operational)" if e["operational"] else ""
            typer.echo(
                f"  {e['property']}: {_jsonable(e['old_value'])} -> {_jsonable(e['new_value'])}{note}"
            )
    a = p["approval"]
    if a["required"]:
        typer.echo(f"  approval: {a['role']} ({a['reason']})")
    for n in p["notify"]:
        typer.echo(f"  notify {n['recipients']} recipient(s): {_short(n['subject'], 60)}")
    if not p["permission"]["allowed"]:
        typer.echo(f"  needs the {p['permission']['min_role']} role")


@action_app.command("list", help=msg("help.action.list"))
def action_list(
    type_name: str = typer.Option(None, "--type", help=msg("help.action.type")),
    as_json: bool = _JSON,
) -> None:
    params = {"type": type_name} if type_name else {}
    body = _guard(lambda: _local().call("GET", "/actions", params=params))
    if as_json:
        _json(body)
        return
    if not body["actions"]:
        typer.echo(msg("ops.action_none"))
        return
    rows = [
        (
            a["name"],
            _short(a["label"], 24),
            _short(a["object_type_label"], 20),
            a["permission"]["min_role"],
            a["approval"].get("role", "none") + (" (when)" if a["approval"].get("when") else ""),
            a["agent"],
        )
        for a in body["actions"]
    ]
    typer.echo(base._table(("NAME", "LABEL", "OBJECT TYPE", "RUN", "APPROVAL", "AGENT"), rows))


@action_app.command("preview", help=msg("help.action.preview"))
def action_preview(
    name: str = typer.Argument(..., help=msg("help.action.name")),
    key: str = typer.Argument(..., help=msg("help.action.key")),
    params: list[str] = typer.Option(None, "--param", help=msg("help.action.param")),
    as_json: bool = _JSON,
) -> None:
    payload = {"key": key, "params": _params(params)}
    seg = _action_seg(name)
    body = _guard(lambda: _local().call("POST", f"/actions/{seg}/preview", json=payload))
    if as_json:
        _json(body)
        return
    _print_preview(body["preview"])


@action_app.command("run", help=msg("help.action.run"))
def action_run(
    name: str = typer.Argument(..., help=msg("help.action.name")),
    key: str = typer.Argument(..., help=msg("help.action.key")),
    params: list[str] = typer.Option(None, "--param", help=msg("help.action.param")),
    yes: bool = typer.Option(False, "--yes", "-y", help=msg("help.action.yes")),
    idempotency_key: str = typer.Option(
        None, "--idempotency-key", help=msg("help.action.idempotency")
    ),
    as_json: bool = _JSON,
) -> None:
    seg = _action_seg(name)
    payload: dict[str, Any] = {"key": key, "params": _params(params)}
    if not yes:
        if as_json:
            _error("invalid_arguments", msg("ops.action_json_needs_yes"), 2)
        shown = _guard(lambda: _local().call("POST", f"/actions/{seg}/preview", json=payload))
        _print_preview(shown["preview"])
        if not shown["preview"]["can_submit"]:
            _error("not_submittable", msg("ops.action_not_submittable", key=key), 1)
        if not typer.confirm(msg("ops.action_confirm", name=name, key=key)):
            raise typer.Exit(1)
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    body = _guard(lambda: _local().call("POST", f"/actions/{seg}/submit", json=payload))
    from ontology.logic import actions as runner
    from ontology.logic import workflows

    runner.wait_notifications()
    workflows.wait()  # on_action workflows this run started
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.action_run", id=body["run"]["id"], status=body["run"]["status"]))


@action_app.command("runs", help=msg("help.action.runs"))
def action_runs(
    status: str = typer.Option(None, "--status", help=msg("help.action.status")),
    action: str = typer.Option(None, "--action", help=msg("help.action.name")),
    key: str = typer.Option(None, "--key", help=msg("help.action.key")),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help=msg("help.opt.size")),
    as_json: bool = _JSON,
) -> None:
    params: dict[str, Any] = {"limit": limit}
    params.update({k: v for k, v in (("status", status), ("action", action), ("key", key)) if v})
    body = _guard(lambda: _local().call("GET", "/actions/runs", params=params))
    if as_json:
        _json(body)
        return
    if not body["runs"]:
        typer.echo(msg("ops.action_no_runs"))
        return
    typer.echo(_run_rows(body["runs"]))


def _action_run_seg(run_id: str) -> str:
    if not re.fullmatch(r"act_[0-9a-f]{1,32}", run_id):
        _error("unknown_run", f"no action run {run_id[:80]!r}", 2)
    return run_id


def _run_decision(run_id: str, verb: str, payload: dict[str, Any], as_json: bool) -> None:
    seg = _action_run_seg(run_id)
    body = _guard(lambda: _local().call("POST", f"/actions/runs/{seg}/{verb}", json=payload))
    from ontology.logic import actions as runner
    from ontology.logic import workflows

    runner.wait_notifications()
    workflows.wait()  # paused workflow objects resumed by this decision
    if as_json:
        _json(body)
        return
    run = body["run"]
    typer.echo(msg("ops.action_run", id=run["id"], status=run["status"]))
    if run.get("error"):
        typer.echo(f"  {run['error']}")


@action_app.command("approve", help=msg("help.action.approve"))
def action_approve(
    run_id: str = typer.Argument(..., help=msg("help.action.run_id")),
    as_json: bool = _JSON,
) -> None:
    _run_decision(run_id, "approve", {}, as_json)


@action_app.command("reject", help=msg("help.action.reject"))
def action_reject(
    run_id: str = typer.Argument(..., help=msg("help.action.run_id")),
    note: str = typer.Option("", "--note", help=msg("help.action.note")),
    as_json: bool = _JSON,
) -> None:
    _run_decision(run_id, "reject", {"note": note}, as_json)


@action_app.command("revert", help=msg("help.action.revert"))
def action_revert(
    run_id: str = typer.Argument(..., help=msg("help.action.run_id")),
    force: bool = typer.Option(False, "--force", help=msg("help.action.force")),
    as_json: bool = _JSON,
) -> None:
    _run_decision(run_id, "revert", {"force": force}, as_json)


# --- workflows ----------------------------------------------------------------------

workflow_app = typer.Typer(help=msg("help.workflow"), no_args_is_help=True)


def _workflow_seg(name: str) -> str:
    if not name or len(name) > 200:
        _error("unknown_workflow", f"no workflow {name[:80]!r}", 2)
    return quote(name, safe="")


def _workflow_run_seg(run_id: str) -> str:
    if not re.fullmatch(r"wfr_[0-9a-f]{1,32}", run_id):
        _error("unknown_run", f"no workflow run {run_id[:80]!r}", 2)
    return run_id


def _counts(summary: dict[str, Any]) -> str:
    keys = ("matched", "completed", "actions_applied", "pending_approval", "skipped", "failed")
    text = " ".join(f"{k}={summary.get(k, 0)}" for k in keys)
    if summary.get("already_pending"):
        text += f" already_pending={summary['already_pending']}"
    return text + (" truncated" if summary.get("truncated") else "")


def _workflow_run_rows(runs: list[dict[str, Any]]) -> str:
    rows = [
        (
            r["id"],
            _short(r["workflow"], 24),
            r["trigger"],
            r["status"],
            _counts(r.get("summary") or {}),
            (r["started_at"] or "-")[:19],
        )
        for r in runs
    ]
    return base._table(("ID", "WORKFLOW", "TRIGGER", "STATUS", "COUNTS", "STARTED"), rows)


@workflow_app.command("list", help=msg("help.workflow.list"))
def workflow_list(as_json: bool = _JSON) -> None:
    body = _guard(lambda: _local().call("GET", "/workflows"))
    if as_json:
        _json(body)
        return
    if not body["workflows"]:
        typer.echo(msg("ops.workflow_none"))
        return
    rows = []
    for w in body["workflows"]:
        trigger = w["trigger"]
        when = trigger.get("schedule") or (
            f"on {trigger['on_action']}" if trigger.get("on_action") else "manual"
        )
        last = w.get("last_run") or {}
        rows.append(
            (
                w["name"],
                _short(w["label"], 24),
                _short(w["object_type_label"] or w["object_type"], 20),
                when,
                w.get("next_slot") or "-",
                last.get("status") or "-",
            )
        )
    typer.echo(base._table(("NAME", "LABEL", "OBJECT TYPE", "TRIGGER", "NEXT", "LAST RUN"), rows))


@workflow_app.command("run", help=msg("help.workflow.run"))
def workflow_run(
    name: str = typer.Argument(..., help=msg("help.workflow.name")),
    wait: bool = typer.Option(False, "--wait", help=msg("help.workflow.wait")),
    as_json: bool = _JSON,
) -> None:
    seg = _workflow_seg(name)
    path = f"/workflows/{seg}/run"
    from ontology.logic import actions as runner
    from ontology.logic import workflows

    def call() -> Any:
        if not wait:
            try:  # the running service processes the objects in the background
                return _service().call("POST", path, json={"wait": False})
            except ServiceDown:
                pass
        return _local().call("POST", path, json={"wait": True})

    body = _guard(call)
    workflows.wait()
    runner.wait_notifications()
    if as_json:
        _json(body)
        return
    run = body["run"]
    typer.echo(msg("ops.workflow_run", id=run["id"], status=run["status"]))
    typer.echo("  " + _counts(run.get("summary") or {}))
    if run.get("error"):
        typer.echo(f"  {run['error']}")


@workflow_app.command("runs", help=msg("help.workflow.runs"))
def workflow_runs(
    status: str = typer.Option(None, "--status", help=msg("help.workflow.status")),
    workflow: str = typer.Option(None, "--workflow", help=msg("help.workflow.name")),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help=msg("help.opt.size")),
    as_json: bool = _JSON,
) -> None:
    params: dict[str, Any] = {"limit": limit}
    params.update({k: v for k, v in (("status", status), ("workflow", workflow)) if v})
    body = _guard(lambda: _local().call("GET", "/workflows/runs", params=params))
    if as_json:
        _json(body)
        return
    if not body["runs"]:
        typer.echo(msg("ops.workflow_no_runs"))
        return
    typer.echo(_workflow_run_rows(body["runs"]))


@workflow_app.command("show", help=msg("help.workflow.show"))
def workflow_show(
    run_id: str = typer.Argument(..., help=msg("help.workflow.run_id")),
    as_json: bool = _JSON,
) -> None:
    seg = _workflow_run_seg(run_id)
    body = _guard(lambda: _local().call("GET", f"/workflows/runs/{seg}"))
    if as_json:
        _json(body)
        return
    run = body["run"]
    typer.echo(
        f"{run['id']} {run['workflow']} (v{run['workflow_version']}) {run['trigger']} "
        f"{run['fire_slot']}: {run['status']}"
    )
    typer.echo("  " + _counts(run["summary"]))
    if run.get("error"):
        typer.echo(f"  {run['error']}")
    for o in run["objects"]:
        reason = f" ({_short(o['reason'], 60)})" if o.get("reason") else ""
        typer.echo(f"  {_short(o['key'], 24)}: {o['status']}{reason}")
    if run["action_runs"]:
        typer.echo(_run_rows(run["action_runs"]))


@workflow_app.command("cancel", help=msg("help.workflow.cancel"))
def workflow_cancel(
    run_id: str = typer.Argument(..., help=msg("help.workflow.run_id")),
    as_json: bool = _JSON,
) -> None:
    seg = _workflow_run_seg(run_id)
    body = _project_write("POST", f"/workflows/runs/{seg}/cancel")
    if as_json:
        _json(body)
        return
    typer.echo(msg("ops.workflow_run", id=body["run"]["id"], status=body["run"]["status"]))


# --- skill --------------------------------------------------------------------------

skill_app = typer.Typer(help=msg("help.skill"), no_args_is_help=True)


@skill_app.command("export", help=msg("help.skill.export"))
def skill_export(
    out: str = typer.Option(None, "-o", "--out", help=msg("help.skill.export.out")),
) -> None:
    import os
    import re as _re

    resp = _guard(lambda: _local().raw("GET", "/logic/skill.zip"))
    disposition = resp.headers.get("content-disposition", "")
    found = _re.search(r'filename="([A-Za-z0-9._-]+)"', disposition)
    default = found.group(1) if found else "ontology-skill.zip"
    target = Path(out).expanduser() if out else Path(default)
    if out and target.is_dir():
        target = target / default
    elif out and out.endswith(("/", os.sep)):
        _error("no_such_directory", msg("ops.no_such_dir", path=target), 2)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resp.content)
    typer.echo(msg("ops.saved", path=target, bytes=len(resp.content)))


def register(app: typer.Typer, source_app: typer.Typer) -> None:
    """Add the parity commands to the CLI (called at the end of :mod:`ontology.cli`)."""
    app.add_typer(ontology_app, name="ontology")
    app.add_typer(runs_app, name="runs")
    app.add_typer(samples_app, name="samples")
    app.add_typer(exports_app, name="exports")
    app.add_typer(playground_app, name="playground")
    app.add_typer(settings_app, name="settings")
    app.add_typer(agent_app, name="agent")
    app.add_typer(project_app, name="project")
    app.add_typer(user_app, name="user")
    app.add_typer(member_app, name="member")
    app.add_typer(object_app, name="object")
    app.add_typer(logic_app, name="logic")
    app.add_typer(action_app, name="action")
    app.add_typer(workflow_app, name="workflow")
    app.add_typer(skill_app, name="skill")
    app.command("audit", help=msg("help.audit"))(audit_cmd)
    source_app.command("freshness", help=msg("help.sources.freshness"))(sources_freshness)
    # `sources freshness` (the UI's page name) is `source freshness`
    app.add_typer(source_app, name="sources", hidden=True)
