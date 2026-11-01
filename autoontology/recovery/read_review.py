# SPDX-License-Identifier: MIT
"""Exact OS-owner review of a restored local read target; never renew semantic approval."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from autoontology.config import Settings
from autoontology.recovery import files, profile, runtime, staging
from autoontology.recovery import transfer_registry as registry
from autoontology.security import owner_registry as host
from autoontology.security.credentials import Secret
from autoontology.security.owner_adoption import _audit
from autoontology.security.owner_recovery import _principal
from autoontology.security.policy import AccessError, Actor
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.store.operations import digest

MAX_BYTES = 2 * 1024 * 1024


def held():
    return AccessError(
        "Review the exact restored data target locally before running queries; preserve the proposal and transfer journal.",
        "restored_catalog_review_required",
        409,
    )


def _boundary(name):
    """Durable read-review boundaries used by interruption acceptance."""


def _settings(destination):
    env = profile.read_env(destination / "local.env")
    if env.get("POSTGRES_HOST") != "127.0.0.1":
        raise held()
    result = Settings(
        home=Path(env["AUTOONTOLOGY_HOME"]),
        exports_dir=Path(env["AUTOONTOLOGY_EXPORTS_DIR"]),
        port=int(env["AUTOONTOLOGY_PORT"]),
        backend_port=int(env["BACKEND_PORT"]),
        postgres_host="127.0.0.1",
        postgres_port=int(env["POSTGRES_PORT"]),
        postgres_user=env["POSTGRES_USER"],
        postgres_password=env.get("POSTGRES_PASSWORD", ""),
        postgres_database=env.get("POSTGRES_DATABASE", "autoontology"),
    )
    if result.home != destination / "workspace" or result.exports_dir != destination / "exports":
        raise held()
    return result


def _current(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='ops_state'").fetchone()
    row = (
        conn.execute("SELECT published_revision FROM ops_state WHERE id=1").fetchone()
        if exists
        else None
    )
    revision = row[0] if row else None
    snapshot = host._dict_row(
        conn, "SELECT * FROM ontology_snapshots WHERE revision=?", (revision,)
    )
    publication = host._dict_row(conn, "SELECT * FROM publications WHERE revision=?", (revision,))
    sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY id")]
    return digest(
        {"revision": revision, "snapshot": snapshot, "publication": publication, "sources": sources}
    )


def _schema(settings, database, expected, destination):
    """Read schema only from the pinned local cluster; never read business rows."""
    import psycopg

    from autoontology.config import assert_source_database

    assert_source_database(settings, database)
    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=settings.postgres_port,
            dbname=database,
            user=settings.postgres_user,
            password=settings.postgres_password or None,
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000 -c search_path=pg_catalog",
        ) as conn:
            location, system_id = conn.execute(
                "SELECT current_setting('data_directory'),system_identifier::text FROM pg_control_system()"
            ).fetchone()
            if Path(location).resolve() != (destination / "postgres").resolve():
                raise held()
            columns = conn.execute("""SELECT n.nspname,c.relname,a.attname,format_type(a.atttypid,a.atttypmod)
                FROM pg_attribute a JOIN pg_class c ON a.attrelid=c.oid
                JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p')
                AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY n.nspname,c.relname,a.attnum""").fetchall()
            actual = {(s, t): [] for s, t, _, _ in columns}
            for schema, table, name, kind in columns:
                actual[(schema, table)].append([name, kind])
            for table in expected:
                found = actual.get((table["schema"], table["name"]))
                if found != table["columns"]:
                    raise held()
            return {
                "system_identifier": system_id,
                "database": database,
                "tables": expected,
                "schema_sha256": digest(expected),
                "data_time": "live_at_execution; rows not archived by this review",
            }
    except psycopg.Error:
        raise held() from None


def _inspect(store, settings):
    from autoontology.ontology.model import MergedOntology
    from autoontology.playground import sql_catalog as sql
    from autoontology.store.publications import Publications, artifact_hashes, index_receipt
    from autoontology.structured.pipeline import catalog_database_name

    with store._conn() as conn:
        row = conn.execute("SELECT published_revision FROM ops_state WHERE id=1").fetchone()
    revision = row[0] if row else None
    if revision is None:
        raise held()
    publications = Publications(store)
    snapshot = store.get_snapshot(revision)
    model = MergedOntology.from_dict(snapshot.model)
    publication = publications.get(revision)
    if publication is not None:
        verified = publications.verify(revision, settings.exports_dir)
        if not verified["ok"]:
            raise held()
        manifest = publication["manifest"]
        approval_evidence = "existing committed publication; no renewed business approval"
    else:
        # Old operational selections predate publication manifests. Validate their
        # actual bytes/index/originals, but never mint a publication or approver.
        folder = settings.exports_dir / f"r{revision:04}"
        artifacts = artifact_hashes(folder)
        marker = json.loads((folder / "export.json").read_text())
        if (
            marker.get("revision") != revision
            or marker.get("snapshot_run") != snapshot.run_id
            or marker.get("workspace") != model.name
            or marker.get("file_sha256") != artifacts
            or set(marker.get("files", [])) != set(artifacts) | {"export.json"}
        ):
            raise held()
        originals = publications.originals(model)
        manifest = {
            "legacy": True,
            "revision": revision,
            "model_sha256": digest(model.to_dict()),
            "artifacts": artifacts,
            "originals": originals,
            "index": index_receipt(store, f"{model.name}@r{revision}", model),
            "source_bindings": publications.bindings([revision]),
        }
        verified = {"originals": len(originals)}
        approval_evidence = (
            "legacy operational selection; historical approver unverified; no publication minted"
        )
    catalogs = []
    for name in model.structured_sources:
        source = store.get_source(name)
        if source is None or source.kind != "files" or not source.pg_database:
            # External URIs and unknown adapters require their own explicit contract.
            raise held()
        candidates = [
            s["sql_binding"]
            for b in manifest["source_bindings"]
            for s in b["sources"]
            if s.get("name") == name and s.get("sql_binding")
        ]
        unique = {digest(b): b for b in candidates}
        if len(unique) > 1:
            raise held()
        binding = (
            next(iter(unique.values())) if unique else sql.legacy_binding(store, revision, source)
        )
        if (
            binding["source_id"] != source.id
            or binding["catalog_database_id"] != source.catalog_database_id
            or binding["database"] != catalog_database_name(source)
        ):
            raise held()
        native = sql.native_catalog(sql.frozen_archive(store, settings, revision, model, name))
        catalog = sql.project(model, name, native, binding)
        # Match all frozen physical columns, including those hidden by the semantic projection.
        native_database = next(
            d for d in native["data_layer"]["databases"] if d["id"] == source.catalog_database_id
        )
        expected = sorted(
            [
                {
                    "schema": s["name"],
                    "name": t["name"],
                    "columns": [[c["name"], c["type"]] for c in t["columns"]],
                }
                for s in native_database["schemas"]
                for t in s["tables"]
            ],
            key=lambda t: (t["schema"], t["name"]),
        )
        physical = _schema(settings, binding["database"], expected, settings.home.parent)
        catalogs.append(
            {
                "source": name,
                "source_id": source.id,
                "source_sha256": digest(asdict(source)),
                "historical_binding_sha256": digest(binding),
                "current_binding": sql.source_binding(source, settings),
                "projected_catalog_sha256": digest(catalog),
                "physical": physical,
            }
        )
    return {
        "revision": revision,
        "publication_id": publication["id"] if publication else None,
        "manifest_sha256": digest(manifest),
        "approval_evidence": approval_evidence,
        "catalogs": catalogs,
        "originals_verified": verified["originals"],
        "scope": "read queries only; input locators, ingestion, collection and effects remain held",
        "semantic_approval": "historical approval preserved; physical OS-owner review only",
    }


def _proposal_path(destination, sha):
    if not isinstance(sha, str) or not host.SHA.fullmatch(sha):
        raise held()
    return Path(destination) / "recovery/read-proposals" / (sha + ".json")


def _load(destination, sha):
    from autoontology.recovery.checkpoint import _file

    with _file(_proposal_path(destination, sha)) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise held()
    value = json.loads(raw)
    if digest(value) != sha:
        raise held()
    return value


def _proposal(store, settings, journal):
    with store._conn() as conn:
        before = conn.execute("SELECT policy_version FROM identity_state WHERE id=1").fetchone()[0]
        current = _current(conn)
    return {
        "version": 1,
        "purpose": "restored-local-read-target",
        "workspace_id": journal["workspace_id"],
        "operation_id": journal["operation_id"],
        "transfer_event_sha256": journal["event_sha256"],
        "destination_database": str(store.path),
        "runtime_hashes_sha256": digest(journal["runtime_hashes"]),
        "before_policy_version": before,
        "state_sha256": current,
        "inspection": _inspect(store, settings),
    }


def prepare(destination):
    staging._owner()
    destination = Path(destination).absolute()
    try:
        marker = runtime._validate(destination)
        with registry.locked(marker["workspace_id"]):
            journal = registry.read(marker["workspace_id"])
            store = Store.for_home(destination / "workspace")
            proposal = _proposal(store, _settings(destination), journal)
            raw = json.dumps(
                proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            if len(raw) > MAX_BYTES:
                raise held()
            sha = digest(proposal)
            files.write_file(_proposal_path(destination, sha), raw, journal["operation_id"])
            return {
                "sha256": sha,
                "proposal": proposal,
                "query_ready": False,
                "human_business_approval": False,
            }
    except AccessError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        raise held() from None


def _event(conn, journal, proposal, sha):
    row = host._dict_row(
        conn,
        "SELECT * FROM identity_events WHERE action='identity.restored_reads_enabled' AND json_extract(detail_json,'$.proposal_sha256')=?",
        (sha,),
    )
    if row is None:
        return None
    stamp = row.pop("sha256")
    detail = json.loads(row["detail_json"])
    if (
        digest(row) != stamp
        or row["actor_id"] != f"local-os-owner:{journal['os_uid']}"
        or row["credential_id"] is not None
        or row["target"] != journal["workspace_id"]
        or detail
        != {
            "proposal_sha256": sha,
            "transfer_event_sha256": journal["event_sha256"],
            "policy_version": proposal["before_policy_version"] + 1,
            "human_business_approval": False,
        }
    ):
        raise held()
    return stamp


def _public(journal):
    review = journal["catalog_review"]
    return {
        "query_ready": journal["catalog_ready"],
        "proposal_sha256": review["proposal_sha256"],
        "event_sha256": review["event_sha256"],
        "workspace_id": journal["workspace_id"],
        "other_effects_ready": False,
        "human_business_approval": False,
    }


def confirm(destination, sha, *, resume=False):
    staging._owner()
    destination = Path(destination).absolute()
    try:
        marker = runtime._validate(destination)
        with registry.locked(marker["workspace_id"]) as directory, host.locked(marker["database"]):
            journal = registry.read(marker["workspace_id"], directory)
            proposal = _load(destination, sha)
            if any(
                proposal[k] != v
                for k, v in {
                    "workspace_id": journal["workspace_id"],
                    "operation_id": journal["operation_id"],
                    "transfer_event_sha256": journal["event_sha256"],
                    "destination_database": marker["database"],
                    "runtime_hashes_sha256": digest(journal["runtime_hashes"]),
                }.items()
            ):
                raise held()
            prior = journal.get("catalog_review")
            if prior and prior["proposal_sha256"] == sha:
                if not resume:
                    raise held()
            elif resume:
                raise held()
            store = Store.for_home(destination / "workspace")
            # Re-inspect the live local target before reserving or completing this exact decision.
            if _inspect(store, _settings(destination)) != proposal["inspection"]:
                raise held()
            with store._immediate() as conn:
                state = Identity(store)._state(conn)
                if _current(conn) != proposal["state_sha256"]:
                    raise held()
                existing = _event(conn, journal, proposal, sha)
                if state["policy_version"] != proposal["before_policy_version"] + int(
                    existing is not None
                ):
                    raise held()
                _audit(conn)
                _principal(conn, journal["username"], journal["workspace_id"])
                if prior and prior["phase"] == "active" and existing:
                    return _public(journal)
                if existing is None:
                    # A different explicit new proposal may supersede a held stale review.
                    journal = {
                        **journal,
                        "catalog_ready": False,
                        "catalog_review": {
                            "phase": "pending",
                            "proposal_sha256": sha,
                            "event_sha256": None,
                        },
                    }
                    registry.put(directory, journal)
                    _boundary("after_reserve")
                    Identity(store)._bump(conn)
                    Identity(store)._event(
                        conn,
                        Actor(
                            f"local-os-owner:{journal['os_uid']}",
                            None,
                            "human",
                            "Local OS owner",
                            Secret("host-proof"),
                        ),
                        "identity.restored_reads_enabled",
                        journal["workspace_id"],
                        {
                            "proposal_sha256": sha,
                            "transfer_event_sha256": journal["event_sha256"],
                            "policy_version": proposal["before_policy_version"] + 1,
                            "human_business_approval": False,
                        },
                    )
                    existing = _event(conn, journal, proposal, sha)
                _boundary("before_commit")
            _boundary("after_commit")
            journal = {
                **journal,
                "catalog_ready": True,
                "catalog_review": {
                    "phase": "active",
                    "proposal_sha256": sha,
                    "event_sha256": existing,
                },
            }
            _boundary("before_activate")
            registry.put(directory, journal)
            _boundary("after_activate")
            return _public(journal)
    except AccessError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        raise held() from None


def _checked(store, conn):
    marker = host.read(store.path)
    if marker is None or marker.get("version") != 4:
        raise held()
    runtime._validate(store.path.parent.parent)
    journal = registry.read(marker["workspace_id"])
    review = journal.get("catalog_review")
    if not journal["catalog_ready"] or not review or review["phase"] != "active":
        raise held()
    proposal = _load(store.path.parent.parent, review["proposal_sha256"])
    if (
        _current(conn) != proposal["state_sha256"]
        or conn.execute("SELECT policy_version FROM identity_state WHERE id=1").fetchone()[0]
        != proposal["before_policy_version"] + 1
        or _event(conn, journal, proposal, review["proposal_sha256"]) != review["event_sha256"]
    ):
        raise held()
    return proposal


def checked(store, conn):
    try:
        return _checked(store, conn)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.DatabaseError):
        # A lost ancillary proof closes queries, not the independently verified
        # original identity or historical-read capabilities.
        raise held() from None


def status(store):
    try:
        with store._conn() as conn:
            checked(store, conn)
        return {"query_ready": True, "other_effects_ready": False}
    except (AccessError, OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        return {"query_ready": False, "other_effects_ready": False}


def allow_binding(store, settings, source, binding):
    """An exact new physical target overlays, never edits, the historical binding."""
    from autoontology.playground.sql_catalog import source_binding

    marker = host.read(store.path)
    if marker is None or marker.get("version") != 4:
        return False
    try:
        with store._conn() as conn:
            proposal = checked(store, conn)
        selected = next(
            c for c in proposal["inspection"]["catalogs"] if c["source_id"] == source.id
        )
        if (
            selected["source_sha256"] != digest(asdict(source))
            or selected["historical_binding_sha256"] != digest(binding)
            or selected["current_binding"] != source_binding(source, settings)
            or settings.home != store.path.parent
        ):
            raise held()
        if (
            _schema(
                settings, binding["database"], selected["physical"]["tables"], settings.home.parent
            )
            != selected["physical"]
        ):
            raise held()
        return True
    except (OSError, ValueError, KeyError, TypeError, StopIteration, sqlite3.DatabaseError):
        raise held() from None


def trace_binding(store, conn):
    marker = host.read(store.path)
    if marker is None or marker.get("version") != 4:
        return None
    proposal = checked(store, conn)
    journal = registry.read(marker["workspace_id"])
    return {
        "proposal_sha256": journal["catalog_review"]["proposal_sha256"],
        "review_event_sha256": journal["catalog_review"]["event_sha256"],
        "transfer_event_sha256": journal["event_sha256"],
        "policy_version": proposal["before_policy_version"] + 1,
        "semantic_approval": "historical; physical target review does not renew business approval",
    }


def status_path(path):
    from contextlib import closing
    from types import SimpleNamespace

    try:
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            checked(SimpleNamespace(path=Path(path)), conn)
        return {"query_ready": True, "other_effects_ready": False}
    except (AccessError, OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        return {"query_ready": False, "other_effects_ready": False}
