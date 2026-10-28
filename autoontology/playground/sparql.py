"""SPARQL console over the latest exported ``ontology.ttl`` (rdflib, in-process).

* The parsed Graph is cached per export run id (LRU, ``CACHE_SIZE``).
* Queries only: SELECT/ASK -> columns/rows (IRIs shortened with the graph's prefixes),
  CONSTRUCT/DESCRIBE -> Turtle text. SPARQL Update is never executed (``Graph.query``
  only parses queries). The parsed algebra is the authoritative check: a query whose
  algebra federates (``SERVICE``) or names a dataset (``FROM``/``FROM NAMED``) is refused,
  and rdflib's remote graph loading is off, so a query never reaches the network. A
  keyword pre-filter (after ``\\u`` escape expansion, skipping strings, IRIs and comments
  in one left-to-right scan) refuses ``SERVICE``/``FROM``/``LOAD`` early with a clearer
  message; it is a convenience, not the security boundary.
* Evaluation runs in a worker thread with a deadline (default 10 s): past it the request
  answers 408. The evaluation reads the graph through a deadline-checking view, so the
  thread aborts itself at the next triple it pulls and frees its slot (at most
  ``MAX_CONCURRENT`` evaluations ever run; 429 otherwise). Only an in-memory sort of the
  solutions already produced (ORDER BY/GROUP BY) can overrun the deadline.
* Results are bounded while evaluating: SELECT stops at ``limit + 1`` solutions (at most
  ``MAX_LIMIT`` = 500 rows, ``truncated`` without an exact total), CONSTRUCT/DESCRIBE stop
  at ``limit * 10`` triples.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import rdflib.plugins.sparql as rdflib_sparql
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parser import expandUnicodeEscapes

rdflib_sparql.SPARQL_LOAD_GRAPHS = False  # FROM <url> must never fetch anything

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_LIMIT = 200
MAX_QUERY_CHARS = 4000
MAX_LIMIT = 500
#: Evaluations running at once (abandoned timed-out ones included).
MAX_CONCURRENT = 2
#: Graphs kept parsed (by export run id).
CACHE_SIZE = 2
#: Keywords refused anywhere outside strings, IRIs and comments.
FORBIDDEN_KEYWORDS = ("SERVICE", "FROM", "LOAD")
#: One left-to-right scan: whichever of string / IRI / comment / keyword starts first
#: wins, so a ``#`` inside a string or a quote inside a comment cannot hide a keyword.
_TOKEN_RE = re.compile(
    r'(?P<skip>"""(?:[^"\\]|\\.|"(?!""))*"""'  # """long"""
    r"|'''(?:[^'\\]|\\.|'(?!''))*'''"  # '''long'''
    r'|"(?:[^"\\\n]|\\.)*"'  # "short"
    r"|'(?:[^'\\\n]|\\.)*'"  # 'short'
    r"|<[^<>\s]*>"  # IRI
    r"|#[^\n]*)"  # comment
    r"|(?<![\w?$:])(?P<kw>" + "|".join(FORBIDDEN_KEYWORDS) + r")(?![\w:-])",
    re.IGNORECASE,
)


class SparqlError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


EXAMPLES: list[dict[str, str]] = [
    {
        "id": "classes",
        "title": "Classes",
        "description": "Every owl:Class and its origin (structured Term / document concept)",
        "query": """SELECT ?class ?label ?source WHERE {
  ?class a owl:Class ;
         rdfs:label ?label ;
         ao:source ?source .
}
ORDER BY ?source ?label""",
    },
    {
        "id": "term-synonyms",
        "title": "Synonyms per Term",
        "description": "Structured Terms and their synonyms (including names aligned from documents)",
        "query": """SELECT ?term ?label (GROUP_CONCAT(DISTINCT ?name; separator=", ") AS ?synonyms)
WHERE {
  ?term a owl:Class ;
        ao:source "structured" ;
        rdfs:label ?label .
  OPTIONAL { ?term skos:altLabel ?alt }
  BIND(COALESCE(STR(?alt), "") AS ?name)
}
GROUP BY ?term ?label
ORDER BY ?label""",
    },
    {
        "id": "aligned-concepts",
        "title": "Aligned concepts",
        "description": "Document concepts linked to a Term by alignment (narrower/related) and the confidence",
        "query": """SELECT ?concept ?relation ?term ?confidence WHERE {
  ?st a rdf:Statement ;
      rdf:subject ?s ;
      rdf:predicate ?relation ;
      rdf:object ?o ;
      ao:source "alignment" .
  OPTIONAL { ?st ao:alignmentConfidence ?confidence }
  ?s rdfs:label ?sl . ?o rdfs:label ?ol .
  ?s ao:source ?ss .
  BIND(IF(?ss = "structured", ?ol, ?sl) AS ?concept)
  BIND(IF(?ss = "structured", ?sl, ?ol) AS ?term)
}
ORDER BY DESC(?confidence)""",
    },
    {
        "id": "relation-evidence",
        "title": "Relations and their evidence",
        "description": "Relations extracted from documents with the quote, document and page",
        "query": """SELECT ?relation ?label ?quote ?document ?page WHERE {
  ?relation a owl:ObjectProperty ;
            ao:source "document" ;
            rdfs:label ?label ;
            prov:wasDerivedFrom ?ev .
  ?ev ao:quote ?quote ;
      dcterms:source ?doc .
  ?doc dcterms:title ?document .
  OPTIONAL { ?ev ao:page ?page }
}
ORDER BY ?label""",
    },
    {
        "id": "business-rules",
        "title": "Business rules",
        "description": "Business rule (ao:BusinessRule) individuals and their description",
        "query": """SELECT ?rule ?label ?comment WHERE {
  ?rule a ao:BusinessRule ;
        rdfs:label ?label .
  OPTIONAL { ?rule rdfs:comment ?comment }
}
ORDER BY ?label""",
    },
    {
        "id": "concepts-per-document",
        "title": "Concepts per document",
        "description": "Classes and individuals linked by evidence (prov:wasDerivedFrom), per document",
        "query": """SELECT ?document (COUNT(DISTINCT ?node) AS ?concepts) WHERE {
  ?node prov:wasDerivedFrom ?ev .
  { ?node a owl:Class } UNION { ?node a owl:NamedIndividual }
  ?ev dcterms:source ?doc .
  ?doc dcterms:title ?document .
}
GROUP BY ?document
ORDER BY DESC(?concepts)""",
    },
    {
        "id": "property-domain-range",
        "title": "Property domain/range",
        "description": "Domain and range of object and datatype properties",
        "query": """SELECT ?property ?kind ?label ?domain ?range WHERE {
  ?property a ?kind ;
            rdfs:label ?label ;
            rdfs:domain ?domain ;
            rdfs:range ?range .
  VALUES ?kind { owl:ObjectProperty owl:DatatypeProperty }
}
ORDER BY ?kind ?label""",
    },
    {
        "id": "unaligned-concepts",
        "title": "Unaligned concepts",
        "description": "Document concept classes not linked to any structured Term",
        "query": """SELECT ?concept ?label WHERE {
  ?concept a owl:Class ;
           ao:source "document" ;
           rdfs:label ?label .
  FILTER NOT EXISTS {
    ?concept (rdfs:subClassOf|skos:related|^rdfs:subClassOf|^skos:related) ?term .
    ?term ao:source "structured" .
  }
}
ORDER BY ?label""",
    },
]


def _walk(node: Any, seen: set[int] | None = None) -> Any:
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v, seen)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v, seen)


def forbidden_keyword(query: str) -> str | None:
    """The first of ``SERVICE``/``FROM``/``LOAD`` used as a keyword in *query* (strings,
    IRIs, comments, variables and prefixed names do not count), else None. ``\\uXXXX``
    escapes are expanded first, as rdflib's parser does."""
    try:
        query = expandUnicodeEscapes(query)
    except ValueError:
        pass  # an invalid escape is the parser's sparql_syntax error
    for token in _TOKEN_RE.finditer(query):
        if token.group("kw"):
            return token.group("kw").upper()
    return None


def check_query(query: str, init_ns: dict[str, Any] | None = None) -> Any:
    """The prepared query (the graph's prefixes predeclared), or SparqlError(400) for a
    parse error or a forbidden keyword (SERVICE, FROM, LOAD)."""
    if len(query) > MAX_QUERY_CHARS:
        raise SparqlError(400, "sparql_too_long", f"query longer than {MAX_QUERY_CHARS} characters")
    keyword = forbidden_keyword(query)
    if keyword:
        raise SparqlError(
            400,
            "sparql_forbidden",
            f"{keyword} is not allowed: the console queries the exported "
            "ontology only (no federation, datasets or loading)",
        )
    try:
        prepared = prepareQuery(query, initNs=init_ns or {})
    except Exception as exc:  # noqa: BLE001 - pyparsing/rdflib raise various types
        raise SparqlError(400, "sparql_syntax", f"{type(exc).__name__}: {str(exc)[:400]}") from exc
    # defence in depth: the parsed algebra must not federate or name a dataset either
    for node in _walk(prepared.algebra):
        if getattr(node, "name", "") == "ServiceGraphPattern":
            raise SparqlError(400, "sparql_forbidden", "SERVICE (federated query) is not allowed")
    if prepared.algebra.get("datasetClause"):
        raise SparqlError(
            400,
            "sparql_forbidden",
            "FROM / FROM NAMED is not allowed: the console queries the exported ontology only",
        )
    return prepared


class _Cancelled(SparqlError):
    def __init__(self, timeout_s: float) -> None:
        super().__init__(408, "sparql_timeout", f"query did not finish within {timeout_s:.0f}s")


class _DeadlineGraph(Graph):
    """A view of *base* (same store, identifier and prefixes) whose triple access raises
    :class:`_Cancelled` once the deadline passed: rdflib's evaluation pulls every solution
    through ``triples``, so a runaway query aborts itself and frees its worker slot."""

    def __init__(self, base: Graph, deadline: float, timeout_s: float) -> None:
        super().__init__(
            store=base.store, identifier=base.identifier, namespace_manager=base.namespace_manager
        )
        self._deadline = deadline
        self._timeout_s = timeout_s

    def _check(self) -> None:
        if time.monotonic() > self._deadline:
            raise _Cancelled(self._timeout_s)

    def triples(self, triple, *args, **kwargs):  # type: ignore[override]
        self._check()
        for t in super().triples(triple, *args, **kwargs):
            self._check()
            yield t

    def triples_choices(self, triple, *args, **kwargs):  # type: ignore[override]
        self._check()
        for t in super().triples_choices(triple, *args, **kwargs):
            self._check()
            yield t


class SparqlRunner:
    """Runs queries in-process: the graph is parsed once per export (cached), each query
    is evaluated in a daemon thread under a deadline."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._graphs: OrderedDict[str, Graph] = OrderedDict()
        self._graphs_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    def graph(self, run_id: str, path: Path) -> Graph:
        with self._graphs_lock:
            cached = self._graphs.get(run_id)
            if cached is not None:
                self._graphs.move_to_end(run_id)
                return cached
        try:
            graph = load_graph(path)
        except Exception as exc:  # noqa: BLE001 - a broken export is the caller's 422
            raise SparqlError(
                422, "sparql_bad_export", f"cannot parse {path.name}: {type(exc).__name__}"
            ) from exc
        with self._graphs_lock:
            self._graphs[run_id] = graph
            while len(self._graphs) > CACHE_SIZE:
                self._graphs.popitem(last=False)
        return graph

    def run_file(
        self, run_id: str, path: Path, query: str, *, limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """*query* against the Turtle file *path* (parsed graph cached by *run_id*)."""
        if len(query) > MAX_QUERY_CHARS:
            raise SparqlError(
                400, "sparql_too_long", f"query longer than {MAX_QUERY_CHARS} characters"
            )
        limit = max(1, min(int(limit), MAX_LIMIT))
        graph = self.graph(run_id, path)
        prepared = check_query(query, prefixes(graph))  # fast: refuse before any thread
        if not self._slots.acquire(blocking=False):
            raise SparqlError(
                429, "sparql_busy", f"{MAX_CONCURRENT} queries are already running; retry shortly"
            )
        done = threading.Event()
        box: dict[str, Any] = {}
        view = _DeadlineGraph(graph, time.monotonic() + self.timeout_s, self.timeout_s)

        def work() -> None:
            try:
                box["result"] = execute(view, prepared, limit)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                box["error"] = exc
            finally:
                done.set()
                self._slots.release()  # only when the evaluation really ended

        try:
            threading.Thread(target=work, name="autoontology-sparql", daemon=True).start()
        except BaseException:
            self._slots.release()
            raise
        if not done.wait(self.timeout_s):
            raise _Cancelled(self.timeout_s)
        if "error" in box:
            raise box["error"]
        return {**box["result"], "triples": len(graph)}


def load_graph(path: Path) -> Graph:
    g = Graph()
    g.parse(path, format="turtle")
    return g


def execute(graph: Graph, query: Any, limit: int) -> dict[str, Any]:
    """Evaluate *query* (text or a prepared query), collecting at most ``limit + 1`` rows
    or ``limit * 10 + 1`` triples while evaluating: ``truncated`` without an exact total."""
    prepared = check_query(query, prefixes(graph)) if isinstance(query, str) else query
    kind = prepared.algebra.name
    try:
        if kind == "ConstructQuery":
            return _construct(graph, prepared, limit)
        if kind == "DescribeQuery":
            return _describe(graph, prepared, limit)
        return _format(graph, graph.query(prepared), limit)
    except SparqlError:
        raise
    except Exception as exc:  # noqa: BLE001 - evaluation errors are the user's query
        raise SparqlError(400, "sparql_error", f"{type(exc).__name__}: {str(exc)[:400]}") from exc


def _context(graph: Graph, prepared: Any) -> Any:
    from rdflib.plugins.sparql.sparql import QueryContext

    ctx = QueryContext(graph, initBindings={}, datasetClause=None)
    ctx.prologue = prepared.prologue
    return ctx


def _turtle_result(graph: Graph, kind: str, out: Graph, cap: int, over: bool) -> dict[str, Any]:
    return {
        "type": kind,
        "turtle": out.serialize(format="turtle"),
        "total": len(out),
        "truncated": over,
        "columns": [],
        "rows": [],
    }


def _construct(graph: Graph, prepared: Any, limit: int) -> dict[str, Any]:
    from rdflib.plugins.sparql.evaluate import _fillTemplate, evalPart

    query = prepared.algebra
    template = query.template or query.p.p.triples  # CONSTRUCT WHERE { ... }
    cap = limit * 10
    out = Graph(namespace_manager=graph.namespace_manager)
    over = False
    for solution in evalPart(_context(graph, prepared), query.p):
        for triple in _fillTemplate(template, solution):
            if triple in out:
                continue
            if len(out) >= cap:
                over = True
                break
            out.add(triple)
        if over:
            break
    return _turtle_result(graph, "CONSTRUCT", out, cap, over)


def _describe(graph: Graph, prepared: Any, limit: int) -> dict[str, Any]:
    from rdflib.plugins.sparql.evaluate import evalPart

    query = prepared.algebra
    cap = limit * 10
    resources: list[Any] = [iri for iri in query.PV if isinstance(iri, URIRef)]
    seen = set(resources)
    over = False
    if query.p is not None:
        for binding in evalPart(_context(graph, prepared), query.p):
            for value in binding.values():
                if value not in seen:
                    if len(resources) >= cap:
                        over = True
                        break
                    seen.add(value)
                    resources.append(value)
            if over:
                break
    out = Graph(namespace_manager=graph.namespace_manager)
    for resource in resources:
        part = graph.cbd(resource)
        for triple in part:
            if len(out) >= cap:
                over = True
                break
            out.add(triple)
        if over:
            break
    return _turtle_result(graph, "DESCRIBE", out, cap, over)


def prefixes(graph: Graph) -> dict[str, str]:
    return {p: str(ns) for p, ns in graph.namespaces() if p}


def short(graph: Graph, term: Any) -> Any:
    if term is None:
        return None
    if isinstance(term, URIRef):
        try:
            prefix, _ns, local = graph.namespace_manager.compute_qname(term, generate=False)
            return f"{prefix}:{local}" if prefix else str(term)
        except (ValueError, KeyError):
            return str(term)
    if isinstance(term, BNode):
        return f"_:{term}"
    if isinstance(term, Literal):
        value = term.toPython()
        return value if isinstance(value, (int, float, bool)) else str(term)
    return str(term)


def _format(graph: Graph, result: Any, limit: int) -> dict[str, Any]:
    if result.type == "ASK":
        return {
            "type": "ASK",
            "boolean": bool(result.askAnswer),
            "columns": ["result"],
            "rows": [[bool(result.askAnswer)]],
            "total": 1,
            "truncated": False,
        }
    columns = [str(v) for v in (result.vars or [])]
    rows: list[list[Any]] = []
    truncated = False
    for row in result:  # lazy: stop pulling solutions at limit + 1
        if len(rows) >= limit:
            truncated = True
            break
        rows.append([short(graph, row[i]) for i in range(len(columns))])
    # total = the rows returned; when truncated the real count is unknown (not computed)
    return {
        "type": "SELECT",
        "columns": columns,
        "rows": rows,
        "total": len(rows),
        "truncated": truncated,
    }
