# SPDX-License-Identifier: MIT
"""First authority transfer from a verified v1/v2 home to an inactive restored home."""

import datetime
import hashlib
import json
import os
import sqlite3
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from autoontology.recovery import files, quiescence, runtime, staging
from autoontology.recovery import transfer_registry as registry
from autoontology.security import owner_registry as host
from autoontology.security.credentials import Secret, check_password, hash_password
from autoontology.security.enrollment import database_enrollment_state
from autoontology.security.owner_adoption import _audit
from autoontology.security.owner_recovery import _principal
from autoontology.security.policy import CAPABILITIES, AccessError, Actor
from autoontology.store.identity import Identity, _username
from autoontology.store.operations import digest

READ_ACTIONS = frozenset(
    action
    for action, capability in CAPABILITIES.items()
    if capability.level == "read" and action != "query.run"
) | {"identity.admin"}


def _boundary(name):
    """Durable first-transfer boundaries for exception and process-death acceptance."""


def _pin(journal, role, phase, event=None):
    return {
        "version": 4,
        "kind": "transfer",
        "role": role,
        "phase": phase,
        "database": journal["source_database"]
        if role == "source"
        else journal["destination_database"],
        **{
            k: journal[k]
            for k in (
                "source_database",
                "destination_database",
                "workspace_id",
                "organization_id",
                "operation_id",
                "principal_id",
                "manifest_sha256",
                "runtime_hashes",
                "os_uid",
            )
        },
        "event_sha256": event,
    }


def receipt(conn, marker):
    state = database_enrollment_state(conn)
    if state is None or any(state[k] != marker[k] for k in ("workspace_id", "organization_id")):
        raise registry.error()
    row = host._dict_row(
        conn, "SELECT * FROM identity_events WHERE sha256=?", (marker["event_sha256"],)
    )
    if row is None:
        raise registry.error()
    stamp = row.pop("sha256")
    detail = json.loads(row["detail_json"])
    if (
        digest(row) != stamp
        or row["action"] != "identity.workspace_restored"
        or row["target"] != marker["workspace_id"]
        or row["actor_id"] != f"local-os-owner:{marker['os_uid']}"
        or row["credential_id"] is not None
        or any(
            detail.get(k) != marker[k]
            for k in (
                "operation_id",
                "manifest_sha256",
                "source_database",
                "destination_database",
                "principal_id",
            )
        )
        or detail.get("runtime_hashes_sha256") != registry.digest(marker["runtime_hashes"])
        or type(detail.get("policy_version")) is not int
        or state["policy_version"] < detail["policy_version"]
    ):
        raise registry.error()
    return {**detail, "event_sha256": stamp}


def check_snapshot(conn, marker):
    if marker["role"] != "destination" or marker["phase"] != "active":
        raise registry.error()
    result = receipt(conn, marker)
    if "recovery" in marker:
        from autoontology.security.owner_recovery import check_active

        check_active(conn, database_enrollment_state(conn), marker)
    return result


def require_action(store, action, *, conn):
    marker = host.read(store.path)
    if marker is not None and marker.get("version") == 4 and action not in READ_ACTIONS:
        if action == "query.run":
            from autoontology.recovery.read_review import checked

            checked(store, conn)
            return
        raise AccessError(
            "This restored home requires physical catalog/source review before queries, preparation or business execution. Historical reads and identity administration remain available.",
            "restored_catalog_review_required",
            409,
        )


@contextmanager
def _source_lock(path):
    conn = None
    try:
        if Path(path).exists():
            conn = sqlite3.connect(
                Path(path).as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None
            )
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    raise quiescence.busy() from None
                conn.close()
                conn = None
            except sqlite3.DatabaseError:
                conn.close()
                conn = None
        yield conn
    finally:
        if conn is not None:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()


def _db(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _status(journal):
    from autoontology.recovery.read_review import status_path

    ready = (
        journal["phase"] == "active"
        and status_path(Path(journal["destination_database"]))["query_ready"]
    )
    return {
        k: journal[k]
        for k in ("workspace_id", "organization_id", "operation_id", "principal_id", "event_sha256")
    } | {
        "phase": journal["phase"],
        "authority_active": journal["phase"] == "active",
        "runtime_start_supported": journal["phase"] == "active",
        "catalog_ready": ready,
        "source_authority_retired": journal["phase"] != "prepared",
    }


def status(destination):
    staging._owner()
    marker = host.read(Path(destination) / "workspace/autoontology.db")
    if marker is None or marker.get("version") not in {3, 4}:
        raise registry.error()
    journal = registry.read(marker["workspace_id"])
    if journal is None or marker["operation_id"] != journal["operation_id"]:
        raise registry.error()
    if marker.get("version") == 3:
        if journal["phase"] == "active" or marker != journal["staged_pin"]:
            raise registry.error()
        return _status(journal)
    if journal["phase"] == "active":
        registry.guard_binding(marker)
        with closing(
            sqlite3.connect(Path(marker["database"]).as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.execute("BEGIN")
            check_snapshot(conn, marker)
    return _status(journal)


def _apply(conn, journal, password_record):
    state = database_enrollment_state(conn)
    _audit(conn)
    principal = _principal(conn, journal["username"], journal["workspace_id"])
    if (
        state["policy_version"] != journal["backup_policy"]
        or principal["epoch"] != journal["before_epoch"]
        or principal["id"] != journal["principal_id"]
    ):
        raise registry.error()
    now = time.time()
    stamp_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    counts = {}
    counts["sessions_revoked"] = conn.execute(
        "UPDATE identity_credentials SET revoked_at=? WHERE revoked_at IS NULL", (now,)
    ).rowcount
    counts["delegations_revoked"] = conn.execute(
        "UPDATE identity_delegations SET revoked_at=? WHERE revoked_at IS NULL", (now,)
    ).rowcount
    counts["other_principals_disabled"] = conn.execute(
        "UPDATE identity_principals SET status='disabled' WHERE id!=? AND status='active'",
        (principal["id"],),
    ).rowcount
    conn.execute("UPDATE identity_principals SET epoch=epoch+1")
    conn.execute(
        "UPDATE identity_principals SET password_hash=? WHERE id=?",
        (password_record, principal["id"]),
    )
    conn.execute(
        "UPDATE identity_state SET policy_version=? WHERE id=1", (journal["policy_version"],)
    )
    counts["host_roots_revoked"] = conn.execute(
        "UPDATE host_file_roots SET active=0,reason='restored-runtime-reapproval-required',updated_at=? WHERE active=1",
        (stamp_time,),
    ).rowcount
    counts["schedules_paused"] = conn.execute(
        "UPDATE collection_schedules SET enabled=0,version=version+1,reason='restored-runtime-paused',updated_at=? WHERE enabled=1",
        (now,),
    ).rowcount
    counts["jobs_cancelled"] = conn.execute(
        "UPDATE collection_jobs SET status='cancelled',generation=generation+1,lease_id=NULL,credential_id=NULL,lease_until=NULL,error_code='restore_reauthorization_required',updated_at=? WHERE status IN ('queued','retry','running')",
        (now,),
    ).rowcount
    counts["workers_cleared"] = conn.execute("DELETE FROM collection_workers").rowcount
    counts["leases_cleared"] = conn.execute("DELETE FROM source_leases").rowcount
    conn.execute(
        "UPDATE steps SET status='cancelled' WHERE run_id IN (SELECT id FROM runs WHERE status IN ('pending','running')) AND status IN ('pending','running')"
    )
    counts["runs_cancelled"] = conn.execute(
        "UPDATE runs SET status='cancelled',finished_at=?,error='restored-runtime-reauthorization-required' WHERE status IN ('pending','running')",
        (stamp_time,),
    ).rowcount
    detail = {
        k: journal[k]
        for k in (
            "operation_id",
            "manifest_sha256",
            "source_database",
            "destination_database",
            "principal_id",
            "policy_version",
        )
    }
    detail.update(
        counts,
        backup_policy=journal["backup_policy"],
        principal_epoch=journal["before_epoch"] + 1,
        runtime_hashes_sha256=registry.digest(journal["runtime_hashes"]),
        catalog_ready=False,
        approval_history="preserved; no new business approval",
    )
    Identity(None)._event(
        conn,
        Actor(
            f"local-os-owner:{journal['os_uid']}",
            None,
            "human",
            "Local OS owner",
            Secret("host-proof"),
        ),
        "identity.workspace_restored",
        journal["workspace_id"],
        detail,
    )
    return conn.execute("SELECT sha256 FROM identity_events ORDER BY id DESC LIMIT 1").fetchone()[0]


def activate(destination, username, password, *, workspace_id, resume=False):
    staging._owner()
    username = _username(username)
    password_record = hash_password(password)
    if not isinstance(workspace_id, str) or not host.ID.fullmatch(workspace_id):
        raise registry.error()
    destination = Path(destination).absolute()
    database = destination / "workspace/autoontology.db"
    try:
        with registry.locked(workspace_id) as directory:
            previous = registry.read(workspace_id, directory)
            marker = host.read(database)
            if previous is not None:
                if (
                    previous["username"] != username
                    or host.database_path(previous["destination_database"])
                    != host.database_path(database)
                    or not resume
                ):
                    raise registry.error()
                journal = previous
                if journal["phase"] == "active":
                    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                        conn.execute("BEGIN")
                        check_snapshot(conn, marker)
                        principal = _principal(conn, username, workspace_id)
                        if not check_password(password, principal["password_hash"]):
                            raise AccessError(
                                "The password does not match this completed transfer.",
                                "invalid_credentials",
                                401,
                            )
                    return _status(journal)
            else:
                if (
                    resume
                    or marker is None
                    or marker.get("version") != 3
                    or marker["phase"] != "staged"
                    or marker["workspace_id"] != workspace_id
                ):
                    raise registry.error()
                staging._result(marker, destination)
                source = host.read(marker["source_database"])
                if source is None or registry.digest(source) != marker["source_pin_sha256"]:
                    raise registry.error()
                proof = quiescence.check(marker["source_database"], destination, marker["ports"])
                with closing(
                    sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
                ) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute("BEGIN")
                    state = database_enrollment_state(conn)
                    _audit(conn)
                    principal = _principal(conn, username, workspace_id)
                    if conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] < 20:
                        raise registry.error()
                generated = runtime.generated(destination)
                hashes = {
                    name: files.stamp(destination / name)
                    for name in (
                        "local.env",
                        "postgres/postgresql.conf",
                        "postgres/postgresql.auto.conf",
                        "postgres/pg_hba.conf",
                        "postgres/pg_ident.conf",
                    )
                }
                hashes.update(
                    {name: hashlib.sha256(data).hexdigest() for name, data in generated.items()}
                )
                journal = {
                    "version": 1,
                    "kind": "workspace-transfer",
                    "phase": "prepared",
                    "catalog_ready": False,
                    "workspace_id": workspace_id,
                    "organization_id": marker["organization_id"],
                    "operation_id": marker["operation_id"],
                    "source_database": marker["source_database"],
                    "destination_database": marker["database"],
                    "current_database": marker["source_database"],
                    "source_pin": source,
                    "source_pin_sha256": registry.digest(source),
                    "staged_pin": marker,
                    "manifest_sha256": marker["manifest_sha256"],
                    "username": username,
                    "principal_id": principal["id"],
                    "before_epoch": principal["epoch"],
                    "backup_policy": state["policy_version"],
                    "policy_version": state["policy_version"] + 1,
                    "os_uid": os.getuid(),
                    "event_sha256": None,
                    "runtime_hashes": hashes,
                    "old_control_sha256": files.stamp(destination / "control.py"),
                    "quiescence": proof,
                }
            paths = sorted({journal["source_database"], journal["destination_database"]})
            with ExitStack() as stack:
                locks = {p: stack.enter_context(host.locked(p)) for p in paths}
                if previous is None:
                    if (
                        host._read(locks[journal["source_database"]], journal["source_database"])
                        != journal["source_pin"]
                    ):
                        raise registry.error()
                    stack.enter_context(_source_lock(journal["source_database"]))
                    registry.put(directory, journal)
                    _boundary("after_prepare")
                elif journal["phase"] == "prepared":
                    quiescence.check(
                        journal["source_database"], destination, journal["staged_pin"]["ports"]
                    )
                    stack.enter_context(_source_lock(journal["source_database"]))
                source_current = host._read(
                    locks[journal["source_database"]], journal["source_database"]
                )
                if journal["phase"] == "prepared":
                    if source_current not in (
                        journal["source_pin"],
                        _pin(journal, "source", "pending"),
                    ):
                        raise registry.error()
                    host.write(
                        locks[journal["source_database"]],
                        journal["source_database"],
                        _pin(journal, "source", "pending"),
                    )
                    journal = {**journal, "phase": "pending", "current_database": None}
                    registry.put(directory, journal)
                    _boundary("after_source_fence")
                if journal["phase"] == "pending":
                    permitted_sources = [_pin(journal, "source", "pending")]
                    if journal["event_sha256"]:
                        permitted_sources.append(
                            _pin(journal, "source", "retired", journal["event_sha256"])
                        )
                    current_source = host._read(
                        locks[journal["source_database"]], journal["source_database"]
                    )
                    if current_source not in permitted_sources:
                        raise registry.error()
                current_target = host._read(locks[journal["destination_database"]], database)
                permitted_targets = [journal["staged_pin"], _pin(journal, "destination", "pending")]
                if journal["event_sha256"]:
                    permitted_targets.append(
                        _pin(journal, "destination", "active", journal["event_sha256"])
                    )
                if current_target not in permitted_targets:
                    raise registry.error()
                host.write(
                    locks[journal["destination_database"]],
                    database,
                    _pin(journal, "destination", "pending"),
                )
                with closing(_db(database)) as conn:
                    try:
                        existing = host._dict_row(
                            conn,
                            "SELECT sha256 FROM identity_events WHERE action='identity.workspace_restored' AND json_extract(detail_json,'$.operation_id')=?",
                            (journal["operation_id"],),
                        )
                        if existing:
                            stamp = existing["sha256"]
                            receipt(conn, _pin(journal, "destination", "active", stamp))
                            _audit(conn)
                            principal = _principal(conn, username, workspace_id)
                            if not check_password(password, principal["password_hash"]):
                                raise AccessError(
                                    "The password does not match the committed transfer.",
                                    "invalid_credentials",
                                    401,
                                )
                        else:
                            stamp = _apply(conn, journal, password_record)
                        _boundary("before_commit")
                        conn.execute("COMMIT")
                    except BaseException:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        raise
                _boundary("after_commit")
                runtime.install(destination, journal)
                for name, expected in journal["runtime_hashes"].items():
                    if files.stamp(destination / name) != expected:
                        raise registry.error()
                _boundary("after_runtime_files")
                journal = {**journal, "event_sha256": stamp}
                registry.put(directory, journal)
                host.write(
                    locks[journal["source_database"]],
                    journal["source_database"],
                    _pin(journal, "source", "retired", stamp),
                )
                host.write(
                    locks[journal["destination_database"]],
                    database,
                    _pin(journal, "destination", "active", stamp),
                )
                _boundary("before_master_active")
                journal = {
                    **journal,
                    "phase": "active",
                    "current_database": journal["destination_database"],
                }
                registry.put(directory, journal)
                _boundary("after_master_active")
                return _status(journal)
    except AccessError:
        raise
    except (OSError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        raise registry.error() from None
