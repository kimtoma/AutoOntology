import { lazy, Suspense, useEffect } from "react"
import { Navigate, Route, Routes, useNavigate } from "react-router"

import { AppShell } from "@/components/app-shell"
import { CommandMenu } from "@/components/command-menu"
import { EmptyState } from "@/components/empty-state"
import ConceptsPage from "@/pages/concepts"
import ExportsPage from "@/pages/exports"
import RunsPage from "@/pages/runs"
import SettingsPage from "@/pages/settings"
import SourcesPage from "@/pages/sources"
import StatusPage from "@/pages/status"
import { Skeleton } from "@/components/ui/skeleton"

// three.js and the force-graph libraries only load with this page
const GraphPage = lazy(() => import("@/pages/graph"))
const PlaygroundPage = lazy(() => import("@/pages/playground"))

const OLD_TABS = ["sources", "runs", "concepts", "graph", "exports", "status"]

/** Links to the old UI (/ui/#runs) land on the matching route. */
function LegacyHashRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    const tab = window.location.hash.slice(1)
    if (OLD_TABS.includes(tab)) navigate(`/${tab}`, { replace: true })
  }, [navigate])
  return null
}

export default function App() {
  return (
    <>
      <LegacyHashRedirect />
      <CommandMenu />
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/sources" replace />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="concepts" element={<ConceptsPage />} />
          <Route path="graph" element={
            <Suspense fallback={<Skeleton className="m-6 h-[60svh]" />}><GraphPage /></Suspense>} />
          <Route path="playground" element={
            <Suspense fallback={<Skeleton className="m-6 h-[60svh]" />}><PlaygroundPage /></Suspense>} />
          <Route path="exports" element={<ExportsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="status" element={<StatusPage />} />
          <Route path="*" element={<EmptyState title="페이지를 찾을 수 없습니다" hint="왼쪽 메뉴에서 페이지를 고르세요." />} />
        </Route>
      </Routes>
    </>
  )
}
