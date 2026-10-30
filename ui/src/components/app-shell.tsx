import { SectionNav } from "@/components/section-nav"
import { Link, NavLink, Outlet, useLocation } from "react-router"

import { LangToggle } from "@/components/lang-toggle"
import { ThemeToggle } from "@/components/theme"
import {
  Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader,
  SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail,
  SidebarTrigger, useSidebar,
} from "@/components/ui/sidebar"
import { isCurrent, NAV } from "@/components/nav"
import { useOperations, useLegacy } from "@/hooks/operations"
import { useStatus } from "@/hooks/queries"
import { cn } from "@/lib/utils"
import { GATEWAY_ACCESS, SHARED_DEMO } from "@/lib/demo"
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

function AppSidebar() {
  const { pathname } = useLocation()
  const { setOpenMobile, isMobile } = useSidebar()
  const t = useT()
  const operations = useOperations(!SHARED_DEMO)
  const legacy = useLegacy(1, "pending", !SHARED_DEMO)
  const pending = (operations.data?.counts.pending ?? 0) + (operations.data?.counts.held ?? 0) + (operations.data?.counts.feedback ?? 0) + (legacy.data?.counts.total ?? 0)
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
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.filter(item => item.id !== "settings").map((item) => (
                <SidebarMenuItem key={item.path}>
                  <SidebarMenuButton asChild tooltip={item.label}
                                     isActive={isCurrent(item, pathname)}>
                    <NavLink to={item.path} data-nav={item.id} end={item.path === "/"}
                             onClick={() => isMobile && setOpenMobile(false)}>
                      <item.icon aria-hidden />
                      <span>{item.label}</span>
                      {item.id === "review" && pending > 0 && <span className="ml-auto rounded border bg-background px-1.5 text-xs tabular-nums">{pending}</span>}
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        {!SHARED_DEMO && <SidebarMenu><SidebarMenuItem><SidebarMenuButton asChild><NavLink to="/settings" data-nav="settings">{t("nav.settings")}</NavLink></SidebarMenuButton></SidebarMenuItem></SidebarMenu>}
        {!GATEWAY_ACCESS && <ServiceFooter />}
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function Crumbs() {
  const { pathname } = useLocation()
  const item = NAV.find((n) => isCurrent(n, pathname))
  const t = useT()
  return (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem className="hidden sm:inline-flex">
          <Link to="/" className="hover:text-foreground">AutoOntology</Link>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="hidden sm:inline-flex" />
        <BreadcrumbItem>
          <BreadcrumbPage>{item?.label ?? t("shell.notFound")}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
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
            {!SHARED_DEMO && <StatusBadge />}
            <LangToggle />
            <ThemeToggle />
          </div>
        </header>
        {GATEWAY_ACCESS && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-4 py-2 text-sm" role="note">
            <span>{t(SHARED_DEMO ? "shell.demo.hint" : "shell.remote.hint")}</span>
            <form action="/__demo/logout" method="post"><button className="underline underline-offset-4" type="submit">{t("shell.demo.logout")}</button></form>
          </div>
        )}
        <main id="main" className={cn("mx-auto w-full min-w-0 flex-1",
                                      fullBleed ? "p-0" : "max-w-6xl px-4 py-6 sm:px-6")}>
          <div className={fullBleed ? "px-4 pt-4" : undefined}><SectionNav /></div>
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
