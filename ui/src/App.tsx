import { lazy, Suspense, useEffect } from "react"
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router"

import { AppShell } from "@/components/app-shell"
import { CommandMenu } from "@/components/command-menu"
import { EmptyState } from "@/components/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import BuildPage from "@/pages/build"
import ExportsPage from "@/pages/exports"
import OperationsHome from "@/pages/operations"
import ReviewPage from "@/pages/review"
import HistoryPage from "@/pages/history"
import HomePage from "@/pages/home"
import OntologyPage from "@/pages/ontology"
import SourcesPage from "@/pages/sources"
import SettingsPage from "@/pages/settings"
import StatusPage from "@/pages/status"
import AccessPage from "@/pages/access"
import { legacyTarget } from "@/lib/routes"
import { useT } from "@/i18n"
import { SHARED_DEMO } from "@/lib/demo"
import { AccessBoundary, CanUse } from "@/components/identity-gate"

const access = (capability: string, children: React.ReactNode) => <AccessBoundary capability={capability}>{children}</AccessBoundary>

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
  <Suspense fallback={<Skeleton className="m-6 h-[60svh]" />}>
    <Page />
  </Suspense>
)

export default function App() {
  const t = useT()
  return (
    <>
      <LegacyHashRedirect />
      <CommandMenu />
      <Routes>
        <Route element={<AppShell />}>
          <Route
            index
            element={
              SHARED_DEMO ? <Navigate to="/ontology" replace /> : <CanUse capability="candidate.read" fallback={<Navigate to="/ontology" replace />}><HomePage afterBuild={<OperationsHome />} /></CanUse>
            }
          />
          <Route path="review" element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("candidate.read", <ReviewPage />)} />
          <Route
            path="history"
            element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("candidate.read", <HistoryPage />)}
          />
          <Route path="setup" element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("settings.manage", <HomePage />)} />
          <Route
            path="sources"
            element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("source.read", <SourcesPage />)}
          />
          <Route path="build" element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("candidate.prepare", <BuildPage />)} />
          <Route path="ontology" element={access("knowledge.read", <OntologyPage />)} />
          <Route path="graph" element={access("knowledge.read", lazyPage(GraphPage))} />
          <Route path="playground" element={access("query.run", lazyPage(PlaygroundPage))} />
          <Route path="exports" element={access("exports.read", <ExportsPage />)} />
          <Route path="status" element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("settings.read", <StatusPage />)} />
          <Route path="access" element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : <AccessPage />} />
          <Route path="runs" element={<LegacyRedirect />} />
          <Route path="concepts" element={<LegacyRedirect />} />
          <Route
            path="settings"
            element={SHARED_DEMO ? <Navigate to="/ontology" replace /> : access("settings.read", <SettingsPage />)}
          />
          <Route
            path="*"
            element={<EmptyState title={t("shell.pageMissing")} hint={t("shell.pageMissing.hint")} />}
          />
        </Route>
      </Routes>
    </>
  )
}
