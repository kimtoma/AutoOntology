/* Pure helpers of the Playground page (unit-tested in src/test/playground.test.tsx). */

export const ELEMENT_LABEL: Record<string, string> = {
  term: "Term", concept: "Concept", rule: "Rule", process: "Process", individual: "Individual",
  relation: "Relation", evidence: "Evidence",
}

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
  if (typeof v === "string") {
    try { v = JSON.parse(v) } catch { return null }
  }
  if (!Array.isArray(v) || !v.length) return Array.isArray(v) ? { columns, rows: [] } : null
  if (Array.isArray(v[0])) {
    const width = Math.max(...v.map((r) => (r as unknown[]).length))
    const cols = columns.length === width ? columns : Array.from({ length: width }, (_, i) => columns[i] ?? `col${i + 1}`)
    return { columns: cols, rows: v as unknown[][] }
  }
  if (typeof v[0] === "object" && v[0] !== null) {
    const cols = columns.length ? columns : Object.keys(v[0] as object)
    return { columns: cols, rows: v.map((r) => cols.map((c) => (r as Record<string, unknown>)[c])) }
  }
  return { columns: columns.length ? columns.slice(0, 1) : ["value"], rows: v.map((x) => [x]) }
}
