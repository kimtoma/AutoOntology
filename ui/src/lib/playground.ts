/* Pure helpers of the Playground page (unit-tested in src/test/playground.test.tsx). */

import { labelTable } from "@/i18n/core"

export const ELEMENT_LABEL: Record<string, string> = labelTable({
  term: "playground.el.term", concept: "playground.el.concept", rule: "playground.el.rule",
  process: "playground.el.process", individual: "playground.el.individual",
  relation: "playground.el.relation", evidence: "playground.el.evidence",
})

export function cell(v: unknown): string {
  if (v === null || v === undefined) return ""
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

const FORMULA_START = /^[=+\-@\t\r]/
const PLAIN_NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/

/** A cell spreadsheet software would run as a formula (=, +, -, @, tab, CR first) gets a
    leading `'`, so ontology labels / SQL values cannot inject formulas; numbers (and
    strings that are plain numbers, e.g. "-12.5") stay as they are. */
export function neutralizeFormula(v: unknown): string {
  const s = cell(v)
  if (typeof v === "number" || typeof v === "bigint") return s
  return FORMULA_START.test(s) && !PLAIN_NUMBER.test(s) ? `'${s}` : s
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  const esc = (s: string) => (/[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s)
  return [columns, ...rows].map((r) => r.map((v) => esc(neutralizeFormula(v))).join(",")).join("\r\n")
}

const isRecord = (x: unknown): x is Record<string, unknown> =>
  typeof x === "object" && x !== null && !Array.isArray(x)

function parseJson(s: string): { ok: true; value: unknown } | { ok: false } {
  try { return { ok: true, value: JSON.parse(s) } } catch { return { ok: false } }
}

/** Upstream's executed-SQL envelope unwrapped: `sql_execution` stores `QueryResponse.result`,
    a list of JSON-records strings (`['[{"n":40}]']`), and the whole value may itself arrive as
    a JSON string. A list whose every item is a string holding a JSON list is concatenated;
    anything else is returned as is. */
function unwrapResult(value: unknown): { ok: true; value: unknown } | { ok: false } {
  let v = value
  if (typeof v === "string") {
    const p = parseJson(v)
    if (!p.ok) return p
    v = p.value
  }
  if (Array.isArray(v) && v.length && v.every((x) => typeof x === "string")) {
    const parts = (v as string[]).map(parseJson)
    if (parts.every((p) => p.ok && Array.isArray(p.value))) {
      return { ok: true, value: parts.flatMap((p) => (p as { value: unknown[] }).value) }
    }
  }
  return { ok: true, value: v }
}

/** The executed result as columns/rows, whatever shape upstream returned. Records (objects)
    take their headers from their own keys (first-seen order across rows); *columns* names
    positional (array) rows only when it has exactly their width, else `col1..n`. Upstream's
    `sql_columns` are semantic-layer column ids, so the SQL tab does not pass them. The CSV
    download is written from the same columns/rows. */
export function tabulate(value: unknown, columns: string[] = []): { columns: string[]; rows: unknown[][] } | null {
  const u = unwrapResult(value)
  if (!u.ok) return null
  const v = u.value
  if (!Array.isArray(v)) return null
  if (!v.length) return { columns, rows: [] }
  if (v.every(Array.isArray)) {
    const width = Math.max(...v.map((r) => (r as unknown[]).length))
    const cols = columns.length === width ? columns : Array.from({ length: width }, (_, i) => `col${i + 1}`)
    return { columns: cols, rows: v as unknown[][] }
  }
  if (v.every(isRecord)) {
    const cols: string[] = []
    const seen = new Set<string>()
    for (const r of v) for (const k of Object.keys(r)) if (!seen.has(k)) { seen.add(k); cols.push(k) }
    return { columns: cols, rows: v.map((r) => cols.map((c) => r[c])) }
  }
  return { columns: columns.length === 1 ? columns : ["value"], rows: v.map((x) => [x]) }
}

/** Engine 1.3 SQL pipeline nodes (text_to_sql/node_labels.py) in the UI language; an unknown
    node keeps upstream's English `label`. */
export const SQL_NODE_LABEL: Record<string, string> = labelTable({
  react_sql_agent: "playground.sql.node.react_sql_agent",
  format_and_respond: "playground.sql.node.format_and_respond",
  unconstructable_sql_response: "playground.sql.node.unconstructable_sql_response",
})

export type SqlAnswerKind = "sql" | "information" | "unconstructable"

/** The server's `answer_kind` when present; otherwise inferred the same way (an answer the
    `unconstructable_sql_response` node gave, one with SQL, or one without: rules/definitions). */
export function sqlAnswerKind(kind: unknown, sqlCode: unknown, nodes: string[]): SqlAnswerKind {
  if (kind === "sql" || kind === "information" || kind === "unconstructable") return kind
  if (nodes.includes("unconstructable_sql_response")) return "unconstructable"
  return typeof sqlCode === "string" && sqlCode.trim() ? "sql" : "information"
}
