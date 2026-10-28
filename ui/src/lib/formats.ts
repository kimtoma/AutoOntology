/* What a source can read, from `/api/status.formats` (autoontology/formats.py::describe):
   the upload `accept` lists, the dialog hints and the per-file preview. Nothing here lists
   extensions itself except the advice for formats the service cannot read. */

import { t } from "@/i18n/core"

export type Engine = "native" | "kordoc" | "kordoc-ocr"
export interface FormatEntry { kind: "structured" | "document"; engine: Engine; note: string }
export interface Formats {
  structured: Record<string, FormatEntry>
  documents: Record<string, FormatEntry>
}
/** The two source families of the add dialog. */
export type Family = "structured" | "documents"

const familyKey = (family: Family) => (family === "structured" ? "structured" : "documents")

/** ".csv" for "Data.CSV"; "" when the name has no extension. */
export function extensionOf(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? name
  const dot = base.lastIndexOf(".")
  return dot > 0 ? base.slice(dot).toLowerCase() : ""
}

/** The `accept` attribute of the file picker for *family*: every extension the service reads. */
export function acceptList(formats: Formats | undefined, family: Family): string {
  return Object.keys(formats?.[familyKey(family)] ?? {}).sort().join(",")
}

/** Extensions per engine, in a stable order, for the dialog hint. */
export function extensionsByEngine(formats: Formats | undefined, family: Family): Record<Engine, string[]> {
  const out: Record<Engine, string[]> = { native: [], kordoc: [], "kordoc-ocr": [] }
  for (const [ext, entry] of Object.entries(formats?.[familyKey(family)] ?? {})) {
    out[entry.engine]?.push(ext)
  }
  for (const list of Object.values(out)) list.sort()
  return out
}

/** ".csv .tsv .xls" -> "CSV, TSV, XLS" */
export function extLabel(exts: string[]): string {
  return exts.map((e) => e.slice(1).toUpperCase()).join(", ")
}

export type FileState = "supported" | "needs_kordoc" | "needs_ocr" | "unsupported"

export interface FileCheck {
  name: string
  size: number | null
  ext: string
  engine: Engine | null
  state: FileState
  /** why it cannot be read now (needs_* and unsupported) */
  reason?: string
  /** what to do instead (unsupported) */
  alternative?: string
}

/** Advice for formats the service cannot read: what to save them as. */
type Advice = [verb: "saveAs" | "exportAs" | "convert", targets: string[]] | "unzip" | "extract" | "message"
const ALTERNATIVES: Record<string, Advice> = {
  ".rtf": ["saveAs", [".docx"]],
  ".odt": ["saveAs", [".docx"]],
  ".pages": ["exportAs", [".docx", ".pdf"]],
  ".ppt": ["saveAs", [".pptx"]],
  ".odp": ["saveAs", [".pptx"]],
  ".key": ["exportAs", [".pptx", ".pdf"]],
  ".xlsb": ["saveAs", [".xlsx"]],
  ".ods": ["saveAs", [".xlsx"]],
  ".numbers": ["exportAs", [".xlsx"]],
  ".gif": ["convert", [".png"]],
  ".bmp": ["convert", [".png"]],
  ".tif": ["convert", [".png", ".pdf"]],
  ".tiff": ["convert", [".png", ".pdf"]],
  ".heic": ["convert", [".jpg"]],
  ".zip": "unzip",
  ".7z": "extract",
  ".tar": "extract",
  ".gz": "extract",
  ".hwt": ["saveAs", [".hwp", ".hwpx"]],
  ".xml": ["convert", [".json", ".csv", ".html"]],
  ".msg": "message",
  ".eml": "message",
}

/** ".json, .csv or .html" (the "or" in the UI language) */
function joinTargets(targets: string[]): string {
  if (targets.length < 2) return targets.join("")
  return `${targets.slice(0, -1).join(", ")} ${t("fmt.or")} ${targets[targets.length - 1]}`
}

function alternativeFor(ext: string): string {
  const a = ALTERNATIVES[ext]
  if (!a) return t("fmt.alt.default")
  if (a === "unzip") return t("fmt.alt.unzip")
  if (a === "extract") return t("fmt.alt.extract")
  if (a === "message") return t("fmt.alt.message")
  const [verb, targets] = a
  return t(`fmt.alt.${verb}`, { target: joinTargets(targets) })
}

export const KORDOC_INSTALL = "uv run autoontology tools install-kordoc"

/** The other family, as named in the advice ("add it as a Documents source"). */
const OTHER_FAMILY: Record<Family, Family> = { structured: "documents", documents: "structured" }

/** Can a *family* source read *name*, given whether kordoc and its OCR models are installed? */
export function classifyFile(
  name: string,
  size: number | null,
  family: Family,
  formats: Formats | undefined,
  engines: { kordoc: boolean; ocr: boolean },
): FileCheck {
  const ext = extensionOf(name)
  const entry = formats?.[familyKey(family)]?.[ext]
  const base = { name, size, ext }
  if (entry) {
    if (entry.engine === "kordoc" && !engines.kordoc) {
      return { ...base, engine: entry.engine, state: "needs_kordoc",
               reason: t("fmt.reason.kordoc", { ext }) }
    }
    if (entry.engine === "kordoc-ocr" && !(engines.kordoc && engines.ocr)) {
      return { ...base, engine: entry.engine, state: "needs_ocr",
               reason: engines.kordoc ? t("fmt.reason.ocrModels") : t("fmt.reason.ocrKordoc") }
    }
    return { ...base, engine: entry.engine, state: "supported" }
  }
  const other = family === "structured" ? "documents" : "structured"
  const elsewhere = formats?.[other]?.[ext]
  if (elsewhere) {
    return { ...base, engine: null, state: "unsupported",
             reason: t(family === "structured" ? "fmt.reason.notTable" : "fmt.reason.notDocument", { ext }),
             alternative: t(elsewhere.engine === "native" ? "fmt.alt.otherFamilyNative" : "fmt.alt.otherFamily",
                            { family: t(`family.${OTHER_FAMILY[family]}`), ext }) }
  }
  return { ...base, engine: null, state: "unsupported",
           reason: ext ? t("fmt.reason.cannotRead", { ext }) : t("fmt.reason.noExt"),
           alternative: alternativeFor(ext) }
}

/** Ignored the way the server ignores them when it scans a folder (hidden files, Office lock files). */
export function isIgnoredName(name: string): boolean {
  const base = name.split(/[\\/]/).pop() ?? name
  return !base || base.startsWith(".") || base.startsWith("~$")
}

export function summarize(checks: FileCheck[]): Record<FileState, number> {
  const out: Record<FileState, number> = { supported: 0, needs_kordoc: 0, needs_ocr: 0, unsupported: 0 }
  for (const c of checks) out[c.state] += 1
  return out
}
