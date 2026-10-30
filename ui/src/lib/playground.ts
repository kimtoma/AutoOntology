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

/** The executed result as columns/rows, whatever shape upstream returned. */
export function tabulate(value: unknown, columns: string[] = []): { columns: string[]; rows: unknown[][] } | null {
  let v = value
  // Upstream wraps JSON records in list[str], sometimes under a query UUID.
  for (let depth = 0; depth < 4; depth++) {
    if (typeof v === "string") {
      try { v = JSON.parse(v) } catch { return null }
    } else if (Array.isArray(v) && v.length === 1) {
      const item = v[0]
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const keys = Object.keys(item)
        if (keys.length !== 1 || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(keys[0])) break
        v = item[keys[0]]
      } else if (typeof item === "string") {
        let records: unknown
        try { records = JSON.parse(item) } catch { break }
        if (!Array.isArray(records) || !records.every((r) => r !== null && typeof r === "object" && !Array.isArray(r))) break
        v = records
      } else break
    } else break
  }
  if (!Array.isArray(v) || !v.length) return Array.isArray(v) ? { columns, rows: [] } : null
  if (Array.isArray(v[0])) {
    const width = Math.max(...v.map((r) => (r as unknown[]).length))
    const cols = columns.length === width ? columns : Array.from({ length: width }, (_, i) => columns[i] ?? `col${i + 1}`)
    return { columns: cols, rows: v as unknown[][] }
  }
  if (typeof v[0] === "object" && v[0] !== null) {
    const first = v[0] as Record<string, unknown>
    const cols = columns.length && columns.every((c) => Object.hasOwn(first, c)) ? columns : Object.keys(first)
    return { columns: cols, rows: v.map((r) => cols.map((c) => (r as Record<string, unknown>)[c])) }
  }
  return { columns: columns.length ? columns.slice(0, 1) : ["value"], rows: v.map((x) => [x]) }
}
