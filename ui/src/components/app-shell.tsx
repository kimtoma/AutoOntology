import { Outlet, useLocation } from "react-router"

import { ArchivedBanner } from "@/components/archived-banner"
import { CommandMenu } from "@/components/command-menu"
import { ProjectLink as Link, ProjectNavLink as NavLink } from "@/components/project-link"
import { ProjectSwitcher } from "@/components/project-switcher"

import { LangToggle } from "@/components/lang-toggle"
import { ThemeToggle } from "@/components/theme"
import {
  Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader,
  SidebarInset, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail,
  SidebarTrigger, useSidebar,
} from "@/components/ui/sidebar"
import { isCurrent, NAV, OTHER_PAGES, useNav } from "@/components/nav"
import { UserMenu } from "@/components/user-menu"
import { useAccounts } from "@/lib/auth"
import { usePendingCount, useStatus } from "@/hooks/queries"
import { pagePathname, projectName, useProject } from "@/lib/project"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n"

function StatusBadge() {
  const t = useT()
  const { data, isError, isPending } = useStatus()
  const tone = isError ? "error" : data?.status ?? "pending"
  const label = isPending ? t("shell.status.checking") : isError ? t("shell.status.unreachable")
    : ["ok", "warn", "error"].includes(data.status) ? t(`shell.status.${data.status as "ok" | "warn" | "error"}`)
      : data.status
  const dot: Record<string, string> = {
    ok: "bg-success", warn: "bg-warning", error: "bg-destructive", pending: "bg-muted-foreground",
  }
  return (
    <Link to="/status" id="status-badge" data-status={tone}
          title={t("shell.status.title")}
          aria-label={t("shell.status.aria", { label })}
          className="inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium
                     outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <span className={cn("size-2 rounded-full", dot[tone])} aria-hidden />
      <span>{label}</span>
      {data?.providers && <span className="hidden text-muted-foreground sm:inline">· {data.providers.chat_provider}</span>}
    </Link>
  )
}

function ServiceFooter() {
  const t = useT()
  const { data } = useStatus()
  const host = data?.service_url ? data.service_url.replace(/^https?:\/\//, "") : "127.0.0.1"
  return (
    <p className="px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
      {host} · {t("shell.localOnly")}{data?.version ? ` · v${data.version}` : ""}
    </p>
  )
}

/** The number of review items waiting (pending-count, polled every 30 s). */
function ReviewBadge({ sr = false }: { sr?: boolean }) {
  const t = useT()
  const { data } = usePendingCount()
  const n = data?.pending ?? 0
  if (!n) return null
  // the visible badge sits outside the link; the link itself carries the count for readers
  if (sr) {
    return (
      <>
        <span className="sr-only"> ({t("nav.review.badge", { n })})</span>
        {/* the collapsed (icon) sidebar hides the number badge: a dot on the icon instead */}
        <span aria-hidden data-review-dot
              className="absolute top-1 right-1 hidden size-2 rounded-full bg-primary group-data-[collapsible=icon]:block" />
      </>
    )
  }
  return (
    <SidebarMenuBadge id="review-badge" data-count={n} aria-hidden
                      className="rounded-full bg-primary/10 text-primary">
      {n > 99 ? "99+" : n}
    </SidebarMenuBadge>
  )
}

function AppSidebar() {
  const pathname = pagePathname(useLocation().pathname)
  const { setOpenMobile, isMobile } = useSidebar()
  const t = useT()
  const nav = useNav()
  // the review badge polls the project: not on pages without one (accounts: no membership yet)
  const scope = useProject()
  const accounts = useAccounts()
  const hasProject = !(accounts && scope && !scope.project)
  return (
    <Sidebar collapsible="icon" aria-label={t("shell.mainMenu")}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="AutoOntology">
              <Link to="/" onClick={() => isMobile && setOpenMobile(false)}>
                <span className="flex aspect-square size-8 items-center justify-center rounded-md
                                 bg-sidebar-primary text-sidebar-primary-foreground" aria-hidden>
                  ◇
                </span>
                <span className="grid flex-1 text-left leading-tight">
                  <span className="font-semibold">AutoOntology</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {t("shell.tagline")}
                  </span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <ProjectSwitcher />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {nav.map((item) => (
                <SidebarMenuItem key={item.path}>
                  <SidebarMenuButton asChild tooltip={item.label}
                                     isActive={isCurrent(item, pathname)}>
                    <NavLink to={item.path} data-nav={item.id} end={item.path === "/"} className="relative"
                             onClick={() => isMobile && setOpenMobile(false)}>
                      <item.icon aria-hidden />
                      <span>{item.label}</span>
                      {item.id === "review" && hasProject && <ReviewBadge sr />}
                    </NavLink>
                  </SidebarMenuButton>
                  {item.id === "review" && hasProject && <ReviewBadge />}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <ServiceFooter />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function Crumbs() {
  const pathname = pagePathname(useLocation().pathname)
  const item = [...NAV, ...OTHER_PAGES].find((n) => isCurrent(n, pathname))
  const t = useT()
  const scope = useProject()
  const onProjects = pathname.startsWith("/projects")
  const unscoped = onProjects || pathname.startsWith("/users")
  const name = projectName(scope?.project, scope?.slug)
  return (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem className="hidden sm:inline-flex">
          <Link to="/" className="hover:text-foreground">AutoOntology</Link>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="hidden sm:inline-flex" />
        {!unscoped && name && (
          <>
            <BreadcrumbItem className="hidden min-w-0 md:inline-flex">
              <Link to="/" className="max-w-48 truncate hover:text-foreground" id="crumb-project">{name}</Link>
            </BreadcrumbItem>
            <BreadcrumbSeparator className="hidden md:inline-flex" />
          </>
        )}
        <BreadcrumbItem>
          <BreadcrumbPage>{onProjects ? t("projects.title") : item?.label ?? t("shell.notFound")}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

export function AppShell() {
  const pathname = pagePathname(useLocation().pathname)
  const slug = useProject()?.slug
  const fullBleed = pathname.startsWith("/graph")  // the graph canvas fills the area under the header
  const wide = typeof window === "undefined" || window.innerWidth >= 1024
  const t = useT()
  return (
    <SidebarProvider defaultOpen={wide}>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b
                           bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" aria-label={t("shell.toggleMenu")} />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <Crumbs />
          <div className="ml-auto flex items-center gap-1">
            <StatusBadge />
            <LangToggle />
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>
        <main id="main" className={cn("mx-auto w-full min-w-0 flex-1",
                                      fullBleed ? "p-0" : "max-w-6xl px-4 py-6 sm:px-6")}>
          <div className={cn(fullBleed && "[&>#archived-banner]:m-4")}><ArchivedBanner /></div>
          {/* a new project remounts the page: no state of the previous project's page stays */}
          <Outlet key={slug} />
        </main>
      </SidebarInset>
      <CommandMenu />
    </SidebarProvider>
  )
}
