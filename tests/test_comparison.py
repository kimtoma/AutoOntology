# SPDX-License-Identifier: MIT
"""Frozen-input comparisons must not become approvals, live SQL, or authority bypasses."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.evaluation import Comparison, sample_comparison
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.web.app import create_app


@pytest.fixture
def comparison(isolated_env):
    return Comparison(Store.for_home(Settings.from_env().home))


def test_sample_executes_frozen_korean_rows_and_distinguishes_reference_from_approval(comparison):
    payload = sample_comparison()
    original = deepcopy(payload)
    result = comparison.compare(payload)
    assert payload == original
    assert result["schema"] == "knowledge-comparison-report/v1"
    assert result["human_approval"] is False and result["model_calls"] == 0
    assert result["data_scope"] == "submitted_frozen_tables_only"
    assert len(result["cases"]) == 5
    first = result["cases"][0]
    assert first["reference"]["rows"] == [[4338000000]]
    assert first["arms"][0]["result"]["rows"] == [[6774000000]]
    assert first["arms"][0]["verdict"] == "different"
    assert first["arms"][1]["verdict"] == "matches_reference"
    assert result["cases"][-1]["arms"][1]["verdict"] == "expected_abstention"
    assert result["baseline_measured"] is False
    assert comparison.compare(payload)["sha256"] == result["sha256"]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE contracts",
        "UPDATE contracts SET 契約金額=0",
        "ATTACH DATABASE '/tmp/not-created.db' AS x",
        "PRAGMA database_list",
        "SELECT load_extension('anything')",
        "SELECT * FROM sqlite_master",
        "SELECT randomblob(1000000000)",
        "SELECT 1; SELECT 2",
        "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x",
    ],
)
def test_submitted_sql_cannot_write_read_host_files_or_escape_limits(comparison, sql):
    payload = sample_comparison()
    payload["attempts"][0]["answers"]["D0-M01"]["sql"] = sql
    result = comparison.compare(payload)
    arm = result["cases"][0]["arms"][0]
    assert arm["verdict"] == "execution_error"
    assert arm["error"] == "comparison_sql_rejected"
    assert result["cases"][0]["arms"][1]["verdict"] == "matches_reference"


def test_changed_tables_change_receipt_and_reference_is_recomputed(comparison):
    payload = sample_comparison()
    before = comparison.compare(payload)
    table = next(t for t in payload["pack"]["tables"] if t["name"] == "contracts")
    table["rows"][0][table["columns"].index("계약금액")] = "18000001"
    after = comparison.compare(payload)
    assert before["sha256"] != after["sha256"]
    assert after["cases"][0]["reference"]["rows"] == [[4338000001]]


def test_known_evidence_ids_do_not_establish_semantic_approval(comparison):
    payload = sample_comparison()
    payload["attempts"][1]["answers"]["D0-M01"]["citations"] = ["invented"]
    result = comparison.compare(payload)
    assert result["cases"][0]["arms"][1]["verdict"] == "evidence_missing"
    assert result["semantic_evidence_review"] == "not_performed"


@pytest.mark.parametrize(
    "damage", ["extra", "nonfinite", "unknown_case", "missing_case", "duplicate_arm"]
)
def test_invalid_comparison_input_is_rejected_without_partial_success(comparison, damage):
    payload = sample_comparison()
    if damage == "extra":
        payload["pack"]["activate"] = True
    if damage == "nonfinite":
        payload["pack"]["tables"][0]["rows"][0][0] = float("nan")
    if damage == "unknown_case":
        payload["attempts"][0]["answers"]["fake"] = {"state": "abstain"}
    if damage == "missing_case":
        payload["attempts"][0]["answers"].pop("D0-M01")
    if damage == "duplicate_arm":
        payload["attempts"][1]["id"] = payload["attempts"][0]["id"]
    with pytest.raises(AccessError) as error:
        comparison.compare(payload)
    assert error.value.code == "comparison_input_invalid"


def test_domain_viewer_cannot_run_comparison_and_revoked_actor_cannot_reuse_it(isolated_env):
    store = Store.for_home(isolated_env)
    identity = Identity(store)
    owner = identity.authenticate(
        identity.bootstrap(
            "Managed", "owner", "Owner", "synthetic comparison password"
        ).secret.reveal()
    )
    viewer = identity.create_human(
        owner, "reader", "Reader", "synthetic comparison reader password", ["viewer"]
    )
    actor = identity.authenticate(
        identity.login("reader", "synthetic comparison reader password").secret.reveal()
    )
    with acting_as(store, actor), pytest.raises(AccessError):
        Comparison(store).compare(sample_comparison())
    with acting_as(store, owner):
        assert (
            Comparison(store).compare(sample_comparison())["actor"]["principal_id"]
            == owner.principal_id
        )
    assert viewer["id"] == actor.principal_id


def test_http_and_native_cli_share_receipt_and_do_not_migrate_or_publish_inputs(
    comparison, isolated_env, tmp_path
):
    import json

    settings = Settings.from_env()
    client = TestClient(create_app(settings))
    payload = sample_comparison()
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    before = comparison.store.schema_version()
    http = client.post("/api/evaluation/compare", json=payload)
    assert http.status_code == 200
    assert http.headers["cache-control"] == "private, no-store"
    native = CliRunner().invoke(app, ["ops", "compare", "--input", str(path)])
    assert native.exit_code == 0, native.output
    assert json.loads(native.stdout)["sha256"] == http.json()["sha256"]
    assert comparison.store.schema_version() == before
    assert client.get("/api/evaluation/sample").status_code == 200


@pytest.mark.parametrize("where", ["data_class", "kind", "origin"])
def test_wrong_json_selector_types_are_validation_errors(comparison, where):
    payload = sample_comparison()
    if where == "data_class":
        payload["pack"][where] = {}
    elif where == "kind":
        payload["pack"]["cases"][0][where] = []
    else:
        payload["attempts"][0][where] = {}
    with pytest.raises(AccessError) as error:
        comparison.compare(payload)
    assert error.value.code == "comparison_input_invalid"


def test_an_extra_unknown_citation_is_not_treated_as_known_evidence(comparison):
    payload = sample_comparison()
    payload["attempts"][1]["answers"]["D0-M01"]["citations"].append("invented")
    assert comparison.compare(payload)["cases"][0]["arms"][1]["verdict"] == "evidence_missing"


def test_cross_join_budget_does_not_change_the_submitted_data_or_other_arm(comparison):
    payload = sample_comparison()
    payload["attempts"][0]["answers"]["D0-M01"]["sql"] = (
        "SELECT COUNT(*) FROM invoices a, invoices b, invoices c, invoices d"
    )
    result = comparison.compare(payload)
    assert result["cases"][0]["arms"][0]["verdict"] == "execution_error"
    assert result["cases"][0]["arms"][1]["verdict"] == "matches_reference"


def test_result_is_denied_if_actor_is_revoked_during_computation(isolated_env, monkeypatch):
    from autoontology import evaluation

    store = Store.for_home(isolated_env)
    identity = Identity(store)
    owner = identity.authenticate(
        identity.bootstrap(
            "Managed", "owner", "Owner", "synthetic comparison owner password"
        ).secret.reveal()
    )
    session = identity.login("owner", "synthetic comparison owner password")
    actor = identity.authenticate(session.secret.reveal())
    original = evaluation.execute
    revoked = False

    def revoke(*args):
        nonlocal revoked
        if not revoked:
            identity.revoke_credential(owner, actor.credential_id)
            revoked = True
        return original(*args)

    monkeypatch.setattr(evaluation, "execute", revoke)
    with acting_as(store, actor), pytest.raises(AccessError) as error:
        Comparison(store).compare(sample_comparison())
    assert error.value.code == "credential_invalid"


def test_authenticated_http_policy_and_payload_size_are_enforced(comparison, isolated_env):
    settings = Settings.from_env()
    Identity(comparison.store).bootstrap(
        "Managed", "owner", "Owner", "synthetic comparison HTTP password"
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/evaluation/sample").status_code == 401
    assert client.post("/api/evaluation/compare", json=sample_comparison()).status_code == 401
    login = client.post(
        "/api/identity/login",
        json={"username": "owner", "password": "synthetic comparison HTTP password"},
    )
    assert login.status_code == 200
    assert client.get("/api/evaluation/sample").status_code == 200
    assert client.post("/api/evaluation/compare", json=sample_comparison()).status_code == 200
    assert (
        client.post("/api/evaluation/compare", content=b"x" * (2 * 1024 * 1024 + 1)).status_code
        == 413
    )


def test_large_integer_results_preserve_exact_digits_for_browser_downloads(comparison):
    payload = sample_comparison()
    case = payload["pack"]["cases"][0]
    case["reference_sql"] = "SELECT 9007199254740993 AS value"
    for arm in payload["attempts"]:
        arm["answers"][case["id"]]["sql"] = case["reference_sql"]
    result = comparison.compare(payload)
    assert result["cases"][0]["reference"]["rows"] == [
        [{"type": "integer", "value": "9007199254740993"}]
    ]


def test_managed_cli_input_obeys_host_root_admission(comparison, tmp_path):
    import json

    from autoontology.security.host_files import HostFiles

    identity = Identity(comparison.store)
    identity.bootstrap("Managed", "owner", "Owner", "synthetic CLI comparison password")
    folder = tmp_path / "business-inputs"
    folder.mkdir()
    input_file = folder / "comparison.json"
    input_file.write_text(json.dumps(sample_comparison(), ensure_ascii=False))
    password = tmp_path / "password-file"
    password.write_text("synthetic CLI comparison password")
    password.chmod(0o600)
    runner = CliRunner()
    assert (
        runner.invoke(app, ["auth", "login", "owner", "--password-file", str(password)]).exit_code
        == 0
    )
    denied = runner.invoke(app, ["ops", "compare", "--input", str(input_file)])
    assert denied.exit_code == 1
    assert json.loads(denied.stdout)["error"] == "host_root_required"
    actor = identity.authenticate(
        identity.login("owner", "synthetic CLI comparison password").secret.reveal()
    )
    with acting_as(comparison.store, actor):
        HostFiles(comparison.store).allow(folder, reason="Explicit frozen comparison inputs")
    accepted = runner.invoke(app, ["ops", "compare", "--input", str(input_file)])
    assert accepted.exit_code == 0
    assert json.loads(accepted.stdout)["model_calls"] == 0
