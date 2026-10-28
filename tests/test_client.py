import json

import httpx
import pytest

from autoontology.engine.client import BackendClient, BackendError, BackendUnavailable


def make(handler):
    return BackendClient("http://backend.test/", transport=httpx.MockTransport(handler))


def test_sends_user_header_and_parses():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/api/datasources/dbs":
            return httpx.Response(
                200, json={"data": [{"id": "d1", "name": "AutoOntology_Src_A"}], "count": 1}
            )
        if req.url.path == "/api/connections" and req.method == "POST":
            return httpx.Response(201, json={"data": json.loads(req.content)["connection"]})
        if req.url.path == "/api/connections":
            return httpx.Response(200, json={"data": [{"database_name": "x", "connection": {}}]})
        if req.url.path == "/api/model/export":
            return httpx.Response(200, content=b"zip-bytes")
        if req.url.path == "/api/terms":
            return httpx.Response(200, json={"data": [], "count": 0, "total": 0})
        return httpx.Response(404)

    c = make(handler)
    assert c.database_id_by_name("autoontology_src_a") == "d1"
    assert c.database_id_by_name("nope") is None
    assert c.create_connection({"database": "x"}) == {"database": "x"}
    assert c.connection_exists("X") and not c.connection_exists("y")
    assert c.export_model(["d1"], "ossie", include_ontology=True) == b"zip-bytes"
    assert json.loads(seen[-1].content) == {
        "databases": ["d1"],
        "format": "ossie",
        "include_ontology": True,
    }
    c.list_terms(query="고객", limit=None)
    assert seen[-1].url.params.get("query") == "고객" and "limit" not in seen[-1].url.params
    assert all(r.headers["x-auto-ontology-user-id"] == "autoontology" for r in seen)


def test_non_2xx_raises_backend_error_with_status_and_body():
    c = make(lambda req: httpx.Response(422, json={"detail": "already exists"}))
    with pytest.raises(BackendError) as info:
        c.create_connection({"database": "x"})
    assert info.value.status == 422 and "already exists" in info.value.body
    assert not isinstance(info.value, BackendUnavailable)


def test_delete_quotes_name():
    seen = []

    def handler(req):
        seen.append(req.url.raw_path)
        return httpx.Response(200, json={"data": {"database_name": "a b"}})

    make(handler).delete_connection("a b/c")
    assert seen == [b"/api/connections/a%20b%2Fc"]


def test_unreachable_is_backend_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(BackendUnavailable, match="autoontology up"):
        make(handler).health()
