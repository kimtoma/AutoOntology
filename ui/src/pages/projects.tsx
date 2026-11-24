/* Projects (`/projects`): every project with its latest revision, sources and last build;
   open, edit (name, slug, description), archive and unarchive. */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Archive, ArchiveRestore, ArrowRight, Loader2, Pencil, Plus } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { EditProjectDialog, NewProjectDialog } from "@/components/project-dialogs"
import { StatusChip } from "@/components/status-chip"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { keys, useProjects } from "@/hooks/queries"
import { fmtDate, fmtNumber, useT } from "@/i18n"
import { api, type Project } from "@/lib/api"
import { projectErrorMessage, projectName, projectPath } from "@/lib/project"
import { cn } from "@/lib/utils"

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="truncate text-sm">{children}</span>
    </div>
  )
}

function ProjectRow({ p, onEdit, onArchive }: {
  p: Project; onEdit: (p: Project) => void; onArchive: (p: Project) => void
}) {
  const t = useT()
  const archive = (
    <Button variant="ghost" size="sm" disabled={p.default} onClick={() => onArchive(p)}
            data-action={p.archived ? "unarchive" : "archive"}
            aria-label={t(p.archived ? "projects.unarchive.aria" : "projects.archive.aria", { name: projectName(p) })}>
      {p.archived ? <ArchiveRestore aria-hidden /> : <Archive aria-hidden />}
      {p.archived ? t("projects.unarchive") : t("projects.archive")}
    </Button>
  )
  return (
    <Card className={cn("grid gap-4 p-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,2fr)_18rem] md:items-center",
                        p.archived && "bg-muted/40")}
          data-project-row={p.slug} data-archived={p.archived || undefined}>
      <div className="grid min-w-0 gap-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Link to={projectPath(p.slug, "/")}
                className={cn("truncate font-semibold hover:underline", p.archived && "text-muted-foreground")}>
            {projectName(p)}
          </Link>
          {p.default && <Badge variant="secondary">{t("projects.default")}</Badge>}
          {p.active && <Badge variant="outline" className="border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300" data-badge="active">
            <Loader2 className="animate-spin" aria-hidden /> {t("projects.active")}
          </Badge>}
          {p.archived && <Badge variant="outline" className="border-warning/40 bg-warning/10 text-amber-700 dark:text-amber-300" data-badge="archived">
            <Archive aria-hidden /> {t("projects.archived")}
          </Badge>}
        </div>
        <span className="truncate font-mono text-xs text-muted-foreground">{p.slug}</span>
        {p.description && <p className="line-clamp-2 text-sm text-muted-foreground">{p.description}</p>}
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Stat label={t("projects.col.revision")}>
          {p.latest_revision
            ? <span title={fmtDate(p.latest_revision_at)}>r{p.latest_revision}
                <span className="text-xs text-muted-foreground"> · {fmtDate(p.latest_revision_at)}</span></span>
            : <span className="text-muted-foreground">{t("projects.notBuilt")}</span>}
        </Stat>
        <Stat label={t("projects.col.sources")}>{fmtNumber(p.sources)}</Stat>
        <Stat label={t("projects.col.lastBuild")}>
          {p.last_build ? <StatusChip status={p.last_build.status} /> : <span className="text-muted-foreground">—</span>}
        </Stat>
      </div>
      <div className="flex flex-wrap items-center gap-1 md:justify-end">
        <Button size="sm" variant="outline" asChild>
          <Link to={projectPath(p.slug, "/")} data-action="open"
                aria-label={t("projects.open.aria", { name: projectName(p) })}>
            {t("projects.open")} <ArrowRight aria-hidden />
          </Link>
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onEdit(p)} data-action="edit"
                aria-label={t("projects.edit.aria", { name: projectName(p) })}>
          <Pencil aria-hidden /> {t("projects.edit")}
        </Button>
        {p.default ? (
          <Tooltip>
            <TooltipTrigger asChild><span tabIndex={0} data-default-archive>{archive}</span></TooltipTrigger>
            <TooltipContent>{t("projects.archive.defaultHint")}</TooltipContent>
          </Tooltip>
        ) : archive}
      </div>
    </Card>
  )
}

export default function ProjectsPage() {
  const t = useT()
  const qc = useQueryClient()
  const projects = useProjects()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [confirm, setConfirm] = useState<Project | null>(null)
  const toggle = useMutation({
    mutationFn: (p: Project) => (p.archived ? api.projects.unarchive(p.slug) : api.projects.archive(p.slug)),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: keys.projects })
      setConfirm(null)
      toast.success(t(p.archived ? "projects.archived.done" : "projects.unarchived.done", { name: projectName(p) }))
    },
    onError: (e) => toast.error(t("projects.saveFailed"), { description: projectErrorMessage(e) }),
  })
  const list = projects.data?.projects ?? []
  const sorted = [...list].sort((a, b) => Number(a.archived) - Number(b.archived))
  return (
    <>
      <PageHeader title={t("projects.title")} description={t("projects.description")}
                  actions={<Button onClick={() => setCreating(true)} id="btn-new-project"><Plus aria-hidden /> {t("projects.new")}</Button>} />
      {projects.error ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        : projects.isPending ? <Skeleton className="h-40" />
          : !list.length ? <EmptyState title={t("projects.empty")} />
            : (
              <div className="grid gap-3" id="project-list">
                {sorted.map((p) => <ProjectRow key={p.slug} p={p} onEdit={setEditing} onArchive={setConfirm} />)}
              </div>
            )}
      <NewProjectDialog open={creating} onOpenChange={setCreating} />
      <EditProjectDialog project={editing} onClose={() => setEditing(null)} />
      <AlertDialog open={!!confirm} onOpenChange={(o) => { if (!o && !toggle.isPending) setConfirm(null) }}>
        <AlertDialogContent id="archive-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirm && t(confirm.archived ? "projects.unarchive.title" : "projects.archive.title", { name: projectName(confirm) })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirm && t(confirm.archived ? "projects.unarchive.body" : "projects.archive.body")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={toggle.isPending}>{t("common.cancel")}</AlertDialogCancel>
            <Button id="btn-confirm-archive" disabled={toggle.isPending} onClick={() => confirm && toggle.mutate(confirm)}>
              {toggle.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {confirm && t(confirm.archived ? "projects.unarchive" : "projects.archive")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
