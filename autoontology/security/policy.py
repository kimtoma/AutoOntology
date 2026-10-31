# SPDX-License-Identifier: MIT
"""Explicit capabilities; identity and delegation never come from model output."""

from dataclasses import dataclass, field


class AccessError(ValueError):
    def __init__(
        self,
        message="This operation is outside your current authority.",
        code="forbidden",
        status=403,
        *,
        retry_after=0,
    ):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


@dataclass(frozen=True)
class Capability:
    level: str
    human_only: bool = False


CAPABILITIES = {
    "collection.read": Capability("read"),
    "collection.work": Capability("draft"),
    "collection.configure": Capability("execute", True),
    "ax.read": Capability("read"),
    "ax.record": Capability("execute", True),
    "ax.configure": Capability("execute", True),
    "knowledge.read": Capability("read"),
    "history.read": Capability("read"),
    "originals.read": Capability("read"),
    "exports.read": Capability("read"),
    "query.run": Capability("read"),
    "results.read": Capability("read"),
    "results.delete": Capability("execute", True),
    "candidate.read": Capability("read"),
    "candidate.prepare": Capability("draft"),
    "source.read": Capability("read"),
    "source.edit": Capability("execute", True),
    "review.suggest": Capability("suggest"),
    "review.decide": Capability("execute", True),
    "validation.run": Capability("draft"),
    "checks.edit": Capability("execute", True),
    "knowledge.publish": Capability("execute", True),
    "knowledge.restore": Capability("execute", True),
    "feedback.submit": Capability("suggest"),
    "feedback.manage": Capability("execute", True),
    "runs.read": Capability("read"),
    "runs.cancel": Capability("execute", True),
    "settings.read": Capability("read", True),
    "settings.manage": Capability("execute", True),
    "identity.admin": Capability("execute", True),
    "audit.read": Capability("read", True),
}
VIEWER = frozenset(
    {
        "knowledge.read",
        "history.read",
        "originals.read",
        "exports.read",
        "query.run",
        "results.read",
        "results.delete",
        "source.read",
        "feedback.submit",
    }
)
REVIEWER = VIEWER | {
    "collection.read",
    "ax.read",
    "ax.record",
    "candidate.read",
    "review.suggest",
    "review.decide",
    "validation.run",
    "checks.edit",
    "feedback.manage",
    "runs.read",
}
ROLES = {
    "viewer": VIEWER,
    "editor": VIEWER
    | {
        "collection.read",
        "collection.work",
        "ax.read",
        "ax.record",
        "candidate.read",
        "candidate.prepare",
        "source.edit",
        "review.suggest",
        "runs.read",
        "runs.cancel",
    },
    "reviewer": REVIEWER,
    "publisher": REVIEWER | {"knowledge.publish", "knowledge.restore"},
    "admin": frozenset(CAPABILITIES),
}


@dataclass(frozen=True)
class Actor:
    principal_id: str
    credential_id: str
    kind: str
    name: str
    _proof: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Decision:
    actor: Actor
    action: str
    workspace_id: str
    domains: tuple[str, ...]
    policy_version: int
