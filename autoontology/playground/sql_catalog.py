# SPDX-License-Identifier: MIT
"""Freeze SQL semantics from the approved model and verified physical export."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import yaml

from autoontology.ontology.model import slug
from autoontology.playground.sql_guard import SqlError
from autoontology.store.operations import digest
from autoontology.structured.pipeline import catalog_database_name, connection_for

CATALOG_FILE = "sql-catalog.json"
MAX_CATALOG_BYTES = 16 * 1024 * 1024


def source_binding(source, settings):
    connection = connection_for(source, settings)
    target = {
        k: connection[k]
        for k in ("type", "host", "user", "database", "schemas", "account", "warehouse", "role")
        if k in connection
    }
    target["port"] = str(
        connection.get("port") or {"postgres": 5432, "mysql": 3306}.get(connection["type"], "")
    )
    return {
        "version": 1,
        "source_id": source.id,
        "catalog_database_id": source.catalog_database_id,
        "connection_sha256": digest(target),
        "database": catalog_database_name(source),
        "dialect": connection["type"],
    }


def legacy_binding(store, revision, source):
    """Only reuse a source identity/configuration actually recorded on an ancestor run."""
    from autoontology.engine.build import source_set

    expected = source_set([source])[0]
    pending, seen = [revision], set()
    while pending:
        selected = pending.pop()
        if selected in seen or selected is None:
            continue
        seen.add(selected)
        snapshot = store.get_snapshot(selected, with_model=False)
        run = store.get_run(snapshot.run_id) if snapshot else None
        if not run:
            continue
        if expected in run.params.get("source_set", []):
            return {
                "version": 1,
                "source_id": source.id,
                "catalog_database_id": source.catalog_database_id,
                "legacy_source_config": expected["config"],
                "database": catalog_database_name(source),
                "status": "legacy_run_binding; host/account were not historically recorded",
            }
        if run.kind in ("publish", "restore"):
            pending.extend([run.params.get("revision"), run.params.get("previous_revision")])
    raise SqlError(
        "This historical version has no verified source identity. Rebuild and review its source before using SQL.",
        "sql_binding_missing",
    )


def verify_binding(store, settings, source_name, binding):
    source = store.get_source(source_name)
    if (
        source is None
        or source.id != binding.get("source_id")
        or source.kind not in ("files", "database")
    ):
        raise SqlError(
            "The published source was removed or replaced. Review the source and publish again.",
            "sql_source_changed",
        )
    current = source_binding(source, settings)
    if current["catalog_database_id"] != binding.get("catalog_database_id") or current[
        "database"
    ] != binding.get("database"):
        raise SqlError(
            "The source target changed. Review it before using this publication's SQL.",
            "sql_source_changed",
        )
    if binding.get("connection_sha256"):
        valid = current["connection_sha256"] == binding["connection_sha256"]
    else:
        from autoontology.engine.build import source_set

        valid = source_set([source])[0]["config"] == binding.get("legacy_source_config")
    if not valid:
        raise SqlError(
            "The source connection changed after this publication. Review the new target before querying it.",
            "sql_source_changed",
        )
    return source


def native_catalog(payload):
    if len(payload) > MAX_CATALOG_BYTES:
        raise SqlError(
            "The frozen SQL catalog exceeds the supported size.", "sql_catalog_large", 413
        )
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if (
                names.count("semantic_model.yaml") != 1
                or archive.getinfo("semantic_model.yaml").file_size > MAX_CATALOG_BYTES
            ):
                raise ValueError("invalid semantic export")
            semantic = yaml.safe_load(archive.read("semantic_model.yaml"))
        for extension in semantic.get("custom_extensions", []):
            if extension.get("vendor_name") == "NVIDIA_AUTO_ONTOLOGY":
                return json.loads(extension["data"])["native_document"]
    except Exception:
        pass
    raise SqlError(
        "The published source has no usable frozen SQL mapping. Rebuild and review the source.",
        "sql_catalog_missing",
    )


def project(model, name, native, binding):
    try:
        return _project(model, name, native, binding)
    except SqlError:
        raise
    except (KeyError, TypeError, AttributeError):
        raise SqlError(
            "The frozen physical/semantic mapping is incomplete or malformed.",
            "sql_catalog_invalid",
        ) from None


def _project(model, name, native, binding):
    """Do not expose sampled values or live/unapproved semantic attributes to a model."""
    classes = {
        c.term_id: c
        for c in model.classes.values()
        if c.source == "structured" and c.structured_source == name
    }
    terms = {t["id"]: t for t in native["semantic_layer"].get("terms", [])}
    if not classes or set(classes) - set(terms):
        raise SqlError(
            "The frozen mapping does not cover all published concepts.", "sql_catalog_mismatch"
        )
    databases = [
        d
        for d in native["data_layer"]["databases"]
        if d.get("id") == binding["catalog_database_id"] and d.get("name") == binding["database"]
    ]
    if len(databases) != 1:
        # Upstream's native database object may omit name; schemas carry it.
        databases = [
            d
            for d in native["data_layer"]["databases"]
            if d.get("id") == binding["catalog_database_id"]
            and all(s.get("database_name") == binding["database"] for s in d.get("schemas", []))
        ]
    if len(databases) != 1:
        raise SqlError(
            "The frozen mapping belongs to another source target.", "sql_catalog_mismatch"
        )
    database = databases[0]
    physical = {}
    column_index = {}
    for schema in database.get("schemas", []):
        for table in schema.get("tables", []):
            physical[table["id"]] = {
                "schema": schema["name"],
                "name": table["name"],
                "columns": table.get("columns", []),
            }
            column_index.update({c["id"]: (table["id"], c) for c in table.get("columns", [])})
    projected = {}
    concepts = []
    for term_id, cls in classes.items():
        term = terms[term_id]
        tables = term.get("represents", [])
        if not tables or any(t not in physical for t in tables):
            raise SqlError(
                "A published concept has no frozen table mapping.", "sql_catalog_mismatch"
            )
        for table_id in tables:
            table = physical[table_id]
            projected.setdefault(
                table_id, {"schema": table["schema"], "name": table["name"], "columns": {}}
            )
        props = {
            p.local: p
            for p in model.properties.values()
            if p.source == "structured" and p.domain == cls.local and p.owner == name
        }
        for attr in term.get("columns_attributes", []):
            prop = props.get("attr-" + slug(attr["id"]))
            if not prop:
                continue
            column_id = attr.get("column_id")
            if column_id not in column_index:
                raise SqlError(
                    "A published attribute has no frozen column mapping.", "sql_catalog_mismatch"
                )
            table_id, column = column_index[column_id]
            if table_id not in tables:
                raise SqlError(
                    "A published attribute belongs to another table.", "sql_catalog_mismatch"
                )
            projected[table_id]["columns"][column["name"]] = {
                "type": column.get("type", "text"),
                "label": prop.label,
                "description": prop.comment,
            }
        if len(props) != sum(
            1 for a in term.get("columns_attributes", []) if "attr-" + slug(a["id"]) in props
        ):
            raise SqlError(
                "The frozen catalog omits a published attribute.", "sql_catalog_mismatch"
            )
        concepts.append(
            {
                "id": cls.local,
                "label": cls.label,
                "aliases": cls.alt_labels,
                "description": cls.comment,
                "tables": [
                    {"schema": physical[t]["schema"], "name": physical[t]["name"]} for t in tables
                ],
            }
        )
    relationships = []
    for prop in model.properties.values():
        if (
            prop.source != "semantic_fk"
            or prop.owner != name
            or prop.domain not in {c.local for c in classes.values()}
        ):
            continue
        matched = []
        for fk in native["semantic_layer"].get("semantic_fks", []):
            pair = column_index.get(fk.get("column_id"))
            if not pair or pair[0] not in projected:
                continue
            table_id, column = pair
            table = physical[table_id]
            if prop.local.startswith(
                "fk-" + slug(fk["column_attribute_id"]) + "-"
            ) and prop.source_column == ".".join([table["schema"], table["name"], column["name"]]):
                matched.append(pair)
        if len(matched) != 1:
            raise SqlError(
                "An approved relationship has no unambiguous frozen column.", "sql_catalog_mismatch"
            )
        table_id, column = matched[0]
        table = physical[table_id]
        projected[table_id]["columns"][column["name"]] = {
            "type": column.get("type", "text"),
            "label": prop.label,
            "description": prop.comment,
        }
        relationships.append(
            {
                "label": prop.label,
                "source_column": prop.source_column,
                "domain": prop.domain,
                "range": prop.range,
            }
        )
    return {
        "version": 1,
        "status": "available",
        "source": name,
        "database": binding["database"],
        "dialect": database.get("dialect"),
        "binding": binding,
        "tables": sorted(projected.values(), key=lambda t: (t["schema"], t["name"])),
        "concepts": concepts,
        "relationships": relationships,
    }


def frozen_archive(store, settings, revision, model, name):
    snapshot = store.get_snapshot(revision, with_model=False)
    folder = settings.exports_dir / f"r{revision:04}"
    try:
        marker = json.loads((folder / "export.json").read_text())
        relative = f"upstream/{name}.ossie.zip"
        path = folder / relative
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("unsafe path")
        raw = path.read_bytes()
        from autoontology.store.publications import Publications

        publication = Publications(store).check_model(revision)
        if publication and hashlib.sha256(raw).hexdigest() != publication["manifest"][
            "artifacts"
        ].get(relative):
            raise ValueError("changed publication mapping")
        if (
            marker.get("revision") != revision
            or marker.get("snapshot_run") != snapshot.run_id
            or marker.get("workspace") != model.name
            or marker.get("file_sha256", {}).get(relative) != hashlib.sha256(raw).hexdigest()
        ):
            raise ValueError("unbound export")
        return raw
    except (OSError, ValueError, KeyError):
        raise SqlError(
            "The published SQL mapping is missing or changed. Verify the publication or rebuild the source.",
            "sql_catalog_invalid",
        ) from None


def binding_for(store, settings, revision, name, bindings):
    source = store.get_source(name)
    if source is None:
        raise SqlError("The published source is no longer registered.", "sql_source_changed")
    candidates = [
        s["sql_binding"]
        for b in bindings
        for s in b["sources"]
        if s.get("name") == name and s.get("sql_binding")
    ]
    unique = {digest(b): b for b in candidates}
    if len(unique) > 1:
        raise SqlError(
            "The publication spans different source targets. Resolve their mapping before querying data.",
            "sql_binding_ambiguous",
        )
    binding = next(iter(unique.values())) if unique else legacy_binding(store, revision, source)
    verify_binding(store, settings, name, binding)
    return binding


def catalog_for(store, settings, revision, model, name=None):
    from autoontology.store.publications import Publications

    names = [
        n
        for n in model.structured_sources
        if any(
            c.source == "structured" and c.structured_source == n for c in model.classes.values()
        )
    ]
    name = name or next(iter(names), None)
    if name not in names:
        raise SqlError(
            "Choose a structured source included in this operational version.",
            "source_not_published",
        )
    publication = Publications(store).check_model(revision)
    if publication and CATALOG_FILE in publication["manifest"]["artifacts"]:
        path = settings.exports_dir / f"r{revision:04}" / CATALOG_FILE
        try:
            raw = path.read_bytes()
            if (
                path.is_symlink()
                or len(raw) > MAX_CATALOG_BYTES
                or hashlib.sha256(raw).hexdigest()
                != publication["manifest"]["artifacts"][CATALOG_FILE]
            ):
                raise ValueError("changed catalog")
            envelope = json.loads(raw)
            from autoontology.ontology.model import MergedOntology

            full_model = MergedOntology.from_dict(store.get_snapshot(revision).model)
            if envelope["model_sha256"] != digest(full_model.to_dict()):
                raise ValueError("different model")
            selected = next(s for s in envelope["sources"] if s["source"] == name)
            if selected["status"] != "available":
                raise SqlError(selected["detail"], selected["error"])
            verify_binding(store, settings, name, selected["binding"])
            if digest(model.to_dict()) != digest(full_model.to_dict()):
                from autoontology.security.views import verify_view_authority

                if not getattr(model, "_access_view", {}).get("restricted"):
                    raise ValueError("unapproved model")
                verify_view_authority(store, model, "query.run")
                return project(
                    model,
                    name,
                    native_catalog(frozen_archive(store, settings, revision, full_model, name)),
                    selected["binding"],
                )
            return selected
        except SqlError:
            raise
        except (OSError, ValueError, KeyError, StopIteration):
            raise SqlError(
                "The frozen SQL catalog is missing or changed.", "sql_catalog_invalid"
            ) from None
    bindings = (
        publication["manifest"]["source_bindings"]
        if publication
        else Publications(store).bindings([revision])
    )
    binding = binding_for(store, settings, revision, name, bindings)
    return project(
        model, name, native_catalog(frozen_archive(store, settings, revision, model, name)), binding
    )


def write_catalog(store, settings, model, revision, folder, bindings):
    sources = []
    for name in model.structured_sources:
        if not any(
            c.source == "structured" and c.structured_source == name for c in model.classes.values()
        ):
            continue
        try:
            binding = binding_for(store, settings, revision, name, bindings)
            raw = (folder / "upstream" / f"{name}.ossie.zip").read_bytes()
            sources.append(project(model, name, native_catalog(raw), binding))
        except (SqlError, OSError) as exc:
            sources.append(
                {
                    "source": name,
                    "status": "unavailable",
                    "error": getattr(exc, "code", "sql_catalog_missing"),
                    "detail": str(exc)
                    if isinstance(exc, SqlError)
                    else "No frozen mapping is available for this source.",
                }
            )
    if not sources:
        return
    payload = json.dumps(
        {"version": 1, "model_sha256": digest(model.to_dict()), "sources": sources},
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    if len(payload) > MAX_CATALOG_BYTES:
        raise SqlError("The SQL catalog exceeds the supported size.", "sql_catalog_large", 413)
    (folder / CATALOG_FILE).write_bytes(payload)
    marker_path = folder / "export.json"
    marker = json.loads(marker_path.read_text())
    marker["files"] = sorted(set(marker["files"] + [CATALOG_FILE]))
    marker["file_sha256"][CATALOG_FILE] = hashlib.sha256(payload).hexdigest()
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2))
