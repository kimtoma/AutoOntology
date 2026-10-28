import { Link, NavLink, Outlet, useLocation } from "react-router"

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
import { NAV } from "@/components/nav"
import { useStatus } from "@/hooks/queries"
import { cn } from "@/lib/utils"

function StatusBadge() {
  const { data, isError, isPending } = useStatus()
  const tone = isError ? "error" : data?.status ?? "pending"
  const label = isPending ? "확인 중…" : isError ? "서비스 응답 없음"
    : `${{ ok: "정상", warn: "주의", error: "문제 있음" }[data.status] ?? data.status}`
  const dot: Record<string, string> = {
    ok: "bg-success", warn: "bg-warning", error: "bg-destructive", pending: "bg-muted-foreground",
  }
  return (
    <Link to="/status" id="status-badge" data-status={tone}
          title="서비스 상태"
          aria-label={`서비스 상태: ${label}`}
          className="inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium
                     outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <span className={cn("size-2 rounded-full", dot[tone])} aria-hidden />
      <span>{label}</span>
      {data && <span className="hidden text-muted-foreground sm:inline">· {data.chat_provider}</span>}
    </Link>
  )
}

function AppSidebar() {
  const { pathname } = useLocation()
  const { setOpenMobile, isMobile } = useSidebar()
  return (
    <Sidebar collapsible="icon" aria-label="주 메뉴">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="AutoOntology">
              <Link to="/sources" onClick={() => isMobile && setOpenMobile(false)}>
                <span className="flex aspect-square size-8 items-center justify-center rounded-md
                                 bg-sidebar-primary text-sidebar-primary-foreground" aria-hidden>
                  ◇
                </span>
                <span className="grid flex-1 text-left leading-tight">
                  <span className="font-semibold">AutoOntology</span>
                  <span className="truncate text-xs text-muted-foreground">
                    데이터와 문서를 하나의 온톨로지로
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
              {NAV.map((item) => (
                <SidebarMenuItem key={item.path}>
                  <SidebarMenuButton asChild tooltip={item.label}
                                     isActive={pathname.startsWith(item.path)}>
                    <NavLink to={item.path} data-nav={item.path.slice(1)}
                             onClick={() => isMobile && setOpenMobile(false)}>
                      <item.icon aria-hidden />
                      <span>{item.label}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          127.0.0.1:7800 · 로컬 전용
        </p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function Crumbs() {
  const { pathname } = useLocation()
  const item = NAV.find((n) => pathname.startsWith(n.path))
  return (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem className="hidden sm:inline-flex">
          <Link to="/sources" className="hover:text-foreground">AutoOntology</Link>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="hidden sm:inline-flex" />
        <BreadcrumbItem>
          <BreadcrumbPage>{item?.label ?? "페이지 없음"}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const fullBleed = pathname.startsWith("/graph")  // the graph canvas fills the area under the header
  const wide = typeof window === "undefined" || window.innerWidth >= 1024
  return (
    <SidebarProvider defaultOpen={wide}>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b
                           bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" aria-label="메뉴 열기·닫기" />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <Crumbs />
          <div className="ml-auto flex items-center gap-1">
            <StatusBadge />
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className={cn("mx-auto w-full min-w-0 flex-1",
                                      fullBleed ? "p-0" : "max-w-6xl px-4 py-6 sm:px-6")}>
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
