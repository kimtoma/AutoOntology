# SPDX-License-Identifier: MIT
"""Ontology Ops routes: operational work, grounded reviews and staged publication."""

from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from autoontology.engine.build import WORKSPACE_LEASE
from autoontology.engine.jobs import hold_sources
from autoontology.provenance import change_originals
from autoontology.store.db import now_iso
from autoontology.store.operations import Operations, OperationsError, digest
from autoontology.web.api import ApiError
from autoontology.web.runs import WORKSPACE, RunStartError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewIn(Strict):
    candidate: int = Field(ge=1)
    decision: Literal["accept", "reject", "hold"]
    note: str = Field(default="", max_length=2000)
    edits: dict = Field(default_factory=dict)


class CandidateIn(Strict):
    revision: int = Field(ge=1)


class PublishIn(CandidateIn):
    token: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class RestorePrepareIn(CandidateIn):
    reason: str = Field(min_length=1, max_length=2000)


class RestoreExecuteIn(Strict):
    confirmation: str = Field(min_length=1, max_length=160)


class FeedbackIn(Strict):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=20000)
    note: str = Field(min_length=1, max_length=4000)


class CheckIn(Strict):
    question: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=2000)


@contextmanager
def edit(api, kind):
    try:
        api.runs.claim_idle(WORKSPACE, kind)
    except RunStartError as exc:
        raise ApiError(exc.status, "workspace_busy", str(exc)) from exc
    try:
        with hold_sources(api.store, [WORKSPACE_LEASE], kind):
            yield
    finally:
        api.runs.release(WORKSPACE)


def install(app, api):
    # Opt empty workspaces in before any first build can be served as operational.
    # Existing legacy workspaces retain their explicit migration baseline.
    if api.store.latest_revision() is None:
        Operations(api.store)
    router = APIRouter(prefix="/api/ops")

    def ops():
        return Operations(api.store)

    def checked(call):
        from autoontology.engine.jobs import JobError
        from autoontology.store.publications import PublicationError
        from autoontology.store.restorations import RestoreError

        try:
            return call()
        except RestoreError as exc:
            raise ApiError(exc.status, exc.code, str(exc)) from exc
        except JobError as exc:
            raise ApiError(409, "workspace_busy", str(exc)) from exc
        except PublicationError as exc:
            raise ApiError(409, "publication_invalid", str(exc)) from exc
        except OperationsError as exc:
            raise ApiError(409, "operations_changed", str(exc)) from exc

    @router.get("")
    def overview():
        op = ops()
        state = op.state()
        items = op.changes()
        head = (
            api.store.get_snapshot(state["published_revision"], with_model=False)
            if state["published_revision"]
            else None
        )
        decisions = [i["decision"]["decision"] if i["decision"] else "pending" for i in items]
        return {
            "published_revision": state["published_revision"],
            "candidate_revision": state["candidate_revision"],
            "workspace": api.settings.workspace,
            "counts": {
                "pending": decisions.count("pending"),
                "held": decisions.count("hold"),
                "approved": decisions.count("accept"),
                "rejected": decisions.count("reject"),
                "feedback": len(op.feedback()),
            },
            "published_at": head.created_at if head else None,
            "highlights": change_originals(api.store, items[:3]),
            "checks": op.checks(),
            "initial_approval_required": state["published_revision"] is None,
            "publication_policy": {"minimum_questions": 1},
        }

    @router.get("/changes")
    def changes():
        op = ops()
        return {
            **op.state(),
            "items": change_originals(api.store, op.changes()),
            "feedback": op.feedback(),
        }

    @router.post("/changes/{item_id}/decision")
    def decide(item_id: str, body: ReviewIn, request: Request):
        with edit(api, "review"):
            return checked(
                lambda: ops().decide(
                    item_id,
                    body.decision,
                    candidate=body.candidate,
                    note=body.note,
                    actor=api.decided_by(request),
                    edits=body.edits,
                )
            )

    @router.delete("/changes/{item_id}/decision")
    def undo(item_id: str):
        with edit(api, "review"):
            checked(lambda: ops().undo(item_id))
        return {"removed": item_id}

    def connection_fingerprint(item):
        return digest({k: v for k, v in item.items() if k not in ("decision", "hold")})

    @router.get("/connections")
    def connections(
        page: int = Query(1, ge=1), status: Literal["pending", "held", "decided", "all"] = "pending"
    ):
        ops()
        inbox = api.inbox()
        listing = inbox.listing(category=None, status="all", page=1, size=max(len(inbox.items), 1))
        with api.store._conn() as conn:
            holds = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ops_connection_holds")}
        items = [item for item in listing["items"] if item.get("category") != "stale_sources"]
        for item in items:
            hold = holds.get(item["id"])
            item["hold"] = (
                hold
                if hold
                and not item.get("decision")
                and hold["fingerprint"] == connection_fingerprint(item)
                else None
            )
        pending = [i for i in items if not i.get("decision") and not i["hold"]]
        held = [i for i in items if i["hold"]]
        decided = [i for i in items if i.get("decision")]
        chosen = {"pending": pending, "held": held, "decided": decided, "all": items}[status]
        return {
            **listing,
            "status": status,
            "page": page,
            "size": 20,
            "total": len(chosen),
            "pages": max(1, (len(chosen) + 19) // 20),
            "counts": {**listing["counts"], "total": len(pending) + len(held), "held": len(held)},
            "items": chosen[(page - 1) * 20 : page * 20],
        }

    @router.post("/connections/{item_id:path}/hold")
    def hold_connection(item_id: str, body: dict, request: Request):
        note = body.get("note", "")
        if set(body) - {"note"} or not isinstance(note, str) or len(note) > 2000:
            raise ApiError(400, "invalid_request", "invalid hold reason")
        with edit(api, "review"):
            ops()
            item = api.inbox().items.get(item_id)
            if not item:
                raise ApiError(404, "unknown_item", "review item no longer exists")
            if item.get("decision"):
                raise ApiError(409, "already_decided", "undo the earlier decision first")
            with api.store._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ops_connection_holds VALUES(?,?,?,?,?)",
                    (
                        item_id,
                        connection_fingerprint(item),
                        note.strip(),
                        api.decided_by(request),
                        now_iso(),
                    ),
                )
        return {"held": item_id}

    @router.delete("/connections/{item_id:path}/hold")
    def undo_hold(item_id: str):
        with edit(api, "review"):
            ops()
            with api.store._conn() as conn:
                conn.execute("DELETE FROM ops_connection_holds WHERE id=?", (item_id,))
        return {"removed": item_id}

    @router.post("/validate")
    def validate(body: CandidateIn):
        from autoontology.engine.operations import validate

        with edit(api, "validation"):
            return checked(lambda: validate(api.store, api.settings, revision=body.revision))

    @router.get("/publications")
    def publications():
        from autoontology.store.publications import Publications

        return checked(lambda: {"items": Publications(api.store).listing()})

    @router.get("/publications/{revision}")
    def publication(revision: int, verify: bool = False):
        from autoontology.store.publications import Publications

        def result():
            receipts = Publications(api.store)
            receipt = receipts.get(revision)
            if receipt is None:
                if not api.store.get_snapshot(revision):
                    raise ApiError(404, "revision_missing", "unknown revision")
                from autoontology.store.operations import visible_revision

                if not visible_revision(api.store, revision):
                    raise ApiError(409, "not_published", "this candidate has not been published")
                return {"revision": revision, "legacy": True, "manifest": None}
            return {
                **receipt,
                **(
                    {"verification": receipts.verify(revision, api.settings.exports_dir)}
                    if verify
                    else {}
                ),
            }

        return checked(result)

    @router.post("/publish", status_code=202)
    def publish(body: PublishIn):
        try:
            return {
                "run_id": api.runs.start(
                    "publish",
                    WORKSPACE,
                    lambda fn, store: fn(
                        store, api.settings, revision=body.revision, token=body.token
                    ),
                )
            }
        except RunStartError as exc:
            raise ApiError(exc.status, "publish_failed", str(exc)) from exc

    @router.post("/restores")
    def prepare_restoration(body: RestorePrepareIn):
        from autoontology.engine.restorations import prepare_restore

        return checked(
            lambda: prepare_restore(
                api.store, api.settings, revision=body.revision, reason=body.reason
            )
        )

    @router.get("/restores")
    def restorations():
        from autoontology.store.restorations import Restorations

        return checked(lambda: {"items": Restorations(api.store).listing()})

    @router.get("/restores/{proposal}")
    def restoration(proposal: str):
        from autoontology.store.restorations import Restorations

        return checked(lambda: Restorations(api.store).get(proposal))

    @router.post("/restores/{proposal}/execute", status_code=202)
    def execute_restoration(proposal: str, body: RestoreExecuteIn):
        from autoontology.engine.restorations import run_restore
        from autoontology.store.restorations import Restorations

        packet = checked(lambda: Restorations(api.store).get(proposal))
        if packet["status"] == "committed":
            return checked(
                lambda: vars(
                    run_restore(
                        api.store, api.settings, proposal=proposal, confirmation=body.confirmation
                    )
                )
            )
        # Reject wrong acknowledgements before starting a worker, too.
        if body.confirmation != packet["confirmation"]:
            raise ApiError(
                409,
                "restore_confirmation_required",
                "confirmation must match the exact restoration packet",
            )
        try:
            return {
                "run_id": api.runs.start(
                    "restore",
                    WORKSPACE,
                    lambda fn, store: fn(
                        store, api.settings, proposal=proposal, confirmation=body.confirmation
                    ),
                )
            }
        except RunStartError as exc:
            raise ApiError(exc.status, exc.code or "restore_failed", str(exc)) from exc

    @router.post("/feedback", status_code=201)
    def feedback(body: FeedbackIn, request: Request):
        with edit(api, "feedback"):
            return ops().add_feedback(
                body.question.strip(), body.answer, body.note.strip(), actor=api.decided_by(request)
            )

    @router.post("/feedback/{item_id}/resolve")
    def resolve(item_id: int):
        with edit(api, "feedback"):
            ops().resolve_feedback(item_id, False)
        return {"resolved": item_id}

    @router.post("/checks", status_code=201)
    def check(body: CheckIn):
        with edit(api, "checks"):
            return checked(lambda: ops().add_check(body.question.strip(), body.expected.strip()))

    @router.delete("/checks/{item_id}")
    def delete_check(item_id: int):
        with edit(api, "checks"):
            with api.store._conn() as conn:
                conn.execute("DELETE FROM ops_checks WHERE id=?", (item_id,))
        return {"removed": item_id}

    from autoontology.store.restorations import Restorations

    Restorations(api.store).reap()
    app.include_router(router)
