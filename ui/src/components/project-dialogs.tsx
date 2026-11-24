/* "New project" and "Edit project" dialogs: name, slug (suggested from the name, editable,
   checked against ^[a-z][a-z0-9-]{1,23}$ and the existing slugs) and description. */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { keys, useProjects } from "@/hooks/queries"
import { useT } from "@/i18n"
import { api, SLUG_RE, type Project, type ProjectChange } from "@/lib/api"
import { freeSlug, projectErrorMessage, projectName, rememberProject, suggestSlug } from "@/lib/project"

type SlugProblem = "format" | "taken" | null

function slugProblem(slug: string, taken: Set<string>, own?: string): SlugProblem {
  if (!SLUG_RE.test(slug)) return "format"
  if (slug !== own && taken.has(slug)) return "taken"
  return null
}

function ProjectForm({ id, initial, own, busy, submitLabel, onSubmit, onCancel }: {
  id: string
  initial: { name: string; slug: string; description: string }
  own?: string
  busy: boolean
  submitLabel: string
  onSubmit: (v: { name: string; slug: string; description: string }) => void
  onCancel: () => void
}) {
  const t = useT()
  const projects = useProjects()
  const taken = new Set((projects.data?.projects ?? []).map((p) => p.slug))
  const [name, setName] = useState(initial.name)
  const [slug, setSlug] = useState(initial.slug)
  const [slugEdited, setSlugEdited] = useState(!!own)
  const [description, setDescription] = useState(initial.description)
  const [touched, setTouched] = useState(false)
  const shownSlug = slugEdited ? slug : (suggestSlug(name) || freeSlug(taken))
  const problem = slugProblem(shownSlug, taken, own)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (problem || !name.trim()) return
    onSubmit({ name: name.trim(), slug: shownSlug, description: description.trim() })
  }
  const showProblem: SlugProblem = touched || slugEdited ? problem : null
  return (
    <form id={id} onSubmit={submit} className="grid gap-4" noValidate>
      <div className="grid gap-2">
        <Label htmlFor={`${id}-name`}>{t("projects.form.name")}</Label>
        <Input id={`${id}-name`} value={name} autoFocus maxLength={200} required
               placeholder={t("projects.form.name.placeholder")}
               aria-invalid={touched && !name.trim()}
               onChange={(e) => setName(e.target.value)} />
        {touched && !name.trim() && <p className="text-xs text-destructive">{t("projects.form.name.required")}</p>}
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`${id}-slug`}>{t("projects.form.slug")}</Label>
        <Input id={`${id}-slug`} value={shownSlug} maxLength={24} spellCheck={false} autoCapitalize="off"
               className="font-mono" aria-invalid={!!showProblem} aria-describedby={`${id}-slug-hint`}
               onChange={(e) => { setSlugEdited(true); setSlug(e.target.value.toLowerCase()) }} />
        <p id={`${id}-slug-hint`} className={showProblem ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
           data-slug-problem={showProblem ? problem : undefined}>
          {showProblem === "taken" ? t("projects.form.slug.taken")
            : showProblem === "format" ? t("projects.form.slug.format")
              : t("projects.form.slug.hint", { url: `/ui/p/${shownSlug}` })}
        </p>
        {own && shownSlug !== own && <p className="text-xs text-muted-foreground">{t("projects.form.slug.changeNote")}</p>}
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`${id}-description`}>{t("projects.form.description")}</Label>
        <Textarea id={`${id}-description`} value={description} maxLength={2000} rows={3}
                  placeholder={t("projects.form.description.placeholder")}
                  onChange={(e) => setDescription(e.target.value)} />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={busy} id={`${id}-submit`}>
          {busy && <Loader2 className="animate-spin" aria-hidden />} {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  )
}

/** "New project": creates it and opens its home (the first-run checklist). */
export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const create = useMutation({
    mutationFn: (v: { name: string; slug: string; description: string }) => api.projects.create(v),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: keys.projects })
      onOpenChange(false)
      rememberProject(p.slug)
      toast.success(t("projects.created", { name: projectName(p) }))
      navigate(`/p/${encodeURIComponent(p.slug)}`)
    },
    onError: (e) => toast.error(t("projects.createFailed"), { description: projectErrorMessage(e) }),
  })
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!create.isPending) onOpenChange(o) }}>
      <DialogContent className="sm:max-w-md" id="new-project-dialog">
        <DialogHeader>
          <DialogTitle>{t("projects.new.title")}</DialogTitle>
          <DialogDescription>{t("projects.new.description")}</DialogDescription>
        </DialogHeader>
        {open && (
          <ProjectForm id="new-project" initial={{ name: "", slug: "", description: "" }}
                       busy={create.isPending} submitLabel={t("projects.new.submit")}
                       onSubmit={(v) => create.mutate(v)} onCancel={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  )
}

/** "Edit project": name, slug and description (a new slug opens the project at its new URL). */
export function EditProjectDialog({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const save = useMutation({
    mutationFn: ({ slug, change }: { slug: string; change: ProjectChange }) => api.projects.update(slug, change),
    onSuccess: (p, { slug }) => {
      qc.invalidateQueries({ queryKey: keys.projects })
      onClose()
      toast.success(t("projects.saved", { name: projectName(p) }))
      if (p.slug !== slug) {
        rememberProject(p.slug)
        navigate(`/p/${encodeURIComponent(p.slug)}`)
      }
    },
    onError: (e) => toast.error(t("projects.saveFailed"), { description: projectErrorMessage(e) }),
  })
  const onSubmit = (v: { name: string; slug: string; description: string }) => {
    if (!project) return
    const change: ProjectChange = {}
    if (v.name !== project.name) change.name = v.name
    if (v.description !== (project.description ?? "")) change.description = v.description
    if (v.slug !== project.slug) change.slug = v.slug
    if (!Object.keys(change).length) { onClose(); return }
    save.mutate({ slug: project.slug, change })
  }
  return (
    <Dialog open={!!project} onOpenChange={(o) => { if (!o && !save.isPending) onClose() }}>
      <DialogContent className="sm:max-w-md" id="edit-project-dialog">
        <DialogHeader>
          <DialogTitle>{t("projects.edit.title")}</DialogTitle>
          <DialogDescription>{t("projects.edit.description")}</DialogDescription>
        </DialogHeader>
        {project && (
          <ProjectForm key={project.slug} id="edit-project" own={project.slug}
                       initial={{ name: project.name, slug: project.slug, description: project.description ?? "" }}
                       busy={save.isPending} submitLabel={t("projects.edit.submit")}
                       onSubmit={onSubmit} onCancel={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
