/* Helpers of the objects and logic pages: value display, a small YAML writer for specs,
   action parameter forms and idempotency keys. */

import { fmtNumber, t } from "@/i18n/core"
import type { ActionParam, LogicKind, ObjectType } from "@/lib/api"

/** A value of an object, a computed value or an effect, in plain words ("—" for nothing). */
export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—"
  if (typeof v === "boolean") return v ? t("logic.value.true") : t("logic.value.false")
  if (typeof v === "number") return fmtNumber(v)
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

/** A new idempotency key: one per opened action dialog, so a double click submits once. */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === "function") return `ui-${c.randomUUID()}`
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/* --- YAML ---------------------------------------------------------------------------------- */

const RESERVED = /^(true|false|yes|no|on|off|null|~|y|n)$/i
const NUMBERISH = /^[-+]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?$|^0x[0-9a-f]+$|^[-+]?\.(inf|nan)$/i

function scalar(s: string): string {
  if (s === "") return "''"
  const plain = !RESERVED.test(s) && !NUMBERISH.test(s) && !/^[\s\-?:,[\]{}#&*!|>'"%@`]/.test(s)
    && !/[\s]$/.test(s) && !/: |\s#|:$/.test(s) && !/[\n\t]/.test(s)
  if (plain) return s
  return `'${s.replace(/'/g, "''")}'`
}

/** The spec's own keys first (kind, name, label, ...), the rest in their order. */
const FIRST_KEYS = ["kind", "name", "label", "description", "object_type", "returns", "expr", "params"]

function orderedKeys(obj: Record<string, unknown>): string[] {
  const keys = Object.keys(obj)
  return [...FIRST_KEYS.filter((k) => keys.includes(k)), ...keys.filter((k) => !FIRST_KEYS.includes(k))]
}

function block(s: string, indent: string): string {
  const body = s.replace(/\n$/, "")
  return `${s.endsWith("\n") ? "|" : "|-"}\n${body.split("\n").map((l) => (l ? indent + l : "")).join("\n")}`
}

function emit(v: unknown, indent: string): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "boolean" || typeof v === "number") return String(v)
  if (typeof v === "string") {
    return v.includes("\n") && !/^\s/.test(v) && !/[ \t]\n/.test(v) ? block(v, indent + "  ") : scalar(v)
  }
  if (Array.isArray(v)) {
    if (!v.length) return "[]"
    return "\n" + v.map((item) => {
      if (item && typeof item === "object" && !Array.isArray(item) && Object.keys(item).length) {
        const inner = emitMap(item as Record<string, unknown>, indent + "  ")
        return `${indent}- ${inner.slice(indent.length + 2)}`
      }
      return `${indent}- ${emit(item, indent + "  ").replace(/^\n/, "\n")}`
    }).join("\n")
  }
  const obj = v as Record<string, unknown>
  if (!Object.keys(obj).length) return "{}"
  return "\n" + emitMap(obj, indent)
}

function emitMap(obj: Record<string, unknown>, indent: string): string {
  return orderedKeys(obj).map((k) => {
    const value = emit(obj[k], indent + "  ")
    const sep = value.startsWith("\n") ? "" : " "
    return `${indent}${scalar(k)}:${sep}${value}`
  }).join("\n")
}

/** A spec (JSON values) as YAML text the service reads back to the same value. */
export function toYaml(spec: Record<string, unknown>): string {
  return emitMap(spec, "") + "\n"
}

/** A starting spec for a new definition of *kind*. */
export function templateSpec(kind: LogicKind, objectType = ""): string {
  const type = objectType || "…"
  if (kind === "function") {
    return `kind: function\nname: new_function\nlabel: ''\nobject_type: ${scalar(type)}\nreturns: number\nexpr: '1'\n`
  }
  if (kind === "action") {
    return [
      "kind: action", "name: new_action", "label: ''", `object_type: ${scalar(type)}`,
      "params: []", "preconditions: []", "effects:", "  - set: note", "    value: '\"\"'", "    operational: true",
      "permission:", "  min_role: editor", "approval:", "  role: none", "",
    ].join("\n")
  }
  return [
    "kind: workflow", "name: new_workflow", "label: ''", "trigger:", "  manual: true", "for_each:",
    `  object_type: ${scalar(type)}`, "  where: 'true'", "steps: []", "",
  ].join("\n")
}

/* --- action parameters ------------------------------------------------------------------------ */

/** The form value of each parameter (its default, or empty). Form values are strings,
    booleans for `bool` parameters. */
export type ParamValues = Record<string, string | boolean>

export function initialParams(params: ActionParam[]): ParamValues {
  const out: ParamValues = {}
  for (const p of params) {
    if (p.type === "bool") out[p.name] = p.default === true
    else out[p.name] = p.default === undefined || p.default === null ? "" : String(p.default)
  }
  return out
}

/** The request parameters of *values*: numbers for numeric fields, empty optional fields left out. */
export function paramPayload(params: ActionParam[], values: ParamValues): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of params) {
    const v = values[p.name]
    if (p.type === "bool") {
      out[p.name] = v === true
      continue
    }
    const text = typeof v === "string" ? v.trim() : ""
    if (!text) continue
    if (p.type === "number") {
      const n = Number(text)
      out[p.name] = Number.isFinite(n) ? n : text
    } else out[p.name] = text
  }
  return out
}

/** The required parameters still empty. */
export function missingParams(params: ActionParam[], values: ParamValues): string[] {
  return params.filter((p) => p.required && p.type !== "bool" && !String(values[p.name] ?? "").trim())
    .map((p) => p.label || p.name)
}

/* --- object types ------------------------------------------------------------------------------- */

/** The object type a spec names (by label or class name). */
export function typeOf(types: ObjectType[] | undefined, name: string): ObjectType | undefined {
  return (types ?? []).find((ot) => ot.name === name || ot.label === name || ot.id === name)
}

/** Active types first, then proposed, broken and retired; by label within each. */
export function sortTypes(types: ObjectType[]): ObjectType[] {
  const rank: Record<string, number> = { active: 0, proposed: 1, broken: 2, retired: 3 }
  return [...types].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.label.localeCompare(b.label))
}

/** A path of the object detail page. */
export const objectPath = (type: string, key: string) => `/objects/${encodeURIComponent(type)}/${encodeURIComponent(key)}`
