import { FolderSearch } from "lucide-react"
import { useRef, useState, type DragEvent } from "react"

import { Code } from "@/components/provider-card"
import { StatusChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { KORDOC_INSTALL, summarize, type FileCheck, type FileState } from "@/lib/formats"
import { fmtBytes } from "@/lib/labels"
import { cn } from "@/lib/utils"
import { tn, tx, useT, type MessageKey } from "@/i18n"

const STATE_LABEL: Record<FileState, MessageKey> = {
  supported: "preview.state.supported",
  needs_kordoc: "preview.state.needs_kordoc",
  needs_ocr: "preview.state.needs_ocr",
  unsupported: "preview.state.unsupported",
}
const STATE_TONE: Record<FileState, string> = {
  supported: "succeeded", needs_kordoc: "cancelled", needs_ocr: "cancelled", unsupported: "failed",
}
/** Rows shown before "… N more" (a large folder). */
const MAX_ROWS = 100

/** One row per file: name, size, detected engine and whether it can be read. */
export function FilePreview({ checks }: { checks: FileCheck[] }) {
  const t = useT()
  if (!checks.length) return null
  const counts = summarize(checks)
  const needsKordoc = counts.needs_kordoc > 0
  const needsOcr = counts.needs_ocr > 0
  return (
    <div className="grid min-w-0 gap-2" id="file-preview-table">
      <p className="text-xs text-muted-foreground" data-summary>
        {tn("preview.summary", checks.length, { supported: counts.supported })}
        {counts.needs_kordoc ? t("preview.summary.kordoc", { n: counts.needs_kordoc }) : ""}
        {counts.needs_ocr ? t("preview.summary.ocr", { n: counts.needs_ocr }) : ""}
        {counts.unsupported ? t("preview.summary.unsupported", { n: counts.unsupported }) : ""}
      </p>
      <div className="max-h-64 overflow-y-auto rounded-md border">
        <Table className="text-xs">
          <TableHeader>
            <TableRow>
              <TableHead className="pl-3">{t("preview.col.file")}</TableHead>
              <TableHead className="hidden sm:table-cell">{t("preview.col.size")}</TableHead>
              <TableHead>{t("preview.col.readBy")}</TableHead>
              <TableHead className="pr-3">{t("preview.col.status")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {checks.slice(0, MAX_ROWS).map((c, i) => (
              <TableRow key={`${c.name}-${i}`} data-file={c.name} data-state={c.state} data-engine={c.engine ?? ""}>
                <TableCell className="max-w-40 pl-3 whitespace-normal">
                  <span className="break-all">{c.name}</span>
                  {c.size != null && <span className="block text-muted-foreground sm:hidden">{fmtBytes(c.size)}</span>}
                </TableCell>
                <TableCell className="hidden sm:table-cell">{c.size != null ? fmtBytes(c.size) : "-"}</TableCell>
                <TableCell><code>{c.engine ?? "-"}</code></TableCell>
                <TableCell className="max-w-48 pr-3 whitespace-normal">
                  <StatusChip status={STATE_TONE[c.state]} label={t(STATE_LABEL[c.state])} />
                  {c.reason && <span className="mt-0.5 block text-muted-foreground">{c.reason}</span>}
                  {c.alternative && <span className="block">{t("preview.instead", { alt: c.alternative })}</span>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {checks.length > MAX_ROWS && (
          <p className="px-3 py-2 text-xs text-muted-foreground">{t("preview.more", { n: checks.length - MAX_ROWS })}</p>
        )}
      </div>
      {(needsKordoc || needsOcr) && (
        <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs" role="note" id="preview-install">
          {tx("preview.install.body", {
            what: t(needsKordoc ? "preview.install.kordoc" : "preview.install.ocr"),
            cmd: <Code key="cmd">{KORDOC_INSTALL}</Code>,
          })}
        </p>
      )}
    </div>
  )
}

export interface PickedName { name: string; size: number | null }

/** Reads the names under a dropped folder (DataTransfer entries); *recursive* = subfolders too. */
async function walk(entry: FileSystemEntry, recursive: boolean, depth: number, out: PickedName[], cap: number) {
  if (out.length >= cap) return
  if (entry.isFile) {
    const size = await new Promise<number | null>((resolve) =>
      (entry as FileSystemFileEntry).file((f) => resolve(f.size), () => resolve(null)))
    out.push({ name: depth > 1 ? entry.fullPath.replace(/^\//, "") : entry.name, size })
    return
  }
  if (depth > 0 && !recursive) return
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve) => reader.readEntries(resolve, () => resolve([])))
    if (!batch.length) break
    for (const e of batch) await walk(e, recursive, depth + 1, out, cap)
    if (out.length >= cap) break
  }
}

const CAP = 2000

/** Files of a folder the user drops or picks, to preview before pointing the server at it.
    Nothing is uploaded: only the names and sizes are read. */
export function FolderCheck({ recursive, onNames }: {
  recursive: boolean
  onNames: (names: PickedName[]) => void
}) {
  const t = useT()
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const onDrop = async (ev: DragEvent) => {
    ev.preventDefault()
    setOver(false)
    const out: PickedName[] = []
    for (const item of [...ev.dataTransfer.items]) {
      const entry = item.webkitGetAsEntry?.()
      if (entry) await walk(entry, recursive, 0, out, CAP)
      else {
        const f = item.getAsFile()
        if (f) out.push({ name: f.name, size: f.size })
      }
    }
    onNames(out)
  }
  return (
    <div onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
         onDrop={onDrop} id="folder-check"
         className={cn("flex flex-wrap items-center gap-2 rounded-md border border-dashed p-3 text-xs text-muted-foreground",
                       over && "border-primary bg-accent")}>
      <span className="min-w-0 flex-1">{t("preview.folder.hint")}</span>
      <Button type="button" size="sm" variant="outline" onClick={() => input.current?.click()}>
        <FolderSearch aria-hidden /> {t("preview.folder.button")}
      </Button>
      <input ref={input} type="file" className="hidden" aria-label={t("preview.folder.aria")} multiple
             {...{ webkitdirectory: "" }}
             onChange={(e) => {
               const files = [...(e.target.files ?? [])]
               onNames(files
                 .filter((f) => recursive || (f.webkitRelativePath || f.name).split("/").length <= 2)
                 .slice(0, CAP)
                 .map((f) => ({ name: (f.webkitRelativePath || f.name).split("/").slice(1).join("/") || f.name, size: f.size })))
               e.target.value = ""
             }} />
    </div>
  )
}
