# SPDX-License-Identifier: MIT
"""Export a project's active logic as an Agent Skill bundle (a zip).

:func:`build_skill` reads the **active** definitions (functions, actions, workflows) and
the active object types of a project and writes:

* ``SKILL.md`` -- YAML front matter (``name``, ``description``: what the skill does and
  when to use it, at most :data:`MAX_DESCRIPTION` characters) and an English body: the
  purpose, the object types (label, table, key, important fields), the functions, the
  actions (parameters, preconditions, approval, agent policy), the workflows, a
  step-by-step procedure for an agent and examples. Korean names and labels stay as
  they are.
* ``mcp.json`` -- the MCP server entry with placeholders only:
  ``{{AUTOONTOLOGY_URL}}/mcp`` and ``Authorization: Bearer <token>``. A real token is
  never written into the bundle.
* ``definitions/{functions,actions,workflows}.yaml`` -- the active specs, in the format
  ``ao logic apply -f`` reads.

The output is deterministic: entries and definitions are sorted, and every zip entry
carries one timestamp taken from the definitions (the latest ``updated_at`` of the active
ones), so exporting unchanged definitions again gives identical bytes.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

import yaml

from autoontology.logic import registry, specs
from autoontology.logic.schema import TypeInfo
from autoontology.logic.specs import ActionSpec, FunctionSpec, StepSpec, WorkflowSpec

#: The front matter's description is at most this long (the Agent Skills limit).
MAX_DESCRIPTION = 1024
#: Columns listed per object type at most.
MAX_FIELDS = 20
URL_PLACEHOLDER = "{{AUTOONTOLOGY_URL}}"
TOKEN_PLACEHOLDER = "<token>"
#: The zip timestamp when there is no active definition (the earliest a zip can hold).
EPOCH = (1980, 1, 1, 0, 0, 0)

_ROLE_WORDS = {"viewer": "a viewer", "reviewer": "a reviewer", "editor": "an editor"}
_ROLE_WORDS["owner"] = "an owner"


@dataclass
class SkillBundle:
    name: str
    files: dict[str, str] = field(default_factory=dict)
    timestamp: tuple[int, int, int, int, int, int] = EPOCH
    counts: dict[str, int] = field(default_factory=dict)

    def zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.files):
                info = zipfile.ZipInfo(f"{self.name}/{path}", date_time=self.timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                info.create_system = 3
                zf.writestr(info, self.files[path].encode("utf-8"))
        return buf.getvalue()


def skill_name(slug: str) -> str:
    """``autoontology-<slug>`` as an Agent Skill name: lower-case letters, digits and
    hyphens, at most 64 characters."""
    clean = re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")
    name = f"autoontology-{clean}" if clean else "autoontology-project"
    return name[:64].rstrip("-")


def _timestamp(values: list[str]) -> tuple[int, int, int, int, int, int]:
    stamps = []
    for v in values:
        try:
            stamps.append(dt.datetime.fromisoformat(v.replace("Z", "+00:00")))
        except (ValueError, AttributeError):
            continue
    if not stamps:
        return EPOCH
    t = max(s.astimezone(dt.UTC) if s.tzinfo else s for s in stamps)
    if t.year < 1980:
        return EPOCH
    return (t.year, t.month, t.day, t.hour, t.minute, t.second - t.second % 2)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _code(text: str | None) -> str:
    one = " ".join(str(text or "").split())
    return f"`{one.replace('`', "'")}`" if one else ""


def _type_ref(ctx: specs.LogicContext, ref: str) -> TypeInfo | None:
    return ctx.schema.resolve(ref)


def _type_label(t: TypeInfo | None, ref: str) -> str:
    if t is None:
        return ref
    return f"{t.label} (`{t.table or t.name}`)"


# --- the sections ---------------------------------------------------------------------


def _types_section(types: list[TypeInfo], ctx: specs.LogicContext) -> list[str]:
    out = ["## Object types", ""]
    if not types:
        return [*out, "No object type is active yet.", ""]
    out += [
        "Objects are rows of source tables. Name a type by its table (stable across "
        "languages), its label or its name (`term-...`).",
        "",
    ]
    for t in types:
        info = ctx.type_of(t.name) or t
        out.append(f"### {t.label}")
        out.append("")
        out.append(f"- Table: `{t.table or '-'}`; name: `{t.name}`; key: `{t.key or '-'}`")
        fields = [f"`{c}` ({kind})" for c, kind in list(info.columns.items())[:MAX_FIELDS]]
        if len(info.columns) > MAX_FIELDS:
            fields.append(f"... {len(info.columns) - MAX_FIELDS} more")
        if fields:
            out.append(f"- Fields: {', '.join(fields)}")
        labels = [f"{label} = `{col}`" for label, col in sorted(info.labels.items())]
        if labels:
            out.append(f"- Field labels: {', '.join(labels[:MAX_FIELDS])}")
        if info.operational:
            ops = ", ".join(f"`{p}`" for p in sorted(info.operational))
            out.append(f"- Operational properties (written by actions): {ops}")
        links = []
        for lk in info.links:
            target = ctx.schema.types.get(lk.target)
            if target is None:
                continue
            arrow = "->" if lk.direction == "out" else "<-"
            links.append(f"{arrow} {target.label} (`{target.table or target.name}`)")
        if links:
            out.append(f"- Links: {', '.join(links)}")
        out.append("")
    return out


def _params_text(params: list[specs.ParamSpec]) -> list[str]:
    out = []
    for p in params:
        bits = [p.type]
        if not p.required:
            bits.append("optional")
        if p.enum:
            bits.append("one of " + ", ".join(json.dumps(v, ensure_ascii=False) for v in p.enum))
        if p.default is not None:
            bits.append(f"default {json.dumps(p.default, ensure_ascii=False)}")
        label = f" -- {p.label}" if p.label and p.label != p.name else ""
        out.append(f"  - `{p.name}` ({'; '.join(bits)}){label}")
    return out


def _functions_section(fns: list[FunctionSpec], ctx: specs.LogicContext) -> list[str]:
    out = ["## Functions", ""]
    if not fns:
        return [*out, "No active function.", ""]
    out += [
        "Read-only computed values and yes/no checks on one object. `get_object` returns "
        "them under `computed`; `call_function` evaluates one.",
        "",
    ]
    for f in fns:
        t = _type_ref(ctx, f.object_type)
        head = f"### `{f.name}`" + (f" -- {f.label}" if f.label and f.label != f.name else "")
        out += [head, ""]
        out.append(f"- On: {_type_label(t, f.object_type)}; returns: {f.returns}")
        if f.description:
            out.append(f"- Meaning: {' '.join(f.description.split())}")
        out.append(f"- Expression: {_code(f.expr)}")
        if f.params:
            out.append("- Parameters:")
            out += _params_text(f.params)
        out.append("")
    return out


def _approval_text(a: ActionSpec) -> str:
    if a.approval.role == "none":
        return "none: it applies at once when the preconditions hold"
    who = _ROLE_WORDS.get(a.approval.role, a.approval.role)
    if a.approval.when:
        return f"needs {who} to approve when {_code(a.approval.when)}"
    return f"always needs {who} to approve"


def _agent_text(a: ActionSpec) -> str:
    if a.agent == "none":
        return "agents may not submit it (tell a person to run it)"
    if a.agent == "propose":
        return (
            "agents may propose it: the run always waits for a person's approval, even "
            "with an execute token"
        )
    return (
        "agents with an execute token may run it; the approval rules above still apply "
        "(a propose token only proposes)"
    )


def _actions_section(acts: list[ActionSpec], ctx: specs.LogicContext) -> list[str]:
    out = ["## Actions", ""]
    if not acts:
        return [*out, "No active action.", ""]
    out += [
        "Changes to one object. They write to AutoOntology's edit layer (the source "
        "tables are never changed) and every applied run can be reverted.",
        "",
    ]
    for a in acts:
        t = _type_ref(ctx, a.object_type)
        head = f"### `{a.name}`" + (f" -- {a.label}" if a.label and a.label != a.name else "")
        out += [head, ""]
        out.append(f"- On: {_type_label(t, a.object_type)}")
        if a.description:
            out.append(f"- What it does: {' '.join(a.description.split())}")
        if a.params:
            out.append("- Parameters:")
            out += _params_text(a.params)
        if a.preconditions:
            out.append("- Preconditions (all must hold, otherwise the run is refused):")
            for pc in a.preconditions:
                msg = f" -- otherwise: {' '.join(pc.message.split())}" if pc.message else ""
                out.append(f"  - {_code(pc.expr)}{msg}")
        if a.effects:
            sets = ", ".join(
                f"`{e.set}` = {_code(e.value)}" + (" (operational)" if e.operational else "")
                for e in a.effects
            )
            out.append(f"- Sets: {sets}")
        if a.notify:
            out.append(f"- Notifies: {', '.join(', '.join(n.to) for n in a.notify)}")
        out.append(f"- Who may run it: {_ROLE_WORDS.get(a.permission.min_role, '?')} or above")
        out.append(f"- Approval: {_approval_text(a)}")
        out.append(f"- Agent policy: {_agent_text(a)}")
        out.append("")
    return out


def _trigger_text(w: WorkflowSpec) -> str:
    if w.trigger.schedule:
        return f"on a schedule: {w.trigger.schedule}"
    if w.trigger.on_action:
        return f"after the action `{w.trigger.on_action}` is applied"
    return "when a person starts it"


def _steps_text(steps: list[StepSpec], depth: int = 0) -> list[str]:
    pad = "  " * (depth + 1)
    out = []
    for s in steps:
        if s.if_ is not None:
            out.append(f"{pad}- if {_code(s.if_)}:")
            out += _steps_text(s.then, depth + 1)
            if s.else_:
                out.append(f"{pad}- otherwise:")
                out += _steps_text(s.else_, depth + 1)
        elif s.action:
            params = ", ".join(f"{k} = {_code(v)}" for k, v in s.params.items())
            out.append(f"{pad}- run the action `{s.action}`" + (f" ({params})" if params else ""))
        elif s.notify is not None:
            out.append(f"{pad}- notify {', '.join(s.notify.to)}")
    return out


def _workflows_section(wfs: list[WorkflowSpec], ctx: specs.LogicContext) -> list[str]:
    out = ["## Workflows", ""]
    if not wfs:
        return [*out, "No active workflow.", ""]
    out += [
        "Workflows run inside AutoOntology (scheduler or a person); an agent does not "
        "start them. A step whose action waits for approval pauses for that object and "
        "resumes once the run is decided.",
        "",
    ]
    for w in wfs:
        t = _type_ref(ctx, w.for_each.object_type)
        head = f"### `{w.name}`" + (f" -- {w.label}" if w.label and w.label != w.name else "")
        out += [head, ""]
        if w.description:
            out.append(f"- What it does: {' '.join(w.description.split())}")
        out.append(f"- Trigger: {_trigger_text(w)}")
        where = f" where {_code(w.for_each.where)}" if w.for_each.where else ""
        out.append(f"- For each {_type_label(t, w.for_each.object_type)}{where}:")
        out += _steps_text(w.steps)
        out.append("")
    return out


PROCEDURE = """\
## How to work with this ontology

Use the MCP server in `mcp.json` (tools below). Every tool but `submit_action` is
read-only.

1. Find the object: `list_object_types`, then `search_objects` (type = the table name,
   e.g. `{table}`; query = key or title text; filters = exact column values).
2. Read it: `get_object` gives its values (with earlier action edits), links and the
   values of its functions under `computed`.
3. Compute what you need: `call_function` (name, key). Do not recompute rules yourself
   when a function exists -- the function is the agreed definition.
4. Before any change: `preview_action` (name, key, params). Check every precondition,
   the effects (old -> new value), whether approval is needed and whether your token may
   submit.
5. Only when the user asked for the change: `submit_action` with the same arguments and
   an `idempotency_key` (retries return the first run).
6. Never assume approval. A run in `pending_approval` waits for a person; check it with
   `get_action_run` later. Report the run id (`act_...`) and its status to the user.
7. If a call fails with `precondition_failed`, explain the failed preconditions' messages
   instead of retrying; `object_busy` may be retried once; `agent_not_allowed` or
   `forbidden` means a person has to act.

Tools: `list_object_types`, `search_objects`, `get_object`, `list_functions`,
`call_function`, `list_actions`, `preview_action`, `submit_action`, `get_action_run`
(also `search_concepts`, `get_concept`, `sparql_query` for the ontology itself). The same
tools are under REST at `{{AUTOONTOLOGY_URL}}/agent/v1/...`.

Your token's scope decides what `submit_action` may do: `read` cannot submit, `propose`
always creates a run that waits for approval, `execute` follows each action's approval
rules. An action's agent policy can only lower that, never raise it.
"""


def _example(acts: list[ActionSpec], fns: list[FunctionSpec], ctx: specs.LogicContext) -> list[str]:
    out = ["## Examples", ""]
    if acts:
        a = acts[0]
        t = _type_ref(ctx, a.object_type)
        table = (t.table or t.name) if t else a.object_type
        params = {
            p.name: (p.default if p.default is not None else (p.enum or [None])[0])
            for p in a.params
            if p.required
        }
        params = {k: v for k, v in params.items() if v is not None}
        args = json.dumps({"name": a.name, "key": "<key>", "params": params}, ensure_ascii=False)
        fn = next((f for f in fns if _type_ref(ctx, f.object_type) is t), None)
        out += [
            f'User: "Record `{a.label or a.name}` on {t.label if t else table} <key>."',
            "",
            f'1. `search_objects` {{"type": "{table}", "query": "<key>"}}',
            f'2. `get_object` {{"type": "{table}", "key": "<key>"}}',
        ]
        step = 3
        if fn is not None:
            out.append(f'{step}. `call_function` {{"name": "{fn.name}", "key": "<key>"}}')
            step += 1
        out += [
            f"{step}. `preview_action` {args} -- show the user the effects and whether "
            "approval is needed",
            f"{step + 1}. `submit_action` (same arguments + `idempotency_key`) -- report the "
            "run id and status (`applied` or `pending_approval`)",
            "",
        ]
    elif fns:
        f = fns[0]
        t = _type_ref(ctx, f.object_type)
        table = (t.table or t.name) if t else f.object_type
        out += [
            f'User: "What is `{f.label or f.name}` of {t.label if t else table} <key>?"',
            "",
            f'1. `call_function` {{"name": "{f.name}", "type": "{table}", "key": "<key>"}}',
            "",
        ]
    else:
        out += ["Define and activate functions or actions first (Logic page).", ""]
    return out


def _description(
    project: str, types: list[TypeInfo], fns: list[Any], acts: list[Any], wfs: list[Any]
) -> str:
    def names(items: list[Any]) -> str:
        return ", ".join((getattr(i, "label", "") or i.name) for i in items)

    parts = [
        f"Work with the business objects and rules of the AutoOntology project "
        f"{project!r} through its MCP server: look up objects"
    ]
    if types:
        parts.append(f" ({names(types)})")
    if fns:
        parts.append(f", compute agreed values and checks ({names(fns)})")
    if acts:
        parts.append(f", and preview or submit actions that may need approval ({names(acts)})")
    parts.append(". ")
    if wfs:
        parts.append(f"Explains the scheduled workflows ({names(wfs)}). ")
    parts.append(
        "Use when a task needs these objects' current values, these business rules, or a "
        "change recorded under them."
    )
    text = "".join(parts)
    if len(text) <= MAX_DESCRIPTION:
        return text
    short = (
        f"Work with the business objects and rules of the AutoOntology project {project!r} "
        f"through its MCP server: {_n(len(types), 'object type')}, "
        f"{_n(len(fns), 'function')}, {_n(len(acts), 'action')}, "
        f"{_n(len(wfs), 'workflow')}. Use when a task needs these objects' "
        "current values, these business rules, or a change recorded under them."
    )
    return short[:MAX_DESCRIPTION]


def _front_matter(name: str, description: str) -> str:
    return (
        "---\n"
        + yaml.safe_dump(
            {"name": name, "description": description},
            allow_unicode=True,
            sort_keys=False,
            width=10_000,
        )
        + "---\n"
    )


def _definitions_yaml(kind: str, defs: list[Any]) -> str:
    lines = [
        f"# Active {kind}s, exported by AutoOntology. Apply with:",
        f"#   ao logic apply -f definitions/{kind}s.yaml",
        "",
    ]
    for d in defs:
        lines.append(f"# {d.name} v{d.version}")
        lines.append(
            yaml.safe_dump([d.spec], allow_unicode=True, sort_keys=False, width=100).rstrip()
        )
        lines.append("")
    return "\n".join(lines)


def build_skill(view: Any) -> SkillBundle:
    """The skill bundle of *view*'s active definitions (module docstring)."""
    ctx = registry.context(view)
    active = sorted(
        (d for d in view.logic_defs().list(None, "active")), key=lambda d: (d.kind, d.name)
    )
    by_kind: dict[str, list[Any]] = {"function": [], "action": [], "workflow": []}
    parsed: dict[str, list[Any]] = {"function": [], "action": [], "workflow": []}
    for d in active:
        try:
            spec = specs.parse_spec(d.spec)
        except specs.SpecError:
            continue
        by_kind[d.kind].append(d)
        parsed[d.kind].append(spec)
    fns: list[FunctionSpec] = parsed["function"]
    acts: list[ActionSpec] = parsed["action"]
    wfs: list[WorkflowSpec] = parsed["workflow"]
    types = sorted(
        (t for t in ctx.schema.types.values() if t.status == "active"),
        key=lambda t: (t.table, t.name),
    )
    name = skill_name(getattr(view, "slug", "") or "")
    project = getattr(view, "name", "") or getattr(view, "slug", "") or "default"
    description = _description(project, types, fns, acts, wfs)
    first = _type_ref(ctx, acts[0].object_type) if acts else None
    first = first or (types[0] if types else None)
    first_table = (first.table or first.name) if first else "invoices"
    body = [
        f"# {project}: business objects and rules",
        "",
        "## Purpose",
        "",
        f"This skill lets an agent use the confirmed business logic of the AutoOntology "
        f"project {project!r}: {_n(len(types), 'object type')}, "
        f"{_n(len(fns), 'function')}, {_n(len(acts), 'action')} and "
        f"{_n(len(wfs), 'workflow')}, all reviewed and activated by people. The "
        "definitions are in `definitions/`; this page explains them.",
        "",
        PROCEDURE.replace("{table}", first_table),
        *_types_section(types, ctx),
        *_functions_section(fns, ctx),
        *_actions_section(acts, ctx),
        *_workflows_section(wfs, ctx),
        *_example(acts, fns, ctx),
    ]
    skill_md = _front_matter(name, description) + "\n" + "\n".join(body).rstrip() + "\n"
    mcp = {
        "mcpServers": {
            name: {
                "type": "http",
                "url": f"{URL_PLACEHOLDER}/mcp",
                "headers": {"Authorization": f"Bearer {TOKEN_PLACEHOLDER}"},
            }
        }
    }
    files = {
        "SKILL.md": skill_md,
        "mcp.json": json.dumps(mcp, indent=2, ensure_ascii=False) + "\n",
    }
    for kind, defs in by_kind.items():
        if defs:
            files[f"definitions/{kind}s.yaml"] = _definitions_yaml(kind, defs)
    return SkillBundle(
        name=name,
        files=files,
        timestamp=_timestamp([d.updated_at for d in active]),
        counts={
            "types": len(types),
            "functions": len(fns),
            "actions": len(acts),
            "workflows": len(wfs),
        },
    )
