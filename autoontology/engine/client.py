"""HTTP client for the upstream auto-ontology backend (``/api/...`` on :3101)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

USER_HEADER = "x-auto-ontology-user-id"
USER_ID = "autoontology"
DEFAULT_TIMEOUT_S = 60.0
#: Exports render the whole model server-side; give them more room.
EXPORT_TIMEOUT_S = 300.0


class BackendError(RuntimeError):
    """Non-2xx answer (``status`` set) or no answer at all (``status`` is None)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str = "",
        method: str = "",
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.method = method
        self.url = url


class BackendUnavailable(BackendError):
    pass


class BackendClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={USER_HEADER: USER_ID},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BackendClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise BackendUnavailable(
                f"upstream backend not reachable at {self.base_url} ({type(exc).__name__}); "
                "start it with `uv run autoontology up`",
                method=method,
                url=f"{self.base_url}{path}",
            ) from exc
        if resp.is_success:
            return resp
        body = resp.text[:2000]
        raise BackendError(
            f"{method} {path} -> HTTP {resp.status_code}: {body[:300]}",
            status=resp.status_code,
            body=body,
            method=method,
            url=str(resp.request.url),
        )

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()

    # --- health / connections -------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/api/health/live")

    def create_connection(self, connection: dict[str, Any]) -> dict[str, Any]:
        return self._json("POST", "/api/connections", json={"connection": connection})["data"]

    def update_connection(self, database_name: str, connection: dict[str, Any]) -> dict[str, Any]:
        path = f"/api/connections/{quote(database_name, safe='')}"
        return self._json("PUT", path, json={"connection": connection})["data"]

    def list_connections(self) -> list[dict[str, Any]]:
        return self._json("GET", "/api/connections")["data"]

    def connection_exists(self, database_name: str) -> bool:
        key = database_name.casefold()
        return any(
            str(c.get("database_name", "")).casefold() == key for c in self.list_connections()
        )

    def delete_connection(self, database_name: str) -> dict[str, Any]:
        path = f"/api/connections/{quote(database_name, safe='')}"
        return self._json("DELETE", path)["data"]

    # --- catalog / semantic layer ----------------------------------------------

    def list_databases(self) -> list[dict[str, Any]]:
        return self._json("GET", "/api/datasources/dbs")["data"]

    def database_id_by_name(self, name: str) -> str | None:
        key = name.casefold()
        for db in self.list_databases():
            if str(db.get("name", "")).casefold() == key:
                return str(db["id"])
        return None

    def list_terms(self, **params: Any) -> dict[str, Any]:
        """``{"terms": [...], "total", "column_attribute_counts", "sql_attribute_counts",
        "related_counts"}``; params: query, skip, limit (no limit = every Term)."""
        return self._json(
            "GET", "/api/terms", params={k: v for k, v in params.items() if v is not None}
        )

    def get_term(self, term_id: str) -> dict[str, Any]:
        """One Term with ``synonyms``, ``tables`` (REPRESENTS) and ``related_terms``."""
        return self._json("GET", f"/api/terms/{quote(term_id, safe='')}")["data"]

    def term_column_attributes(self, term_id: str) -> list[dict[str, Any]]:
        """ColumnAttributes of a Term with ``source_column``, ``datatype``,
        ``primary_column`` and ``referenced_columns`` (the SEMANTIC_FK sources)."""
        path = f"/api/terms/{quote(term_id, safe='')}/column-attributes"
        return self._json("GET", path)["data"]

    def schemas(self, database_id: str) -> list[dict[str, Any]]:
        """``[{"id", "schema_name", ...}]`` of one catalog database."""
        data = self._json("GET", f"/api/schemas/{quote(database_id, safe='')}") or {}
        return data.get("schemas") or []

    def patch_term(self, term_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json("PATCH", f"/api/terms/{quote(term_id, safe='')}", json=payload)["data"]

    def semantic_graph(self, database_id: str | None = None) -> dict[str, Any]:
        params = {"database_id": database_id} if database_id else {}
        return self._json("GET", "/api/exploration/semantic-graph", params=params)["data"]

    def export_model(
        self, database_ids: list[str], fmt: str = "auto_ontology", include_ontology: bool = False
    ) -> bytes:
        body: dict[str, Any] = {"databases": list(database_ids), "format": fmt}
        if include_ontology:
            body["include_ontology"] = True
        return self._request(
            "POST", "/api/model/export", json=body, timeout=EXPORT_TIMEOUT_S
        ).content
