import { lazy, Suspense, useEffect } from "react"
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router"

import { DefaultProjectRedirect, ProjectRoute, ProjectsRoute } from "@/components/project-scope"
import { EmptyState } from "@/components/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import BuildPage from "@/pages/build"
import ConnectPage from "@/pages/connect"
import ExportsPage from "@/pages/exports"
import ProjectsPage from "@/pages/projects"
import HomePage from "@/pages/home"
import IndexPage from "@/pages/overview"
import OntologyPage from "@/pages/ontology"
import ReviewPage from "@/pages/review"
import SourcesPage from "@/pages/sources"
import SettingsPage from "@/pages/settings"
import StatusPage from "@/pages/status"
import { pagePathname, projectPath, pathnameProject } from "@/lib/project"
import { legacyTarget } from "@/lib/routes"
import { useT } from "@/i18n"

// three.js and the force-graph libraries only load with this page
const GraphPage = lazy(() => import("@/pages/graph"))
const PlaygroundPage = lazy(() => import("@/pages/playground"))

/** `/p/<slug>/runs` -> `/p/<slug>/build` (and /concepts), see legacyTarget. */
function LegacyRedirect() {
  const { pathname, search } = useLocation()
  const target = legacyTarget(pagePathname(pathname), search)
  return target ? <Navigate to={projectPath(pathnameProject(pathname), target)} replace /> : null
}

const OLD_TABS = ["sources", "runs", "concepts", "graph", "exports", "status"]

/** Links to the first UI (/ui/#runs) land on the matching route. */
function LegacyHashRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    const tab = window.location.hash.slice(1)
    if (OLD_TABS.includes(tab)) navigate(legacyTarget(`/${tab}`, "") ?? `/${tab}`, { replace: true })
  }, [navigate])
  return null
}

const lazyPage = (Page: React.ComponentType) => (
  <Suspense fallback={<Skeleton className="m-6 h-[60svh]" />}><Page /></Suspense>
)

export default function App() {
  const t = useT()
  return (
    <>
      <LegacyHashRedirect />
      <Routes>
        <Route path="projects" element={<ProjectsRoute />}>
          <Route index element={<ProjectsPage />} />
        </Route>
        <Route path="p/:project" element={<ProjectRoute />}>
          <Route index element={<IndexPage />} />
          <Route path="setup" element={<HomePage />} />
          <Route path="review" element={<ReviewPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="build" element={<BuildPage />} />
          <Route path="ontology" element={<OntologyPage />} />
          <Route path="graph" element={lazyPage(GraphPage)} />
          <Route path="playground" element={lazyPage(PlaygroundPage)} />
          <Route path="exports" element={<ExportsPage />} />
          <Route path="connect" element={<ConnectPage />} />
          <Route path="status" element={<StatusPage />} />
          <Route path="runs" element={<LegacyRedirect />} />
          <Route path="concepts" element={<LegacyRedirect />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<EmptyState title={t("shell.pageMissing")} hint={t("shell.pageMissing.hint")} />} />
        </Route>
        {/* /ui/, /ui/sources?lang=ko, /ui/runs ...: the same page of the default project */}
        <Route path="*" element={<DefaultProjectRedirect />} />
      </Routes>
    </>
  )
}
