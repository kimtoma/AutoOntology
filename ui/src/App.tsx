import { lazy, Suspense, useEffect } from "react"
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router"

import { AppShell } from "@/components/app-shell"
import { CommandMenu } from "@/components/command-menu"
import { EmptyState } from "@/components/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import BuildPage from "@/pages/build"
import ExportsPage from "@/pages/exports"
import OntologyPage from "@/pages/ontology"
import SourcesPage from "@/pages/sources"
import StatusPage from "@/pages/status"
import { legacyTarget } from "@/lib/routes"

// three.js and the force-graph libraries only load with this page
const GraphPage = lazy(() => import("@/pages/graph"))
const PlaygroundPage = lazy(() => import("@/pages/playground"))

function LegacyRedirect() {
  const { pathname, search } = useLocation()
  const target = legacyTarget(pathname, search)
  return target ? <Navigate to={target} replace /> : null
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
  return (
    <>
      <LegacyHashRedirect />
      <CommandMenu />
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/sources" replace />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="build" element={<BuildPage />} />
          <Route path="ontology" element={<OntologyPage />} />
          <Route path="graph" element={lazyPage(GraphPage)} />
          <Route path="playground" element={lazyPage(PlaygroundPage)} />
          <Route path="exports" element={<ExportsPage />} />
          <Route path="status" element={<StatusPage />} />
          <Route path="runs" element={<LegacyRedirect />} />
          <Route path="concepts" element={<LegacyRedirect />} />
          <Route path="settings" element={<LegacyRedirect />} />
          <Route path="*" element={<EmptyState title="Page not found" hint="Pick a page in the sidebar." />} />
        </Route>
      </Routes>
    </>
  )
}
