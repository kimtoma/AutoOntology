"""Playground end to end against the live service and the real data (``hanbit`` +
``hanbit-docs``) with whatever chat/embed provider is active.

Run with: AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_playground_e2e.py -s
(slow with the local ollama profile: ~10 min, the NL->SQL agent alone takes minutes).
Every answer is written to ``exports/playground-e2e/<ts>.json``.
"""

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from autoontology.config import Settings

pytestmark = pytest.mark.integration

DOCS = "hanbit-docs"
TIMEOUT = httpx.Timeout(30.0, read=900.0)
RECORD: dict = {}


@pytest.fixture(scope="module")
def svc():
    settings = Settings.from_env()
    base = f"{settings.service_url}/api/playground"
    try:
        httpx.get(f"{settings.service_url}/health/live", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("service not running (uv run autoontology up)")
    names = [s["name"] for s in httpx.get(f"{settings.service_url}/api/sources"
                                          ).json()["sources"]]
    if DOCS not in names:
        pytest.skip(f"no {DOCS} source")
    yield base
    out = settings.exports_dir / "playground-e2e"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"{stamp}.json").write_text(json.dumps(RECORD, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\nrecorded: {out / f'{stamp}.json'}")


def events(url, body):
    out, event = [], "message"
    with httpx.stream("POST", url, json=body, timeout=TIMEOUT) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                out.append((event, json.loads(line[5:].strip())))
    return out


def test_chat_about_contracts_cites_elements(svc):
    evs = events(f"{svc}/chat", {"source": DOCS, "question": "계약은 어떤 승인 절차를 거치나요?"})
    names = [n for n, _ in evs]
    assert "error" not in names, evs[-1]
    answer = "".join(d["text"] for n, d in evs if n == "delta")
    cites = dict(evs)["citations"]
    RECORD["chat"] = {"question": "계약은 어떤 승인 절차를 거치나요?", "answer": answer,
                      "citations": cites, "status": dict(evs)["status"],
                      "retrieved": len(dict(evs)["retrieved"]["elements"]),
                      "index": dict(evs)["retrieved"]["index"]}
    assert answer.strip()
    assert len(cites["citations"]) >= 1, answer


def test_nl_to_sql_question(svc):
    evs = events(f"{svc}/sql", {"source": DOCS, "question": "고객 수는?",
                                "conversation_id": str(uuid.uuid4())})
    names = [n for n, _ in evs]
    RECORD["sql"] = {"question": "고객 수는?", "events": names,
                     "meta": dict(evs).get("meta"), "result": dict(evs).get("result"),
                     "error": dict(evs).get("error")}
    assert names[0] == "meta" and names[-1] in ("done", "error")
    assert "step" in names
    assert "result" in names or "error" in names


def test_sparql_examples_all_execute(svc):
    examples = httpx.get(f"{svc}/sparql/examples").json()["examples"]
    assert len(examples) == 8
    RECORD["sparql"] = {}
    for ex in examples:
        r = httpx.post(f"{svc}/sparql", json={"source": DOCS, "query": ex["query"],
                                              "limit": 3}, timeout=30)
        assert r.status_code == 200, (ex["id"], r.text)
        body = r.json()
        RECORD["sparql"][ex["id"]] = {"total": body["total"], "columns": body["columns"],
                                      "rows": body["rows"], "export": body["export"]}
