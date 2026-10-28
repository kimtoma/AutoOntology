/* What a source can read, from `/api/status.formats` (autoontology/formats.py::describe):
   the upload `accept` lists, the dialog hints and the per-file preview. Nothing here lists
   extensions itself except the advice for formats the service cannot read. */

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
const ALTERNATIVES: Record<string, string> = {
  ".rtf": "save it as .docx",
  ".odt": "save it as .docx",
  ".pages": "export it as .docx or .pdf",
  ".ppt": "save it as .pptx",
  ".odp": "save it as .pptx",
  ".key": "export it as .pptx or .pdf",
  ".xlsb": "save it as .xlsx",
  ".ods": "save it as .xlsx",
  ".numbers": "export it as .xlsx",
  ".gif": "convert it to .png",
  ".bmp": "convert it to .png",
  ".tif": "convert it to .png or .pdf",
  ".tiff": "convert it to .png or .pdf",
  ".heic": "convert it to .jpg",
  ".zip": "unzip it and add the files",
  ".7z": "extract it and add the files",
  ".tar": "extract it and add the files",
  ".gz": "extract it and add the files",
  ".hwt": "save it as .hwp or .hwpx",
  ".xml": "convert it to .json, .csv or .html",
  ".msg": "save the message as .txt or .pdf",
  ".eml": "save the message as .txt or .pdf",
}

export const KORDOC_INSTALL = "uv run autoontology tools install-kordoc"

const OTHER: Record<Family, string> = { structured: "Documents", documents: "Structured" }

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
               reason: `${ext} is read by kordoc, which is not installed` }
    }
    if (entry.engine === "kordoc-ocr" && !(engines.kordoc && engines.ocr)) {
      return { ...base, engine: entry.engine, state: "needs_ocr",
               reason: engines.kordoc ? "images need the OCR models, which are not downloaded"
                 : "images need kordoc and its OCR models, which are not installed" }
    }
    return { ...base, engine: entry.engine, state: "supported" }
  }
  const other = family === "structured" ? "documents" : "structured"
  const elsewhere = formats?.[other]?.[ext]
  if (elsewhere) {
    return { ...base, engine: null, state: "unsupported",
             reason: `${ext} is not a ${family === "structured" ? "table" : "document"} format here`,
             alternative: `add it as a ${OTHER[family]} source (${ext} is supported there${
               elsewhere.engine === "native" ? " natively" : ""})` }
  }
  return { ...base, engine: null, state: "unsupported",
           reason: ext ? `${ext} files cannot be read` : "the file has no extension",
           alternative: ALTERNATIVES[ext] ?? "save it in one of the formats listed above" }
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
