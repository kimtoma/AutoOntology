import { SourceFreshness } from "@/components/source-freshness"
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
import { t, tn, tx, useT } from "@/i18n"
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
    return <StatusChip status="running" label={t("sources.runRunning", { kind: RUN_LABEL[s.active_run.kind] ?? s.active_run.kind })} />
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
  const t = useT()
  const { data: sources, isPending, error, refetch } = useSources()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Source | null>(null)

  return (
    <>
      <PageHeader
        title={t("sources.title")}
        description={tx("sources.description", {
          structured: <b key="s">{t("sources.description.structured")}</b>,
          documents: <b key="d">{t("sources.description.documents")}</b>,
          build: <Link key="b" to="/build" className="underline underline-offset-4">{t("sources.description.build")}</Link>,
        })}
        actions={<Button onClick={() => setAdding(true)} id="btn-new-source">
          <Plus aria-hidden /> {t("sources.add")}</Button>}
      />
      <SourceFreshness />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-48 w-full" />
        : !sources.length ? (
          <EmptyState icon={Database} title={t("sources.empty")}
                      hint={t("sources.empty.hint")}
                      action={<Button onClick={() => setAdding(true)}><Plus aria-hidden /> {t("sources.add")}</Button>} />
        ) : (
          <>
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="sources-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">{t("sources.col.name")}</TableHead>
                      <TableHead>{t("sources.col.kind")}</TableHead>
                      <TableHead className="hidden lg:table-cell">{t("sources.col.location")}</TableHead>
                      <TableHead className="hidden md:table-cell">{t("sources.col.state")}</TableHead>
                      <TableHead className="hidden sm:table-cell">{t("sources.col.lastRun")}</TableHead>
                      <TableHead className="w-12 pr-4"><span className="sr-only">{t("sources.col.actions")}</span></TableHead>
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
                              {KIND_LABEL[s.kind]}{s.uploaded ? t("sources.uploaded") : ""}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="hidden max-w-72 lg:table-cell">
                          <span className="block truncate font-mono text-xs" title={s.locator}>{s.locator}</span>
                        </TableCell>
                        <TableCell className="hidden md:table-cell">{sourceState(s)}</TableCell>
                        <TableCell className="hidden sm:table-cell"><LastRun s={s} /></TableCell>
                        <TableCell className="pr-4 text-right">
                          <Button variant="ghost" size="icon" aria-label={t("sources.delete.aria", { name: s.name })}
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
              {tx("sources.next", {
                link: (
                  <Link key="build" to="/build" className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4">
                    <Hammer className="size-3.5" aria-hidden /> {t("sources.next.link")}</Link>
                ),
              })}
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
  const t = useT()
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
        if (!files.length) throw new Error(t("sources.err.noFiles"))
        if (unsupported.length) {
          throw new Error(tn("sources.err.unsupported", unsupported.length, { names: unsupported.map((c) => c.name).join(", ") }))
        }
        if (tooLarge) {
          throw new Error(t("sources.err.tooLarge", { size: fmtBytes(totalBytes), mb: limitMb }))
        }
        return api.addSourceUpload(name.trim(), kind as "files" | "documents", files)
      }
      return api.addSourceLocator({ name: name.trim(), kind, locator: locator.trim(), recursive })
    },
    onSuccess: (s) => {
      toast.success(t("sources.added", { name: s.name }), { description: t("sources.added.next") })
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
            <DialogTitle>{t("sources.dialog.title")}</DialogTitle>
            <DialogDescription>{t("sources.dialog.description")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>{t("sources.dialog.kind")}</Label>
            <Tabs value={family} onValueChange={(v) => { setFamily(v as Family); setChecked([]) }}>
              <TabsList className="w-full" id="source-kind">
                <TabsTrigger value="structured" data-family="structured"><Database aria-hidden /> {t("family.structured")}</TabsTrigger>
                <TabsTrigger value="documents" data-family="documents"><FileText aria-hidden /> {t("family.documents")}</TabsTrigger>
              </TabsList>
            </Tabs>
            <FormatHint family={family} formats={formats} kordoc={!!kordoc} ocr={!!ocr} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="source-name">{t("sources.dialog.name")}</Label>
            <Input id="source-name" required value={name} onChange={(e) => setName(e.target.value)}
                   pattern={family === "documents" ? "[a-z0-9][a-z0-9_\\-]*" : "[a-z0-9_]+"} autoComplete="off"
                   placeholder={family === "documents" ? t("sources.dialog.name.docs") : t("sources.dialog.name.structured")} />
          </div>
          <div className="grid gap-2">
            <Label>{t("sources.dialog.from")}</Label>
            <Tabs value={effectiveMode} onValueChange={(v) => setMode(v as Mode)}>
              <TabsList className="w-full">
                <TabsTrigger value="upload">{t("sources.dialog.upload")}</TabsTrigger>
                <TabsTrigger value="path">{t("sources.dialog.folder")}</TabsTrigger>
                {family === "structured" && <TabsTrigger value="database">{t("sources.dialog.db")}</TabsTrigger>}
              </TabsList>
            </Tabs>
          </div>
          {upload ? (
            <div className="grid gap-2">
              <Label htmlFor="source-files">{t("sources.dialog.files")}</Label>
              <Input id="source-files" type="file" multiple accept={acceptList(formats, family) || undefined}
                     onChange={(e) => setFiles([...(e.target.files ?? [])])} />
              <p className={tooLarge ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
                 id="upload-limit">
                {files.length > 0 && t("sources.dialog.selected", { n: files.length, size: fmtBytes(totalBytes) })}
                {t("sources.dialog.limit", { mb: limitMb })}
              </p>
              <FilePreview checks={fileChecks} />
              {unsupported.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-destructive" role="alert" id="unsupported-files">
                  <span className="min-w-0 flex-1">{t("sources.dialog.refuses")}</span>
                  <Button type="button" size="sm" variant="outline"
                          onClick={() => setFiles(files.filter((f) => classifyFile(f.name, f.size, family, formats, engines).state !== "unsupported"))}>
                    {t("sources.dialog.removeUnsupported", { n: unsupported.length })}
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="source-locator">{effectiveMode === "database" ? t("sources.dialog.dbUrl") : t("sources.dialog.serverFolder")}</Label>
                <Input id="source-locator" required value={locator}
                       onChange={(e) => setLocator(e.target.value)} autoComplete="off"
                       className="font-mono text-xs"
                       placeholder={effectiveMode === "database" ? "postgresql://user:pass@host:5432/db"
                         : "/absolute/path/to/folder"} />
                {effectiveMode === "path" && (
                  <p className="text-xs text-muted-foreground">{t("sources.dialog.serverFolder.hint")}</p>
                )}
              </div>
              {effectiveMode === "path" && (
                <>
                  <div className="flex items-center gap-2">
                    <Switch id="source-recursive" checked={recursive} onCheckedChange={setRecursive} />
                    <Label htmlFor="source-recursive">{t("sources.dialog.recursive")}</Label>
                  </div>
                  <FolderCheck recursive={recursive} onNames={setChecked} />
                  <FilePreview checks={pathChecks} />
                </>
              )}
            </>
          )}
          {error && <p role="alert" className="text-sm break-words text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => { onOpenChange(false); reset() }}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={add.isPending || (upload && (tooLarge || unsupported.length > 0))}>
              {add.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {add.isPending ? t("sources.dialog.adding") : t("sources.dialog.add")}
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
  const t = useT()
  const by = extensionsByEngine(formats, family)
  if (!formats) return <p className="text-xs text-muted-foreground" id="source-kind-hint">{t("sources.hint.checking")}</p>
  if (family === "structured") {
    return (
      <p className="text-xs text-muted-foreground" id="source-kind-hint">
        {t("sources.hint.tables", { exts: extLabel(by.native) })}
      </p>
    )
  }
  return (
    <div className="grid gap-1 text-xs text-muted-foreground" id="source-kind-hint">
      <p>{t("sources.hint.native", { exts: extLabel(by.native) })}</p>
      <p>
        {t(kordoc ? "sources.hint.kordocInstalled" : "sources.hint.kordoc", { exts: extLabel(by.kordoc) })}
        {!kordoc && tx("sources.hint.kordocNeeded", { cmd: <Code key="cmd">{KORDOC_INSTALL}</Code> })}
      </p>
      <p>
        {t(kordoc && ocr ? "sources.hint.imagesReady" : "sources.hint.images", { exts: extLabel(by["kordoc-ocr"]) })}
        {kordoc && !ocr && tx("sources.hint.ocrMissing", { cmd: <Code key="cmd">{KORDOC_INSTALL}</Code> })}
        {!kordoc && tx("sources.hint.imagesNeedKordoc", { cmd: <Code key="cmd">{KORDOC_INSTALL}</Code> })}
      </p>
    </div>
  )
}

function DeleteSourceDialog({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [dropDb, setDropDb] = useState(false)
  const remove = useMutation({
    mutationFn: (s: Source) => api.deleteSource(s.name, s.kind === "files" && dropDb),
    onSuccess: (r) => {
      toast.success(t("sources.delete.ok", { name: r.removed }), {
        description: r.messages.length ? r.messages.join(" · ") : t("sources.delete.rebuild"),
      })
      setDropDb(false)  // a prop-driven close fires no onOpenChange
      onClose()
    },
    onError: (e) => toast.error(t("sources.delete.failed"), { description: errorMessage(e) }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.sources }),
  })
  const what = source?.kind === "documents" ? t("sources.delete.what.docs") : t("sources.delete.what.files")
  return (
    <AlertDialog open={!!source} onOpenChange={(o) => { if (!o && !remove.isPending) { onClose(); setDropDb(false) } }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("sources.delete.title", { name: source?.name })}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("sources.delete.body", { what })}
            {remove.isPending && t("sources.delete.wait")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {source?.kind === "files" && (
          <div className="flex items-start gap-3 rounded-md border p-3">
            <Switch id="drop-db" checked={dropDb} onCheckedChange={setDropDb} />
            <Label htmlFor="drop-db" className="grid gap-1 leading-snug">
              <span>{t("sources.delete.dropDb")}</span>
              <span className="font-mono text-xs font-normal break-all text-muted-foreground">{source.database}</span>
            </Label>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>{t("common.cancel")}</AlertDialogCancel>
          <Button variant="destructive" disabled={remove.isPending}
                  onClick={() => source && remove.mutate(source)}>
            {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {remove.isPending ? t("sources.delete.deleting") : t("sources.delete.button")}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
