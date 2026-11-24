/* The project switcher at the top of the sidebar: the current project, the other projects
   (archived ones greyed under a separator), "New project" and "All projects". */

import { Archive, Check, ChevronsUpDown, FolderKanban, Plus } from "lucide-react"
import { useState } from "react"
import { useLocation, useNavigate } from "react-router"

import { NewProjectDialog } from "@/components/project-dialogs"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar"
import { useProjects } from "@/hooks/queries"
import { useT } from "@/i18n"
import type { Project } from "@/lib/api"
import { pagePathname, projectName, projectPath, useProject } from "@/lib/project"
import { cn } from "@/lib/utils"

/** The first letter of a project's name, as its avatar. */
function Avatar({ name, archived, className }: { name: string; archived?: boolean; className?: string }) {
  return (
    <span aria-hidden className={cn(
      "flex aspect-square size-8 shrink-0 items-center justify-center rounded-md text-sm font-semibold",
      archived ? "bg-muted text-muted-foreground" : "bg-sidebar-primary text-sidebar-primary-foreground", className)}>
      {name.trim().charAt(0).toUpperCase() || "◇"}
    </span>
  )
}

export function ProjectSwitcher() {
  const t = useT()
  const scope = useProject()
  const projects = useProjects()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()
  const [creating, setCreating] = useState(false)
  const list = projects.data?.projects ?? []
  const current = scope?.project ?? list.find((p) => p.slug === scope?.slug) ?? null
  const name = projectName(current, scope?.slug ?? "")
  const page = pagePathname(pathname)
  const go = (to: string) => { if (isMobile) setOpenMobile(false); navigate(to) }
  // the same page in the other project (the projects page itself opens the project's home)
  const open = (p: Project) => go(projectPath(p.slug, page.startsWith("/projects") ? "/" : page))
  const active = list.filter((p) => !p.archived)
  const archived = list.filter((p) => p.archived)
  const item = (p: Project) => (
    <DropdownMenuItem key={p.slug} onSelect={() => open(p)} data-project={p.slug}
                      className={cn(p.archived && "text-muted-foreground")}>
      <Avatar name={projectName(p)} archived={p.archived} className="size-6 text-xs" />
      <span className="min-w-0 flex-1 truncate">{projectName(p)}</span>
      {p.archived && <Archive className="size-3.5" aria-label={t("projects.archived")} />}
      {p.slug === scope?.slug && <Check className="size-4" aria-label={t("projects.switcher.current")} />}
    </DropdownMenuItem>
  )
  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton size="lg" id="project-switcher" tooltip={name || t("projects.switcher.label")}
                                 aria-label={t("projects.switcher.aria", { name })}
                                 className="data-[state=open]:bg-sidebar-accent">
                <Avatar name={name} archived={current?.archived} />
                <span className="grid min-w-0 flex-1 text-left leading-tight">
                  <span className="truncate font-semibold" id="project-switcher-name">{name || "…"}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {current?.archived ? t("projects.archived") : t("projects.switcher.label")}
                  </span>
                </span>
                <ChevronsUpDown className="ml-auto size-4" aria-hidden />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="w-(--radix-dropdown-menu-trigger-width) min-w-60" align="start"
                                 side={isMobile ? "bottom" : "right"} sideOffset={4} id="project-menu">
              <DropdownMenuLabel className="text-xs text-muted-foreground">{t("projects.switcher.heading")}</DropdownMenuLabel>
              <DropdownMenuGroup>{active.map(item)}</DropdownMenuGroup>
              {archived.length > 0 && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuLabel className="text-xs text-muted-foreground">{t("projects.switcher.archived")}</DropdownMenuLabel>
                  <DropdownMenuGroup>{archived.map(item)}</DropdownMenuGroup>
                </>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setCreating(true)} id="project-menu-new">
                <Plus aria-hidden /> {t("projects.new")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => go("/projects")} id="project-menu-all">
                <FolderKanban aria-hidden /> {t("projects.all")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>
      <NewProjectDialog open={creating} onOpenChange={setCreating} />
    </>
  )
}
