import { useQuery } from "@tanstack/react-query"
import { ChevronRight, Download, Eye, FolderOpen, Hammer, Loader2 } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { api, errorMessage, type ExportDir, type ExportEntry } from "@/lib/api"
import {
  canPreview, okfValidation, ossieValidation, previewText, subGroups, type Validation,
} from "@/lib/exports"
import { fmtBytes, fmtTime } from "@/lib/labels"

const DIR_INFO: Record<string, string> = {
  ossie: "Apache Ossie bundle: semantic model, ontology and mapping (spec 0.2.0.dev0)",
  okf: "Open Knowledge Format v0.2: one Markdown file per concept, table and document",
  upstream: "The upstream backend's own per-source model and Ossie zip",
}

/** export folders shown at first; "Show more" adds this many at a time */
const PAGE = 20

type Preview = { dir: string; file: string; text: string }
type Open = (dir: string, file: string) => void

function FileRow({ entry, path, label, bytes, onPreview, loading }: {
  entry: ExportEntry; path: string; label: string; bytes?: number; onPreview: Open; loading: string | null
}) {
  const key = `${entry.dir}/${path}`
  return (
    <li className="flex min-w-0 items-center gap-1" data-file={path}>
      <a href={api.exportFileUrl(entry.dir, path)} download
         className="inline-flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs outline-none
                    hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
         title={`Download ${path}`}>
        <Download className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate font-mono">{label}</span>
        {bytes !== undefined && <span className="shrink-0 text-muted-foreground">{fmtBytes(bytes)}</span>}
      </a>
      {canPreview(path) && (
        <Button variant="ghost" size="icon" className="size-6 shrink-0" aria-label={`Preview ${path}`}
                data-preview={path} disabled={!!loading} onClick={() => onPreview(entry.dir, path)}>
          {loading === key ? <Loader2 className="animate-spin" aria-hidden /> : <Eye aria-hidden />}
        </Button>
      )}
    </li>
  )
}

function ValidationBadge({ name, v }: { name: string; v: Validation | null | undefined }) {
  if (v === undefined) return <Badge variant="outline" className="font-normal text-muted-foreground">{name} …</Badge>
  if (v === null) return <Badge variant="outline" className="font-normal text-muted-foreground" data-validation={name.toLowerCase()} data-ok="unknown">{name}: no validation.json</Badge>
  return (
    <span title={v.parts.join("\n")} data-validation={name.toLowerCase()} data-ok={String(v.ok)}>
      <StatusChip status={v.ok ? "succeeded" : "failed"}
                  label={v.ok ? `${name} ok${v.warnings ? ` · ${v.warnings} warning${v.warnings === 1 ? "" : "s"}` : ""}`
                    : `${name} · ${v.errors} error${v.errors === 1 ? "" : "s"}`} />
    </span>
  )
}

/** Reads `<dir>/<sub>/validation.json` when the folder has one. */
function useValidation(entry: ExportEntry, sub: "ossie" | "okf") {
  const has = entry.files.some((f) => f.name === `${sub}/validation.json`)
  const q = useQuery({
    queryKey: ["validation", entry.dir, entry.created_at, sub],
    queryFn: async () => {
      const text = await api.exportText(entry.dir, `${sub}/validation.json`)
      const record = JSON.parse(text) as unknown
      return sub === "ossie" ? ossieValidation(record) : okfValidation(record)
    },
    enabled: has,
    staleTime: Infinity,
  })
  if (!has) return null
  return q.data ?? (q.error ? { ok: false, errors: 1, warnings: 0, parts: [errorMessage(q.error)] } : undefined)
}

function DirGroup({ entry, dir, sizes, onPreview, loading, validation }: {
  entry: ExportEntry
  dir: ExportDir
  sizes: Map<string, number>
  onPreview: Open
  loading: string | null
  validation?: Validation | null
}) {
  const groups = subGroups(dir)
  return (
    <details className="group rounded-lg border" data-dir={dir.name}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-sm
                          outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden />
        <span className="font-mono font-medium">{dir.name}/</span>
        <span className="text-xs text-muted-foreground">{dir.files.length} files · {fmtBytes(dir.bytes)}</span>
        {validation !== undefined && <ValidationBadge name={dir.name === "okf" ? "OKF" : "Ossie"} v={validation} />}
        <span className="basis-full pl-6 text-xs text-muted-foreground">{DIR_INFO[dir.name] ?? ""}</span>
      </summary>
      <div className="grid gap-3 border-t px-3 py-2">
        {groups.map((g) => (
          <div key={g.name || "root"} className="grid gap-1">
            {g.name && <p className="text-xs font-medium text-muted-foreground">{g.name}/ <span className="font-normal">({g.files.length})</span></p>}
            <ul className="grid gap-0.5 sm:grid-cols-2">
              {g.files.map((f) => (
                <FileRow key={f} entry={entry} path={`${dir.name}/${f}`} label={g.name ? f.slice(g.name.length + 1) : f}
                         bytes={sizes.get(`${dir.name}/${f}`)} onPreview={onPreview} loading={loading} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  )
}

function ExportCard({ entry, latest, onPreview, loading }: {
  entry: ExportEntry; latest: boolean; onPreview: Open; loading: string | null
}) {
  const sizes = new Map(entry.files.map((f) => [f.name, f.bytes]))
  const top = entry.files.filter((f) => !f.name.includes("/"))
  const total = entry.files.reduce((n, f) => n + f.bytes, 0)
  const ossie = useValidation(entry, "ossie")
  const okf = useValidation(entry, "okf")
  const rank = (name: string) => { const i = ["ossie", "okf", "upstream"].indexOf(name); return i < 0 ? 99 : i }
  const dirs = [...entry.dirs].sort((x, y) => rank(x.name) - rank(y.name))
  return (
    <Card className="gap-3 py-4" data-export={entry.dir}>
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <FolderOpen className="size-4" aria-hidden />
          <span className="font-mono font-semibold">exports/{entry.dir}/</span>
          <Badge variant="secondary" className="font-normal">Revision {entry.revision}</Badge>
          {latest && <Badge variant="outline" className="font-normal">latest</Badge>}
          <span className="text-xs font-normal text-muted-foreground">{fmtTime(entry.created_at)} · {entry.files.length} files · {fmtBytes(total)}</span>
        </CardTitle>
        <div className="flex flex-wrap gap-1.5" id={latest ? "export-validation" : undefined}>
          {entry.dirs.some((d) => d.name === "ossie") && <ValidationBadge name="Ossie" v={ossie} />}
          {entry.dirs.some((d) => d.name === "okf") && <ValidationBadge name="OKF" v={okf} />}
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 px-4">
        <ul className="grid gap-0.5 sm:grid-cols-2" aria-label="Files">
          {top.map((f) => (
            <FileRow key={f.name} entry={entry} path={f.name} label={f.name} bytes={f.bytes}
                     onPreview={onPreview} loading={loading} />
          ))}
        </ul>
        {dirs.map((d) => (
          <DirGroup key={d.name} entry={entry} dir={d} sizes={sizes} onPreview={onPreview} loading={loading}
                    validation={d.name === "ossie" ? ossie : d.name === "okf" ? okf : undefined} />
        ))}
      </CardContent>
    </Card>
  )
}

export default function ExportsPage() {
  const list = useQuery({ queryKey: keys.exports, queryFn: api.exports })
  const [preview, setPreview] = useState<Preview | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [shown, setShown] = useState(PAGE)

  const open: Open = async (dir, file) => {
    setLoading(`${dir}/${file}`)
    try {
      const text = await api.exportText(dir, file)
      setPreview({ dir, file, text: previewText(file, text) })
    } catch (err) {
      toast.error(`Could not load ${file}`, { description: errorMessage(err) })
    } finally {
      setLoading(null)
    }
  }

  return (
    <>
      <PageHeader title="Exports"
                  description="Every export writes one folder per revision: Turtle, JSON-LD, GraphML, a report, an Apache Ossie bundle and an OKF bundle. Preview text files inline or download them." />
      {list.error ? <ErrorState error={list.error} onRetry={() => list.refetch()} />
        : list.isPending ? <div className="grid gap-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
        : !list.data.length ? (
          <EmptyState icon={FolderOpen} title="No exports yet"
                      hint="Build the ontology, then press Export on the Build page."
                      action={<Button asChild><Link to="/build"><Hammer aria-hidden /> Go to Build</Link></Button>} />
        ) : (
          <div id="exports-list" className="grid gap-4">
            {list.data.slice(0, shown).map((e, i) => (
              <ExportCard key={e.dir} entry={e} latest={i === 0} onPreview={open} loading={loading} />
            ))}
            {list.data.length > shown && (
              <Button variant="outline" className="justify-self-center" id="exports-more"
                      onClick={() => setShown((n) => n + PAGE)}>
                Show more ({list.data.length - shown} older)
              </Button>
            )}
          </div>
        )}
      <Dialog open={!!preview} onOpenChange={(o) => { if (!o) setPreview(null) }}>
        <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle className="break-all">{preview?.file.split("/").pop()}</DialogTitle>
            <DialogDescription className="break-all">exports/{preview?.dir}/{preview?.file}</DialogDescription>
          </DialogHeader>
          <ScrollArea className="min-h-0 flex-1 rounded-md border bg-muted/40">
            <pre id="file-preview" className="p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
              {preview?.text}
            </pre>
          </ScrollArea>
          {preview && (
            <div className="flex justify-end">
              <Button variant="outline" size="sm" asChild>
                <a href={api.exportFileUrl(preview.dir, preview.file)} download><Download aria-hidden /> Download</a>
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
