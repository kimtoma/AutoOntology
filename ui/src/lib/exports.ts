/* Pure helpers of the Exports page: validation.json summaries and the file tree. */

import type { ExportDir } from "@/lib/api"

export interface Validation {
  ok: boolean
  errors: number
  warnings: number
  /** one line per checked part: "ontology: ok", "mapping: 2 errors", ... */
  parts: string[]
}

/** `ossie/validation.json`: semantic_model / ontology / mapping are "ok", "skipped: …" or a
    list of errors; roundtrip is "ok", "skipped: …" or "error: …" (export/ossie.py). */
export function ossieValidation(record: unknown): Validation {
  const r = (record && typeof record === "object" ? record : {}) as Record<string, unknown>
  let errors = 0
  const parts: string[] = []
  for (const key of ["semantic_model", "ontology", "mapping", "roundtrip"]) {
    const v = r[key]
    if (Array.isArray(v)) {
      errors += v.length
      parts.push(`${key}: ${v.length} error${v.length === 1 ? "" : "s"}`)
    } else if (typeof v === "string") {
      if (v.startsWith("error")) errors += 1
      parts.push(`${key}: ${v}`)
    } else {
      errors += 1
      parts.push(`${key}: missing`)
    }
  }
  const warnings = Array.isArray(r.warnings) ? r.warnings.length : 0
  return { ok: errors === 0, errors, warnings, parts }
}

/** `okf/validation.json`: {okf_version, ok, files, errors[], warnings[]} (export/okf.py). */
export function okfValidation(record: unknown): Validation {
  const r = (record && typeof record === "object" ? record : {}) as Record<string, unknown>
  const errors = Array.isArray(r.errors) ? r.errors.length : 0
  const warnings = Array.isArray(r.warnings) ? r.warnings.length : 0
  return {
    ok: r.ok === true && errors === 0,
    errors,
    warnings,
    parts: [`OKF ${String(r.okf_version ?? "?")}`, `${Number(r.files ?? 0)} files`],
  }
}

/** Extensions shown inline (text); everything else is download-only. */
const PREVIEW = /\.(ya?ml|md|ttl|jsonld|json|graphml)$/i
export const canPreview = (name: string) => PREVIEW.test(name)

export interface FileGroup { name: string; files: string[] }

/** The files of a sub-folder grouped by their next segment: `okf` gives `(root)`, `concepts`,
    `documents`, `tables`. Paths stay relative to the sub-folder. */
export function subGroups(dir: ExportDir): FileGroup[] {
  const groups = new Map<string, string[]>()
  for (const f of dir.files) {
    const at = f.indexOf("/")
    const key = at < 0 ? "" : f.slice(0, at)
    groups.set(key, [...(groups.get(key) ?? []), f])
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)))
    .map(([name, files]) => ({ name, files: files.sort((x, y) => x.localeCompare(y)) }))
}

/** Pretty-prints JSON previews; other text is returned as is. */
export function previewText(name: string, text: string, max = 200_000): string {
  let out = text
  if (/\.(json|jsonld)$/i.test(name)) {
    try { out = JSON.stringify(JSON.parse(text), null, 2) } catch { /* keep the raw text */ }
  }
  return out.length > max ? `${out.slice(0, max)}\n… (first ${max.toLocaleString("en-US")} characters shown)` : out
}
