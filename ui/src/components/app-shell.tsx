import { useState, type CSSProperties } from "react"
import { ArrowUpRight, Command, GitBranch, Search } from "lucide-react"
import { Link, Outlet, useLocation } from "react-router"
import { LangToggle } from "@/components/lang-toggle"
import { ThemeToggle } from "@/components/theme"
import { SectionNav } from "@/components/section-nav"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { openCommandMenu } from "@/lib/command-menu"
import { isCurrent, NAV } from "@/components/nav"
import { useOperations, useLegacy } from "@/hooks/operations"
import { useStatus } from "@/hooks/queries"
import { cn } from "@/lib/utils"
import { GATEWAY_ACCESS, SHARED_DEMO } from "@/lib/demo"
import { useT } from "@/i18n"
import { CanUse, IdentityControls } from "@/components/identity-gate"
import { useCanUse, useIdentity } from "@/lib/identity"

const navigationAuthority: Record<string, string> = { home: "candidate.read", review: "candidate.read", sources: "source.read", ontology: "knowledge.read", playground: "query.run", history: "candidate.read", settings: "settings.read" }

function StatusBadge() {
  const t = useT()
  const { data, isError, isPending } = useStatus()
  const tone = isError ? "error" : (data?.status ?? "pending")
  const label = isPending
    ? t("shell.status.checking")
    : isError
      ? t("shell.status.unreachable")
      : ["ok", "warn", "error"].includes(data.status)
        ? t(`shell.status.${data.status as "ok" | "warn" | "error"}`)
        : data.status
  return (
    <Link
      to="/status"
      id="status-badge"
      data-status={tone}
      title={t("shell.status.title")}
      aria-label={t("shell.status.aria", { label })}
      className="console-health"
    >
      <span
        className={cn(
          "status-dot",
          tone === "ok"
            ? "bg-success"
            : tone === "error"
              ? "bg-destructive"
              : tone === "warn"
                ? "bg-warning"
                : "bg-muted-foreground",
        )}
        aria-hidden
      />
      <span>{label}</span>
    </Link>
  )
}

function WorkspaceNavigation() {
  const identity = useIdentity()
  const { pathname } = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()
  const t = useT()
  const canReview = useCanUse("candidate.read")
  const operations = useOperations(!SHARED_DEMO && canReview)
  const legacy = useLegacy(1, "pending", !SHARED_DEMO && canReview)
  const pending =
    (operations.data?.counts.pending ?? 0) +
    (operations.data?.counts.held ?? 0) +
    (operations.data?.counts.feedback ?? 0) +
    (legacy.data?.counts.total ?? 0)
  return (
    <Sidebar collapsible="icon" className="console-sidebar">
      <SidebarHeader className="console-sidebar-header">
        <Link
          to="/"
          aria-label="AutoOntology"
          className="console-brand"
          onClick={() => isMobile && setOpenMobile(false)}
        >
          <svg width="25" height="25" viewBox="0 0 28 28" fill="none" aria-hidden>
            <path d="M14 3 24 9v10l-10 6-10-6V9L14 3Z" stroke="currentColor" strokeWidth="1.5" />
            <path d="m4 9 10 6 10-6M14 15v10M14 3v6" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="14" cy="15" r="2.5" fill="currentColor" />
          </svg>
          <span className="console-sidebar-name">AutoOntology</span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <nav id="workspace-navigation" aria-label={t("shell.mainMenu")} className="console-sidebar-nav">
          <SidebarMenu className="console-sidebar-menu">
            {NAV.map((item) => (
              <CanUse capability={navigationAuthority[item.id]} key={item.path}><SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={isCurrent(item, pathname)}
                  tooltip={item.label}
                  className="console-sidebar-link"
                >
                  <Link
                    to={item.path}
                    data-nav={item.id}
                    aria-label={item.label}
                    aria-current={isCurrent(item, pathname) ? "page" : undefined}
                    onClick={() => isMobile && setOpenMobile(false)}
                  >
                    <item.icon aria-hidden />
                    <span className="console-sidebar-name">{item.label}</span>
                    {item.id === "review" && pending > 0 && (
                      <span className="console-counter console-sidebar-count">{pending}</span>
                    )}
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem></CanUse>
            ))}
          </SidebarMenu>
        </nav>
      </SidebarContent>
      <SidebarFooter className="console-sidebar-footer">
        {identity && <Link to="/access" data-nav="access" className="console-sidebar-link text-xs" onClick={() => isMobile && setOpenMobile(false)}>{t("access.title")}</Link>}
        <span className="console-sidebar-name">Ontology Ops</span>
      </SidebarFooter>
    </Sidebar>
  )
}

function NavigationToggle() {
  const t = useT()
  const { open, openMobile, isMobile } = useSidebar()
  return (
    <SidebarTrigger
      aria-label={t("shell.toggleMenu")}
      aria-expanded={isMobile ? openMobile : open}
      aria-controls="workspace-navigation"
      title={t("shell.toggleMenu")}
    />
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const fullBleed = pathname.startsWith("/graph")
  const t = useT()
  const canReview = useCanUse("candidate.read")
  const operations = useOperations(!SHARED_DEMO && canReview)
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      return localStorage.getItem("autoontology.sidebar") !== "collapsed"
    } catch {
      return true
    }
  })
  const changeSidebar = (open: boolean) => {
    setSidebarOpen(open)
    try {
      localStorage.setItem("autoontology.sidebar", open ? "expanded" : "collapsed")
    } catch {
      /* Private browsing may deny storage. */
    }
  }
  return (
    <SidebarProvider
      open={sidebarOpen}
      onOpenChange={changeSidebar}
      className="console-layout"
      style={{ "--sidebar-width": "224px", "--sidebar-width-icon": "56px" } as CSSProperties}
    >
      <WorkspaceNavigation />
      <div className="console-shell">
        <a href="#main" className="console-skip">
          {t("console.skip")}
        </a>
        <header className="console-header">
          <div className="console-topbar">
            <NavigationToggle />
            <div className="console-workspace" title={operations.data?.workspace}>
              <span className="console-workspace-icon" aria-hidden>
                <GitBranch />
              </span>
              <span className="truncate">{operations.data?.workspace || t("console.workspace")}</span>
            </div>
            <span className="console-environment">
              {t(GATEWAY_ACCESS ? "console.remote" : "console.local")}
            </span>
            <div className="console-top-actions">
              <button
                type="button"
                className="console-search"
                onClick={openCommandMenu}
                aria-label={t("cmd.title")}
              >
                <Search className="size-3.5" aria-hidden />
                <span>{t("console.search")}</span>
                <kbd>
                  <Command className="size-3" aria-hidden /> K
                </kbd>
              </button>
              {!SHARED_DEMO && <CanUse capability="settings.read"><StatusBadge /></CanUse>}
              <IdentityControls />
              <div className="console-preferences">
                <LangToggle />
                <ThemeToggle />
              </div>
            </div>
          </div>
        </header>
        {GATEWAY_ACCESS && (
          <div className="console-remote-note" role="note">
            <span>{t(SHARED_DEMO ? "shell.demo.hint" : "shell.remote.hint")}</span>
            <form action="/__demo/logout" method="post">
              <button className="underline underline-offset-4" type="submit">
                {t("shell.demo.logout")}
              </button>
            </form>
          </div>
        )}
        <main id="main" tabIndex={-1} className={cn("console-main", fullBleed && "console-main-graph")}>
          <div className={fullBleed ? "px-5 pt-4" : undefined}>
            <SectionNav />
          </div>
          <Outlet />
        </main>
        {!fullBleed && (
          <footer className="console-footer">
            <span>
              AutoOntology <span className="mx-2 text-border">/</span> Ontology Ops
            </span>
            <CanUse capability="candidate.read"><Link to="/history">
              {t("nav.history")}
              <ArrowUpRight className="size-3" aria-hidden />
            </Link></CanUse>
          </footer>
        )}
      </div>
    </SidebarProvider>
  )
}
