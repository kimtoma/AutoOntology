# SPDX-License-Identifier: MIT
"""Versioned suites and explicit rule facts must bind deterministic validation."""

import pytest

from autoontology.ontology.model import MergedOntology, OEvidence, OIndividual
from autoontology.ontology.rules import RuleError, clean_rule, rule_checks
from autoontology.store.db import Store
from autoontology.store.quality import Quality


def rule(**patch):
    return {
        "scope": "계약",
        "measure": "계약 금액",
        "operator": "gte",
        "amount": "10000000",
        "currency": "KRW",
        "authority": "대표",
        "effective_from": "2026-11-01",
        "effective_to": None,
        "exceptions": [],
        "priority": 0,
        **patch,
    }


def test_rule_values_roundtrip_without_changing_legacy_model_bytes():
    model = MergedOntology("org", "https://example.test/#")
    model.individuals["rule1"] = OIndividual("rule1", "승인", "BusinessRule", "rule")
    legacy = model.to_dict()
    assert legacy["format"] == 1 and "rule" not in legacy["individuals"]["rule1"]
    model.individuals["rule1"].rule = clean_rule(rule(amount="10000000.00"))
    modern = model.to_dict()
    assert modern["format"] == 2
    assert MergedOntology.from_dict(modern).to_dict() == modern
    assert modern["individuals"]["rule1"]["rule"]["amount"] == "10000000"


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": "NaN"},
        {"amount": "-1"},
        {"operator": "eval"},
        {"currency": "won"},
        {"effective_to": "2026-01-01"},
        {"authority": ""},
        {"priority": True},
    ],
)
def test_invalid_rule_fields_fail_closed(patch):
    with pytest.raises(RuleError):
        clean_rule(rule(**patch))


def test_rule_conflicts_respect_amount_date_boundaries_and_explicit_priority():
    model = MergedOntology("org", "https://example.test/#")
    for ident, fields in [("a", rule()), ("b", rule(authority="재무 책임자"))]:
        model.individuals[ident] = OIndividual(ident, ident, "BusinessRule", "rule", rule=fields)
    checks = rule_checks(model)
    assert checks["conflicts"] and not checks["ok"]
    model.individuals["b"].rule["priority"] = 1
    assert not rule_checks(model)["conflicts"]
    model.individuals["b"].rule.update(priority=0, operator="lt")
    assert not rule_checks(model)["conflicts"]
    model.individuals["b"].rule.update(operator="gte", effective_from="2027-01-01")
    model.individuals["a"].rule["effective_to"] = "2027-01-01"
    assert not rule_checks(model)["conflicts"]


def suite(**patch):
    return {
        "title": "계약 승인 평가",
        "description": "금액과 권한",
        "questions": [
            {
                "id": "approval",
                "question": "승인자는?",
                "expected": "대표",
                "mode": "contains",
                "forbidden": ["재무팀"],
                "minimum_citations": 1,
            }
        ],
        "assertions": [],
        **patch,
    }


def test_suite_versions_are_immutable_activation_is_explicit_and_old_sets_survive(tmp_path):
    store = Store.for_home(tmp_path)
    quality = Quality(store)
    first = quality.prepare("contracts", suite(), previous=None)
    assert quality.active() == []
    quality.activate(first["id"], first["sha256"], confirm=True)
    second = quality.prepare("contracts", suite(title="개정 평가"), previous=first["id"])
    assert quality.active()[0]["id"] == first["id"]
    quality.activate(second["id"], second["sha256"], confirm=True)
    assert len(quality.versions("contracts")) == 2
    assert quality.get(first["id"])["payload"]["title"] == "계약 승인 평가"
    with pytest.raises(Exception):
        quality.activate(first["id"], first["sha256"], confirm=True)
    with store._conn() as conn, pytest.raises(Exception):
        conn.execute("UPDATE quality_versions SET payload_json='{}' WHERE id=?", (first["id"],))


def test_deterministic_answer_criteria_require_actual_known_citations():
    case = suite()["questions"][0]
    assert Quality.answer_check(case, "대표 [E1]", {"E1": {}}, [])
    assert not Quality.answer_check(case, "대표 재무팀 [E1]", {"E1": {}}, [])
    assert not Quality.answer_check(case, "대표", {}, [])
    assert not Quality.answer_check(case, "대표 [E2]", {}, ["E2"])
    assert not Quality.answer_check({**case, "mode": "exact"}, "대리 대표", {"E1": {}}, [])


def test_assertions_bind_semantic_subject_fields_and_missing_ones_fail(tmp_path):
    model = MergedOntology("org", "https://example.test/#")
    model.individuals["rule1"] = OIndividual("rule1", "승인", "BusinessRule", "rule", rule=rule())
    model.evidence["ev"] = OEvidence("ev", "doc", "대표 승인", 1, True)
    model.individuals["rule1"].derived_from = ["ev"]
    quality = Quality(Store.for_home(tmp_path))
    data = suite(
        assertions=[
            {
                "id": "authority",
                "collection": "individuals",
                "subject": "rule1",
                "field": "rule.authority",
                "expected": "대표",
            }
        ]
    )
    prepared = quality.prepare("contracts", data, previous=None)
    quality.activate(prepared["id"], prepared["sha256"], confirm=True)
    assert quality.model_checks(model)["ok"]
    model.individuals["rule1"].rule["authority"] = "담당자"
    assert not quality.model_checks(model)["ok"]
    del model.individuals["rule1"]
    assert not quality.model_checks(model)["ok"]


def test_typed_rule_is_retrievable_and_exported_without_executing_exceptions():
    from autoontology.export.rdf import to_graph
    from autoontology.playground.elements import elements_from_model

    model = MergedOntology("org", "https://example.test/#")
    model.individuals["rule1"] = OIndividual("rule1", "승인", "BusinessRule", "rule", rule=rule())
    assert "10000000" in elements_from_model(model)[0].text
    assert "ruleFields" in to_graph(model).serialize(format="turtle")


def test_missing_rule_fields_do_not_satisfy_null_equality(tmp_path):
    model = MergedOntology("org", "https://example.test/#")
    model.individuals["rule1"] = OIndividual("rule1", "승인", "BusinessRule", "rule")
    quality = Quality(Store.for_home(tmp_path))
    item = quality.prepare(
        "missing",
        suite(
            assertions=[
                {
                    "id": "missing",
                    "collection": "individuals",
                    "subject": "rule1",
                    "field": "rule.effective_to",
                    "expected": None,
                }
            ]
        ),
        previous=None,
    )
    quality.activate(item["id"], item["sha256"], confirm=True)
    assert not quality.model_checks(model)["ok"]


def test_huge_decimal_exponent_is_refused_before_formatting():
    with pytest.raises(RuleError):
        clean_rule(rule(amount="1e999999999"))


def test_suite_changes_invalidate_publication_validation_and_preserve_failed_run(tmp_path):
    from dataclasses import replace

    from autoontology.config import Settings
    from autoontology.engine.operations import run_publish, validate
    from autoontology.store.operations import Operations, OperationsError
    from tests.test_operations import example, snapshot

    store = Store.for_home(tmp_path)
    model = example()
    snapshot(store, model)
    ops = Operations(store)
    ops.state()
    model.classes["concept-approval"].label = "개정 승인"
    revision = snapshot(store, model)
    for change in ops.changes():
        ops.decide(change["id"], "accept", candidate=revision, note="Reviewed")

    class Answer:
        async def stream(self, messages):
            yield "대표 [E1]"

    quality = Quality(store)
    first = quality.prepare("contracts", suite(), previous=None)
    quality.activate(first["id"], first["sha256"], confirm=True)
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    embedder = (lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts], "test")
    validated = validate(store, settings, revision=revision, embedder=embedder, llm=Answer())
    assert validated["ok"]
    second = quality.prepare(
        "contracts",
        suite(questions=[{**suite()["questions"][0], "expected": "財務責任者"}]),
        previous=first["id"],
    )
    quality.activate(second["id"], second["sha256"], confirm=True)
    with pytest.raises(OperationsError):
        run_publish(store, settings, revision=revision, token=validated["token"], embedder=embedder)
    failed = validate(store, settings, revision=revision, embedder=embedder, llm=Answer())
    assert not failed["ok"]
    assert len(quality.runs()) == 2 and quality.runs()[0]["result"] == failed
    assert quality.runs()[1]["result"]["model_checks"]["suite_versions"][0]["id"] == first["id"]


@pytest.fixture
def authority_workspace(tmp_path):
    from autoontology.store.identity import Identity

    store = Store.for_home(tmp_path / "authority")
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "quality owner password long")
    owner = identity.authenticate(issued.secret.reveal())
    delegated = identity.delegate(
        owner,
        "preparer",
        "Preparer",
        ["candidate.read", "review.suggest", "validation.run"],
        ttl=600,
    )
    return (
        store,
        identity,
        issued,
        owner,
        delegated,
        identity.authenticate(delegated.secret.reveal()),
    )


def test_agent_prepares_but_cannot_activate_and_policy_change_invalidates_draft(
    authority_workspace,
):
    from autoontology.security.context import acting_as
    from autoontology.security.policy import AccessError

    store, identity, _, owner, _, agent = authority_workspace
    quality = Quality(store)
    with acting_as(store, agent):
        draft = quality.prepare("contracts", suite(), previous=None)
        with pytest.raises(AccessError):
            quality.activate(draft["id"], draft["sha256"], confirm=True)
    with acting_as(store, owner):
        identity.create_domain(owner, "security", "Security")
        with pytest.raises(AccessError) as error:
            quality.activate(draft["id"], draft["sha256"], confirm=True)
        assert error.value.code == "quality_stale"
        assert not quality.active()


def test_http_and_native_suite_parity_and_explicit_confirmation(
    authority_workspace, isolated_env, monkeypatch, tmp_path
):
    import json
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology.cli import app as cli
    from autoontology.config import Settings
    from autoontology.security.credential_file import save_credential
    from autoontology.web.app import create_app

    store, identity, issued, owner, delegated, _ = authority_workspace
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(store.path.parent))
    settings = replace(Settings.from_env(), home=store.path.parent)
    folder = store.path.parent / "security"
    folder.mkdir(mode=0o700)
    file = folder / "cli.json"
    save_credential(
        file, issued, workspace_id=identity.status()["workspace_id"], origin=settings.service_url
    )
    payload = tmp_path / "suite.json"
    payload.write_text(json.dumps(suite(), ensure_ascii=False))
    runner = CliRunner()
    prepared = runner.invoke(
        cli,
        [
            "--credential-file",
            str(file),
            "ops",
            "quality-prepare",
            "contracts",
            "--input",
            str(payload),
        ],
    )
    assert prepared.exit_code == 0, prepared.stdout
    draft = json.loads(prepared.stdout)
    headers = {"Authorization": "Bearer " + issued.secret.reveal()}
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/quality/versions/{draft['id']}/activate",
            headers=headers,
            json={"sha256": draft["sha256"]},
        )
        assert response.status_code == 400
        response = client.post(
            f"/api/quality/versions/{draft['id']}/activate",
            headers=headers,
            json={"sha256": draft["sha256"], "confirm": True},
        )
        assert response.status_code == 200
        api = client.get("/api/quality", headers=headers).json()
    native = runner.invoke(cli, ["--credential-file", str(file), "ops", "quality"])
    assert native.exit_code == 0 and json.loads(native.stdout) == api


def test_evaluation_change_in_last_publication_gap_is_refused(tmp_path, monkeypatch):
    from autoontology.engine.operations import run_publish, validate
    from autoontology.store.operations import Operations, OperationsError
    from tests import test_publication_manifest as pub

    store, settings, revision, _, _ = pub.prepared.__wrapped__(tmp_path)
    quality = Quality(store)
    data = suite(
        questions=[],
        assertions=[
            {
                "id": "label",
                "collection": "classes",
                "subject": "concept-rule",
                "field": "label",
                "expected": "승인",
            }
        ],
    )
    first = quality.prepare("labels", data, previous=None)
    quality.activate(first["id"], first["sha256"], confirm=True)
    checked = validate(
        store, settings, revision=revision, embedder=(pub.embed, "test"), llm=pub.Answer()
    )
    second = quality.prepare(
        "labels", {**data, "description": "A new exact version"}, previous=first["id"]
    )
    original = Operations.commit

    def race(self, *args, **kwargs):
        Quality(self.store).activate(second["id"], second["sha256"], confirm=True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Operations, "commit", race)
    with pytest.raises(OperationsError):
        run_publish(
            store, settings, revision=revision, token=checked["token"], embedder=(pub.embed, "test")
        )
    assert Operations(store).state()["published_revision"] is None


def test_current_model_assertion_blocks_restore_before_model_calls(tmp_path):
    from autoontology.engine.restorations import prepare_restore
    from tests import test_restorations as restores

    store, settings, target, *_ = restores.history.__wrapped__(tmp_path)
    quality = Quality(store)
    version = quality.prepare(
        "labels",
        suite(
            questions=[],
            assertions=[
                {
                    "id": "label",
                    "collection": "classes",
                    "subject": "concept-rule",
                    "field": "label",
                    "expected": "A different required label",
                }
            ],
        ),
        previous=None,
    )
    quality.activate(version["id"], version["sha256"], confirm=True)

    def forbid(*args):
        raise AssertionError("Provider must not be called for a deterministic model failure")

    packet = prepare_restore(
        store, settings, revision=target, reason="Controlled rule check", embedder=(forbid, "test")
    )
    assert not packet["packet"]["validation"]["ok"]
    assert not packet["packet"]["validation"]["model_checks"]["ok"]
