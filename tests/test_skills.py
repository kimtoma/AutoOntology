# SPDX-License-Identifier: MIT
"""Skill export (``ontology/logic/skills.py``, ``GET /logic/skill.zip``, ``onto skill
export``): the bundle's layout, SKILL.md front matter and sections, placeholders instead
of tokens, re-importable definitions, deterministic bytes and the audit record."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from ontology.config import Settings
from ontology.logic import functions, registry, skills, specs
from ontology.store.db import Store
from ontology.web import app as web
from ontology.web.runs import RunManager
from tests import logic_fixtures as fx
from tests.test_actions import activate_samples, sample_specs
from tests.test_logic import TODAY

API = "/api"


@pytest.fixture
def view(isolated_env, monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    fx.install_backend(monkeypatch)
    store = Store.for_home(Settings.from_env().home)
    view = fx.seed(store)
    activate_samples(view)
    for doc in sample_specs("workflows.yaml"):
        registry.save(view, doc)
        registry.set_status(view, "workflow", doc["name"], "active")
    return view


def _unzip(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i).decode("utf-8") for i in zf.infolist()}


def _front(text: str) -> dict:
    assert text.startswith("---\n")
    head, _, _ = text[4:].partition("\n---\n")
    return yaml.safe_load(head)


def test_bundle_layout_and_skill_md(view):
    bundle = skills.build_skill(view)
    assert bundle.name == "ontology-default"
    files = _unzip(bundle.zip_bytes())
    root = "ontology-default/"
    assert sorted(files) == [
        root + "SKILL.md",
        root + "definitions/actions.yaml",
        root + "definitions/functions.yaml",
        root + "definitions/workflows.yaml",
        root + "mcp.json",
    ]
    md = files[root + "SKILL.md"]
    front = _front(md)
    assert front["name"] == "ontology-default"
    assert 0 < len(front["description"]) <= skills.MAX_DESCRIPTION
    assert "Use when" in front["description"]
    for heading in (
        "## Purpose",
        "## How to work with this ontology",
        "## Object types",
        "## Functions",
        "## Actions",
        "## Workflows",
        "## Examples",
    ):
        assert heading in md, heading
    # Korean names and labels as they are; tables, keys, rules in plain words
    assert "### 청구" in md and "Table: `invoices`" in md and "key: `청구ID`" in md
    assert "### `연체일수`" in md and "returns: number" in md
    assert "### `독촉단계기록` -- 독촉 단계 기록" in md
    assert "이미 완납된 청구서입니다." in md
    assert "needs an owner to approve when `param.단계 >= 4`" in md
    assert "agents may propose it" in md
    assert "on a schedule: daily 02:00" in md
    assert "run the action `독촉단계기록`" in md
    assert "Never assume approval" in md and "preview_action" in md
    assert "1 action and 1 workflow," in md and "e.g. `invoices`" in md
    assert '"params": {"단계": 1}' in md  # the default, not an enum value
    assert "Operational properties (written by actions): `독촉단계`, `독촉메모`" in md


def test_mcp_json_has_placeholders_and_never_a_token(view):
    from ontology.store.agent_tokens import AgentTokens

    secret, _ = AgentTokens(view).create("bot", created_by="local")
    files = _unzip(skills.build_skill(view).zip_bytes())
    mcp = json.loads(files["ontology-default/mcp.json"])
    server = mcp["mcpServers"]["ontology-default"]
    assert server["url"] == "{{ONTOLOGY_URL}}/mcp"
    assert server["headers"] == {"Authorization": "Bearer <token>"}
    blob = "".join(files.values())
    assert secret not in blob and "aok_" not in blob


def test_definitions_are_the_active_specs_and_apply_again(view):
    registry.save(view, {**sample_specs("functions.yaml")[0], "name": "초안", "label": "초안"})
    files = _unzip(skills.build_skill(view).zip_bytes())
    fns = specs.load_documents(files["ontology-default/definitions/functions.yaml"])
    assert sorted(d["name"] for d in fns) == sorted(
        d.name for d in view.logic_defs().list("function", "active")
    )
    assert "초안" not in {d["name"] for d in fns}  # drafts stay out
    for name in ("functions", "actions", "workflows"):
        docs = specs.load_documents(files[f"ontology-default/definitions/{name}.yaml"])
        for doc in docs:
            spec, errors = registry.check(view, doc)
            assert spec is not None and errors == [], doc["name"]


def test_export_is_deterministic(view):
    first = skills.build_skill(view).zip_bytes()
    assert skills.build_skill(view).zip_bytes() == first
    with zipfile.ZipFile(io.BytesIO(first)) as zf:
        stamps = {i.date_time for i in zf.infolist()}
    assert len(stamps) == 1 and next(iter(stamps))[0] >= 2020
    registry.set_status(view, "workflow", "연체독촉", "retired")
    second = skills.build_skill(view).zip_bytes()
    assert second != first
    assert "definitions/workflows.yaml" not in "".join(_unzip(second))


def test_an_empty_project_exports_a_valid_skill(isolated_env):
    store = Store.for_home(Settings.from_env().home)
    bundle = skills.build_skill(store.project())
    files = _unzip(bundle.zip_bytes())
    md = files["ontology-default/SKILL.md"]
    assert _front(md)["name"] == "ontology-default"
    assert "No active action." in md
    assert bundle.timestamp == skills.EPOCH


def test_skill_name_and_long_descriptions():
    assert skills.skill_name("Beta Team_2") == "ontology-beta-team-2"
    assert skills.skill_name("한글") == "ontology-project"
    assert len(skills.skill_name("x" * 200)) <= 64
    many = [type("T", (), {"label": "가" * 60, "name": "n"})() for _ in range(40)]
    text = skills._description("p", many, many, many, many)
    assert len(text) <= skills.MAX_DESCRIPTION and "Use when" in text


def test_route_and_cli(view, tmp_path):
    from typer.testing import CliRunner

    from ontology.cli import app

    settings = Settings.from_env()
    with TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict))) as c:
        resp = c.get(f"{API}/logic/skill.zip")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        assert 'filename="ontology-default.zip"' in resp.headers["content-disposition"]
        assert resp.content == skills.build_skill(view).zip_bytes()
        assert c.get(f"{API}/projects/default/logic/skill.zip").status_code == 200
        with sqlite3.connect(view.store.path) as conn:
            rows = conn.execute(
                "SELECT target, detail_json FROM audit_log WHERE action = 'skill.export'"
            ).fetchall()
        assert rows and rows[0][0] == "ontology-default"
        assert json.loads(rows[0][1])["actions"] == 1
        out = CliRunner().invoke(app, ["skill", "export", "-o", str(tmp_path)])
        assert out.exit_code == 0, out.output
        saved = tmp_path / "ontology-default.zip"
        assert saved.read_bytes() == resp.content
