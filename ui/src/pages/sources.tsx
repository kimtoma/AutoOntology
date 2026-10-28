import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Database, FileText, Hammer, Loader2, Plus, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { keys, useSources, useStatus } from "@/hooks/queries"
import { api, errorMessage, type Source, type SourceKind } from "@/lib/api"
import { familyOf, FAMILY_LABEL, fmtBytes, fmtTime, KIND_LABEL, RUN_LABEL, sourceState } from "@/lib/labels"
import { cn } from "@/lib/utils"
import { FilePreview, FolderCheck, type PickedName } from "@/components/file-preview"
import { Code } from "@/components/provider-card"
import {
  acceptList, classifyFile, extensionOf, extensionsByEngine, extLabel, isIgnoredName, KORDOC_INSTALL,
  type Family, type Formats,
} from "@/lib/formats"

/** Server default of AUTOONTOLOGY_UPLOAD_MAX_MB when /status has not answered yet. */
const DEFAULT_UPLOAD_MAX_MB = 200

export function KindBadge({ kind }: { kind: string }) {
  const family = familyOf(kind)
  const Icon = family === "documents" ? FileText : Database
  return (
    <Badge variant="outline" data-kind={family}
           className={cn("font-normal", family === "documents"
             ? "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300"
             : "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300")}>
      <Icon aria-hidden /> {FAMILY_LABEL[family]}
    </Badge>
  )
}

function LastRun({ s }: { s: Source }) {
  if (s.active_run) {
    return <StatusChip status="running" label={`${RUN_LABEL[s.active_run.kind] ?? s.active_run.kind} running`} />
  }
  if (!s.last_run) return <span className="text-muted-foreground">-</span>
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span>{RUN_LABEL[s.last_run.kind] ?? s.last_run.kind}</span>
        <StatusChip status={s.last_run.status} />
      </div>
      <span className="text-xs text-muted-foreground">{fmtTime(s.last_run.finished_at)}</span>
    </div>
  )
}

export default function SourcesPage() {
  const { data: sources, isPending, error, refetch } = useSources()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Source | null>(null)

  return (
    <>
      <PageHeader
        title="Sources"
        description={<>Register <b>structured</b> data (tables in files or a database) and
          {" "}<b>documents</b> (policies, manuals, reports, scans). One
          {" "}<Link to="/build" className="underline underline-offset-4">build</Link> turns all of
          them into a single ontology.</>}
        actions={<Button onClick={() => setAdding(true)} id="btn-new-source">
          <Plus aria-hidden /> Add source</Button>}
      />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-48 w-full" />
        : !sources.length ? (
          <EmptyState icon={Database} title="No sources yet"
                      hint="Add a folder of CSV files, a database URL or a folder of documents."
                      action={<Button onClick={() => setAdding(true)}><Plus aria-hidden /> Add source</Button>} />
        ) : (
          <>
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="sources-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">Name</TableHead>
                      <TableHead>Kind</TableHead>
                      <TableHead className="hidden lg:table-cell">Location</TableHead>
                      <TableHead className="hidden md:table-cell">State</TableHead>
                      <TableHead className="hidden sm:table-cell">Last run</TableHead>
                      <TableHead className="w-12 pr-4"><span className="sr-only">Actions</span></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sources.map((s) => (
                      <TableRow key={s.name} data-source={s.name}>
                        <TableCell className="max-w-48 pl-4 whitespace-normal">
                          <div className="font-medium break-all">{s.name}</div>
                          {s.database && <div className="text-xs break-all text-muted-foreground">{s.database}</div>}
                          <div className="text-xs text-muted-foreground md:hidden">{sourceState(s)}</div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col items-start gap-1">
                            <KindBadge kind={s.kind} />
                            <span className="text-xs text-muted-foreground">
                              {KIND_LABEL[s.kind]}{s.uploaded ? " · uploaded" : ""}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="hidden max-w-72 lg:table-cell">
                          <span className="block truncate font-mono text-xs" title={s.locator}>{s.locator}</span>
                        </TableCell>
                        <TableCell className="hidden md:table-cell">{sourceState(s)}</TableCell>
                        <TableCell className="hidden sm:table-cell"><LastRun s={s} /></TableCell>
                        <TableCell className="pr-4 text-right">
                          <Button variant="ghost" size="icon" aria-label={`Delete ${s.name}`}
                                  data-delete={s.name} disabled={!!s.active_run}
                                  onClick={() => setRemoving(s)}>
                            <Trash2 aria-hidden />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
            <p className="mt-4 text-sm text-muted-foreground">
              Next: <Link to="/build" className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4">
                <Hammer className="size-3.5" aria-hidden /> build the ontology</Link> from every source.
            </p>
          </>
        )}
      <NewSourceDialog open={adding} onOpenChange={setAdding} />
      <DeleteSourceDialog key={removing?.name ?? ""} source={removing} onClose={() => setRemoving(null)} />
    </>
  )
}

type Mode = "upload" | "path" | "database"

/** What the add dialog sends: an upload (files/documents) or a locator (path or DB URL). */
function sourceRequest(family: Family, mode: Mode): { kind: SourceKind; upload: boolean } {
  if (family === "documents") return { kind: "documents", upload: mode === "upload" }
  if (mode === "database") return { kind: "database", upload: false }
  return { kind: "files", upload: mode === "upload" }
}

export function NewSourceDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState("")
  const [family, setFamily] = useState<Family>("structured")
  const [mode, setMode] = useState<Mode>("upload")
  const [files, setFiles] = useState<File[]>([])
  const [locator, setLocator] = useState("")
  const [recursive, setRecursive] = useState(false)
  const [error, setError] = useState("")
  const effectiveMode: Mode = family === "documents" && mode === "database" ? "upload" : mode
  const { kind, upload } = sourceRequest(family, effectiveMode)

  const reset = () => {
    setName(""); setFamily("structured"); setMode("upload"); setFiles([]); setLocator("")
    setRecursive(false); setError(""); setChecked([])
  }

  const status = useStatus()
  const limitMb = status.data?.upload_max_mb ?? DEFAULT_UPLOAD_MAX_MB
  const kordoc = status.data?.kordoc_available
  const ocr = status.data?.ocr_ready
  const formats = status.data?.formats
  const [checked, setChecked] = useState<PickedName[]>([])
  const engines = { kordoc: !!kordoc, ocr: !!ocr }
  const fileChecks = files.map((f) => classifyFile(f.name, f.size, family, formats, engines))
  const locatorName = locator.trim()
  const pathChecks = (checked.length ? checked.filter((n) => !isIgnoredName(n.name))
    : effectiveMode === "path" && extensionOf(locatorName) ? [{ name: locatorName.split(/[\\/]/).pop()!, size: null }]
    : []).map((n) => classifyFile(n.name, n.size, family, formats, engines))
  const unsupported = fileChecks.filter((c) => c.state === "unsupported")
  const totalBytes = files.reduce((sum, f) => sum + f.size, 0)
  const tooLarge = totalBytes > limitMb * 1024 * 1024

  const add = useMutation({
    mutationFn: () => {
      if (upload) {
        if (!files.length) throw new Error("Choose at least one file")
        if (unsupported.length) {
          throw new Error(`Remove the unsupported file${unsupported.length === 1 ? "" : "s"} first: ${unsupported.map((c) => c.name).join(", ")}`)
        }
        if (tooLarge) {
          throw new Error(`The files total ${fmtBytes(totalBytes)}, over the ${limitMb} MB limit (AUTOONTOLOGY_UPLOAD_MAX_MB)`)
        }
        return api.addSourceUpload(name.trim(), kind as "files" | "documents", files)
      }
      return api.addSourceLocator({ name: name.trim(), kind, locator: locator.trim(), recursive })
    },
    onSuccess: (s) => {
      toast.success(`Added source “${s.name}”`, { description: "Build the ontology to include it." })
      qc.invalidateQueries({ queryKey: keys.sources })
      onOpenChange(false)
      reset()
    },
    onError: (e) => setError(errorMessage(e)),
  })

  const submit = (ev: FormEvent) => {
    ev.preventDefault()
    setError("")
    add.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="grid min-w-0 gap-4" id="form-source">
          <DialogHeader>
            <DialogTitle>Add source</DialogTitle>
            <DialogDescription>Upload files, point at a folder, or give a database URL.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>Kind</Label>
            <Tabs value={family} onValueChange={(v) => { setFamily(v as Family); setChecked([]) }}>
              <TabsList className="w-full" id="source-kind">
                <TabsTrigger value="structured" data-family="structured"><Database aria-hidden /> Structured</TabsTrigger>
                <TabsTrigger value="documents" data-family="documents"><FileText aria-hidden /> Documents</TabsTrigger>
              </TabsList>
            </Tabs>
            <FormatHint family={family} formats={formats} kordoc={!!kordoc} ocr={!!ocr} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="source-name">Name</Label>
            <Input id="source-name" required value={name} onChange={(e) => setName(e.target.value)}
                   pattern={family === "documents" ? "[a-z0-9][a-z0-9_\\-]*" : "[a-z0-9_]+"} autoComplete="off"
                   placeholder={family === "documents" ? "e.g. hanbit-docs (a-z, 0-9, _ and -)" : "e.g. hanbit (a-z, 0-9 and _)"} />
          </div>
          <div className="grid gap-2">
            <Label>From</Label>
            <Tabs value={effectiveMode} onValueChange={(v) => setMode(v as Mode)}>
              <TabsList className="w-full">
                <TabsTrigger value="upload">Upload</TabsTrigger>
                <TabsTrigger value="path">Folder</TabsTrigger>
                {family === "structured" && <TabsTrigger value="database">DB URL</TabsTrigger>}
              </TabsList>
            </Tabs>
          </div>
          {upload ? (
            <div className="grid gap-2">
              <Label htmlFor="source-files">Files</Label>
              <Input id="source-files" type="file" multiple accept={acceptList(formats, family) || undefined}
                     onChange={(e) => setFiles([...(e.target.files ?? [])])} />
              <p className={tooLarge ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
                 id="upload-limit">
                {files.length > 0 && `${files.length} selected · ${fmtBytes(totalBytes)} total · `}
                up to {limitMb} MB per upload
              </p>
              <FilePreview checks={fileChecks} />
              {unsupported.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-destructive" role="alert" id="unsupported-files">
                  <span className="min-w-0 flex-1">The service refuses an upload with unsupported files.</span>
                  <Button type="button" size="sm" variant="outline"
                          onClick={() => setFiles(files.filter((f) => classifyFile(f.name, f.size, family, formats, engines).state !== "unsupported"))}>
                    Remove {unsupported.length} unsupported
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="source-locator">{effectiveMode === "database" ? "Database URL" : "Folder or file path"}</Label>
                <Input id="source-locator" required value={locator}
                       onChange={(e) => setLocator(e.target.value)} autoComplete="off"
                       className="font-mono text-xs"
                       placeholder={effectiveMode === "database" ? "postgresql://user:pass@host:5432/db"
                         : "/absolute/path/to/folder"} />
                {effectiveMode === "path" && (
                  <p className="text-xs text-muted-foreground">A folder (or one file) on the machine that runs the service.</p>
                )}
              </div>
              {effectiveMode === "path" && (
                <>
                  <div className="flex items-center gap-2">
                    <Switch id="source-recursive" checked={recursive} onCheckedChange={setRecursive} />
                    <Label htmlFor="source-recursive">Include subfolders</Label>
                  </div>
                  <FolderCheck recursive={recursive} onNames={setChecked} />
                  <FilePreview checks={pathChecks} />
                </>
              )}
            </>
          )}
          {error && <p role="alert" className="text-sm break-words text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => { onOpenChange(false); reset() }}>Cancel</Button>
            <Button type="submit" disabled={add.isPending || (upload && (tooLarge || unsupported.length > 0))}>
              {add.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {add.isPending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** What a *family* source reads, from `/api/status.formats`, with the install command for
    the engines that are missing. */
function FormatHint({ family, formats, kordoc, ocr }: {
  family: Family
  formats: Formats | undefined
  kordoc: boolean
  ocr: boolean
}) {
  const by = extensionsByEngine(formats, family)
  if (!formats) return <p className="text-xs text-muted-foreground" id="source-kind-hint">Checking the supported formats…</p>
  if (family === "structured") {
    return (
      <p className="text-xs text-muted-foreground" id="source-kind-hint">
        Tables: {extLabel(by.native)} (one table per file, per sheet or per SQLite table; the first row
        is the header), or a PostgreSQL database URL.
      </p>
    )
  }
  return (
    <div className="grid gap-1 text-xs text-muted-foreground" id="source-kind-hint">
      <p>Read directly: {extLabel(by.native)}.</p>
      <p>
        With kordoc{kordoc ? " (installed)" : ""}: {extLabel(by.kordoc)}.
        {!kordoc && <> These need kordoc: <Code>{KORDOC_INSTALL}</Code></>}
      </p>
      <p>
        Images, read by OCR{kordoc && ocr ? " (models ready)" : ""}: {extLabel(by["kordoc-ocr"])}.
        {kordoc && !ocr && <> The OCR models are missing: <Code>{KORDOC_INSTALL}</Code></>}
      </p>
    </div>
  )
}

function DeleteSourceDialog({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [dropDb, setDropDb] = useState(false)
  const remove = useMutation({
    mutationFn: (s: Source) => api.deleteSource(s.name, s.kind === "files" && dropDb),
    onSuccess: (r) => {
      toast.success(`Deleted source “${r.removed}”`, {
        description: r.messages.length ? r.messages.join(" · ") : "Rebuild to drop it from the ontology.",
      })
      setDropDb(false)  // a prop-driven close fires no onOpenChange
      onClose()
    },
    onError: (e) => toast.error("Delete failed", { description: errorMessage(e) }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.sources }),
  })
  const what = source?.kind === "documents" ? "its documents, chunks, concepts, relations and evidence"
    : "its upstream connection, ingested graph and run history"
  return (
    <AlertDialog open={!!source} onOpenChange={(o) => { if (!o && !remove.isPending) { onClose(); setDropDb(false) } }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete source “{source?.name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            This also deletes {what}. It cannot be undone.
            {remove.isPending && " Waiting for the graph cleanup can take up to 2 minutes."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {source?.kind === "files" && (
          <div className="flex items-start gap-3 rounded-md border p-3">
            <Switch id="drop-db" checked={dropDb} onCheckedChange={setDropDb} />
            <Label htmlFor="drop-db" className="grid gap-1 leading-snug">
              <span>Also drop the database</span>
              <span className="font-mono text-xs font-normal break-all text-muted-foreground">{source.database}</span>
            </Label>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
          <Button variant="destructive" disabled={remove.isPending}
                  onClick={() => source && remove.mutate(source)}>
            {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {remove.isPending ? "Deleting…" : "Delete"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
