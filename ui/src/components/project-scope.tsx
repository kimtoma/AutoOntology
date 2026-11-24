/* The project routes: `/p/:project/*` sets the current project (API base, query keys, links)
   for the pages under it; `/projects` uses the last opened (or the default) project for the
   shell around the projects page; every other path is an older URL that lands on the same
   page of the default project (query and hash kept: `/ui/sources?lang=ko` ->
   `/ui/p/<default>/sources?lang=ko`). An unknown project (a slug renamed meanwhile) says so
   and goes to the default project. */

import { useEffect, useMemo } from "react"
import { Navigate, useLocation, useParams } from "react-router"
import { toast } from "sonner"

import { AppShell } from "@/components/app-shell"
import { ErrorState } from "@/components/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { useProjects } from "@/hooks/queries"
import { useT } from "@/i18n"
import { setCurrentProject, type Project } from "@/lib/api"
import { lastProject, pagePathname, projectPath, ProjectContext, rememberProject } from "@/lib/project"
import { legacyTarget } from "@/lib/routes"

const Loading = () => <Skeleton className="m-6 h-[60svh]" id="project-loading" />

/** The shell and pages of project *slug*. */
function Scoped({ slug, project, remember }: { slug: string; project: Project | null; remember: boolean }) {
  // before the pages render: their API calls and query keys read it
  setCurrentProject(slug)
  useEffect(() => { if (remember) rememberProject(slug) }, [slug, remember])
  const value = useMemo(() => ({ slug, project, archived: !!project?.archived }), [slug, project])
  return (
    <ProjectContext.Provider value={value}>
      <AppShell />
    </ProjectContext.Provider>
  )
}

function UnknownProject({ slug, to }: { slug: string; to: string }) {
  const t = useT()
  useEffect(() => {
    toast.error(t("projects.unknown", { slug }), { id: `unknown-project-${slug}` })
  }, [slug, t])
  return <Navigate to={to} replace />
}

/** `/p/:project/*`. */
export function ProjectRoute() {
  const { project: slug = "" } = useParams()
  const { pathname, search, hash } = useLocation()
  const projects = useProjects()
  if (projects.isPending) return <Loading />
  // the service is unreachable: the pages explain it (the setup checklist, error states)
  if (projects.isError) return <Scoped slug={slug} project={null} remember={false} />
  const project = projects.data.projects.find((p) => p.slug === slug) ?? null
  if (!project) {
    return <UnknownProject slug={slug}
                           to={projectPath(projects.data.default, pagePathname(pathname)) + search + hash} />
  }
  return <Scoped slug={slug} project={project} remember />
}

/** `/projects`: the shell of the last opened project (else the default one) around the page. */
export function ProjectsRoute() {
  const projects = useProjects()
  if (projects.isPending) return <Loading />
  if (projects.isError) return <div className="p-6"><ErrorState error={projects.error} onRetry={() => projects.refetch()} /></div>
  const last = lastProject()
  const project = projects.data.projects.find((p) => p.slug === last)
    ?? projects.data.projects.find((p) => p.slug === projects.data.default) ?? null
  return <Scoped slug={project?.slug ?? projects.data.default} project={project} remember={false} />
}

/** Any other path: the same page in the default project (`/` -> its home). */
export function DefaultProjectRedirect() {
  const { pathname, search, hash } = useLocation()
  const projects = useProjects()
  if (projects.isPending) return <Loading />
  if (projects.isError) return <div className="p-6"><ErrorState error={projects.error} onRetry={() => projects.refetch()} /></div>
  const page = legacyTarget(pathname, search) ?? `${pathname}${search}`
  return <Navigate to={projectPath(projects.data.default, page) + hash} replace />
}
