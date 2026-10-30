import { ArrowUpRight, Command, GitBranch, Search, Slash } from "lucide-react"
import { Link, Outlet, useLocation } from "react-router"
import { LangToggle } from "@/components/lang-toggle"
import { ThemeToggle } from "@/components/theme"
import { SectionNav } from "@/components/section-nav"
import { openCommandMenu } from "@/lib/command-menu"
import { isCurrent, NAV } from "@/components/nav"
import { useOperations, useLegacy } from "@/hooks/operations"
import { useStatus } from "@/hooks/queries"
import { cn } from "@/lib/utils"
import { GATEWAY_ACCESS, SHARED_DEMO } from "@/lib/demo"
import { useT } from "@/i18n"

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

export function AppShell() {
  const { pathname } = useLocation()
  const fullBleed = pathname.startsWith("/graph")
  const t = useT()
  const operations = useOperations(!SHARED_DEMO)
  const legacy = useLegacy(1, "pending", !SHARED_DEMO)
  const pending =
    (operations.data?.counts.pending ?? 0) +
    (operations.data?.counts.held ?? 0) +
    (operations.data?.counts.feedback ?? 0) +
    (legacy.data?.counts.total ?? 0)
  return (
    <div className="console-shell">
      <a href="#main" className="console-skip">
        {t("console.skip")}
      </a>
      <header className="console-header">
        <div className="console-topbar">
          <Link to="/" aria-label="AutoOntology" className="console-brand">
            <svg width="25" height="25" viewBox="0 0 28 28" fill="none" aria-hidden>
              <path d="M14 3 24 9v10l-10 6-10-6V9L14 3Z" stroke="currentColor" strokeWidth="1.5" />
              <path d="m4 9 10 6 10-6M14 15v10M14 3v6" stroke="currentColor" strokeWidth="1.5" />
              <circle cx="14" cy="15" r="2.5" fill="currentColor" />
            </svg>
            <span>AutoOntology</span>
          </Link>
          <Slash className="console-divider" aria-hidden />
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
            {!SHARED_DEMO && <StatusBadge />}
            <div className="console-preferences">
              <LangToggle />
              <ThemeToggle />
            </div>
          </div>
        </div>
        <div className="console-nav-wrap">
          <nav aria-label={t("shell.mainMenu")} className="console-nav">
            {NAV.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                data-nav={item.id}
                aria-current={isCurrent(item, pathname) ? "page" : undefined}
                className={cn("console-nav-link", isCurrent(item, pathname) && "is-current")}
              >
                {item.label}
                {item.id === "review" && pending > 0 && <span className="console-counter">{pending}</span>}
              </Link>
            ))}
          </nav>
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
          <Link to="/history">
            {t("nav.history")}
            <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        </footer>
      )}
    </div>
  )
}
