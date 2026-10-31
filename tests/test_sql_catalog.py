# SPDX-License-Identifier: MIT
"""Frozen source identity and approved model attributes drive SQL, never live Terms."""

import hashlib
import json
from dataclasses import replace
from zipfile import ZipFile

import pytest
import yaml

from autoontology.config import Settings
from autoontology.engine.build import source_set
from autoontology.ontology.model import MergedOntology, OClass, OProperty
from autoontology.playground.sql_catalog import catalog_for, source_binding, write_catalog
from autoontology.playground.sql_guard import SqlError, validate_sql
from autoontology.store.db import Store
from autoontology.store.operations import Operations
from autoontology.store.publications import Publications


@pytest.fixture
def frozen(tmp_path):
    settings = replace(
        Settings.from_env(), home=tmp_path / "home", exports_dir=tmp_path / "exports"
    )
    store = Store.for_home(settings.home)
    Operations(store)  # An empty managed installation, before its first candidate.
    source = store.add_source(
        name="finance",
        kind="files",
        locator=json.dumps({"path": str(tmp_path)}),
        pg_database="autoontology_src_finance",
    )
    store.set_catalog_database_id(source.id, "db")
    source = store.get_source("finance")
    model = MergedOntology("org", "https://example.test/#", structured_sources=["finance"])
    model.classes["term-customer"] = OClass(
        "term-customer",
        "Approved customer",
        "structured",
        term_id="customer",
        tables=["public.customers"],
        structured_source="finance",
        comment="Approved meaning",
    )
    model.properties["attr-name"] = OProperty(
        "attr-name",
        "datatype",
        "Approved name",
        "term-customer",
        "xsd:string",
        "structured",
        source_column="public.customers.name",
        owner="finance",
    )
    native = {
        "data_layer": {
            "databases": [
                {
                    "id": "db",
                    "dialect": "postgres",
                    "schemas": [
                        {
                            "name": "public",
                            "database_name": "autoontology_src_finance",
                            "tables": [
                                {
                                    "id": "customers",
                                    "name": "customers",
                                    "columns": [
                                        {
                                            "id": "name",
                                            "name": "name",
                                            "type": "text",
                                            "sample_values": ["do not send sampled values"],
                                        },
                                        {"id": "password", "name": "password", "type": "text"},
                                    ],
                                },
                                {
                                    "id": "private",
                                    "name": "private",
                                    "columns": [{"id": "private-id", "name": "id", "type": "text"}],
                                },
                            ],
                        }
                    ],
                }
            ]
        },
        "semantic_layer": {
            "terms": [
                {
                    "id": "customer",
                    "name": "Unapproved live label",
                    "represents": ["customers"],
                    "columns_attributes": [
                        {"id": "name", "column_id": "name"},
                        {"id": "secret", "column_id": "password"},
                    ],
                },
                {"id": "private-term", "represents": ["private"], "columns_attributes": []},
            ],
            "semantic_fks": [],
        },
    }
    run = store.create_run(
        kind="build", source_id=None, params={"source_set": source_set([source])}
    )
    revision = store.save_snapshot(
        run.id,
        model,
        sources=[
            {
                "source_id": source.id,
                "name": source.name,
                "kind": "structured",
                "sql_binding": source_binding(source, settings),
            }
        ],
    )
    store.update_run(run.id, status="succeeded")
    folder = settings.exports_dir / f"r{revision:04}"
    (folder / "upstream").mkdir(parents=True)
    path = folder / "upstream/finance.ossie.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "semantic_model.yaml",
            yaml.safe_dump(
                {
                    "custom_extensions": [
                        {
                            "vendor_name": "NVIDIA_AUTO_ONTOLOGY",
                            "data": json.dumps({"native_document": native}),
                        }
                    ]
                }
            ),
        )
    (folder / "export.json").write_text(
        json.dumps(
            {
                "revision": revision,
                "snapshot_run": run.id,
                "workspace": model.name,
                "files": ["upstream/finance.ossie.zip"],
                "file_sha256": {
                    "upstream/finance.ossie.zip": hashlib.sha256(path.read_bytes()).hexdigest()
                },
            }
        )
    )
    return store, settings, model, revision, source, folder


def test_sql_projection_uses_approved_labels_and_only_approved_columns(frozen):
    store, settings, model, rev, _, _ = frozen
    catalog = catalog_for(store, settings, rev, model, "finance")
    assert catalog["concepts"][0]["label"] == "Approved customer"
    assert catalog["tables"][0]["columns"] == {
        "name": {"type": "text", "label": "Approved name", "description": ""}
    }
    assert "sample_values" not in json.dumps(catalog)
    with pytest.raises(SqlError):
        validate_sql("SELECT password FROM customers", catalog)
    with pytest.raises(SqlError):
        validate_sql("SELECT * FROM private", catalog)


def test_current_source_uuid_cannot_replace_a_published_source(frozen):
    store, settings, model, rev, source, _ = frozen
    store.delete_source(source.id)
    new = store.add_source(
        name="finance", kind="files", locator=source.locator, pg_database=source.pg_database
    )
    store.set_catalog_database_id(new.id, "db")
    with pytest.raises(SqlError, match="replaced"):
        catalog_for(store, settings, rev, model, "finance")


def test_changed_connection_target_is_rejected(frozen):
    store, settings, model, rev, _, _ = frozen
    with pytest.raises(SqlError, match="connection changed"):
        catalog_for(
            store,
            replace(settings, postgres_port=settings.postgres_port + 1),
            rev,
            model,
            "finance",
        )


def test_credentials_are_not_exported_and_rotation_preserves_target_identity(frozen):
    _, settings, _, _, source, _ = frozen
    a = source_binding(source, replace(settings, postgres_password="first-secret"))
    b = source_binding(source, replace(settings, postgres_password="new-secret"))
    assert a == b and "secret" not in json.dumps(a)


def test_frozen_export_must_match_its_snapshot_receipt(frozen):
    store, settings, model, rev, _, folder = frozen
    (folder / "upstream/finance.ossie.zip").write_bytes(b"changed")
    with pytest.raises(SqlError, match="missing or changed"):
        catalog_for(store, settings, rev, model, "finance")


def test_publication_catalog_file_is_added_to_export_integrity_metadata(frozen):
    store, settings, model, rev, _, folder = frozen
    write_catalog(store, settings, model, rev, folder, Publications(store).bindings([rev]))
    envelope = json.loads((folder / "sql-catalog.json").read_text())
    assert envelope["sources"][0]["status"] == "available"
    marker = json.loads((folder / "export.json").read_text())
    assert (
        marker["file_sha256"]["sql-catalog.json"]
        == hashlib.sha256((folder / "sql-catalog.json").read_bytes()).hexdigest()
    )


def test_reviewed_publication_pins_sql_catalog_and_tampering_blocks_reads(frozen):
    from autoontology.engine.operations import run_publish, validate
    from autoontology.store.publications import PublicationError

    store, settings, model, rev, _, folder = frozen
    payload = (folder / "upstream/finance.ossie.zip").read_bytes()
    ops = Operations(store)
    for item in ops.changes():
        ops.decide(item["id"], "accept", candidate=rev, note="Approved SQL source reviewed")
    ops.add_check("Which approved concept?", "customer")

    class Answer:
        async def stream(self, messages):
            yield "Approved customer [E1]"

    embedder = (lambda texts, kind: [[1.0, 0.0] for _ in texts], "test")
    check = validate(store, settings, revision=rev, embedder=embedder, llm=Answer())
    assert check["ok"]

    def export(model, folder, revision, run):
        # Controlled export bundle; full OSSIE schema tests have separate fixtures.
        (folder / "upstream").mkdir(parents=True)
        (folder / "upstream/finance.ossie.zip").write_bytes(payload)
        (folder / "export.json").write_text(
            json.dumps(
                {
                    "revision": revision,
                    "snapshot_run": run.id,
                    "workspace": model.name,
                    "files": ["upstream/finance.ossie.zip", "export.json"],
                    "file_sha256": {
                        "upstream/finance.ossie.zip": hashlib.sha256(payload).hexdigest()
                    },
                }
            )
        )

    result = run_publish(
        store, settings, revision=rev, token=check["token"], embedder=embedder, exporter=export
    )
    publication = Publications(store).get(result.revision)
    published_model = ops.model(result.revision)
    assert "sql-catalog.json" in publication["manifest"]["artifacts"]
    assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]
    assert catalog_for(store, settings, result.revision, published_model)["status"] == "available"
    (settings.exports_dir / f"r{result.revision:04}/sql-catalog.json").write_text("{}")
    with pytest.raises((SqlError, PublicationError)):
        catalog_for(store, settings, result.revision, published_model)


def test_unpublished_source_and_missing_frozen_mapping_are_actionable(frozen):
    store, settings, model, rev, _, folder = frozen
    with pytest.raises(SqlError):
        catalog_for(store, settings, rev, model, "private")
    (folder / "upstream/finance.ossie.zip").unlink()
    with pytest.raises(SqlError):
        catalog_for(store, settings, rev, model, "finance")


def test_legacy_source_binding_uses_a_recorded_run_not_a_name_guess(frozen):
    store, settings, model, rev, _, _ = frozen
    with store._conn() as conn:
        conn.execute(
            "UPDATE ontology_snapshots SET sources_json=? WHERE revision=?",
            (json.dumps([{"name": "finance", "kind": "structured"}]), rev),
        )
    assert catalog_for(store, settings, rev, model, "finance")["binding"]["legacy_source_config"]
    run = store.get_snapshot(rev).run_id
    store.update_run(run, status="succeeded", params={})
    with pytest.raises(SqlError, match="no verified source identity"):
        catalog_for(store, settings, rev, model, "finance")
