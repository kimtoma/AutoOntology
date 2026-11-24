import { useEffect, useState } from "react"
import { FolderKanban, FolderOpen } from "lucide-react"
import { useLocation, useNavigate } from "react-router"

import { SETUP, useNav } from "@/components/nav"
import { useProjects } from "@/hooks/queries"
import { useT } from "@/i18n"
import { pagePathname, projectName, projectPath, useProject, useProjectPath } from "@/lib/project"
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command"

/** ⌘K / Ctrl+K: jump to a page, switch projects or search the ontology's concepts. */
export function CommandMenu() {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const navigate = useNavigate()
  const path = useProjectPath()
  const scope = useProject()
  const projects = useProjects()
  const page = pagePathname(useLocation().pathname)
  const nav = useNav()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [])
  const goTo = (to: string) => { setOpen(false); setText(""); navigate(to) }
  const go = (to: string) => goTo(path(to))
  const others = (projects.data?.projects ?? []).filter((p) => p.slug !== scope?.slug)
  return (
    <CommandDialog open={open} onOpenChange={setOpen} title={t("cmd.title")}
                   description={t("cmd.description")}>
      <CommandInput placeholder={t("cmd.placeholder")} value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>{t("cmd.empty")}</CommandEmpty>
        {text.trim() && (
          <CommandGroup heading={t("cmd.concepts")}>
            <CommandItem value={`concept-search ${text}`}
                         onSelect={() => go(`/ontology?q=${encodeURIComponent(text.trim())}`)}>
              {t("cmd.search", { text: text.trim() })}
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading={t("cmd.pages")}>
          {nav.map((n) => (
            <CommandItem key={n.path} value={`${n.label} ${n.hint}`} onSelect={() => go(n.path)}>
              <n.icon aria-hidden /> {n.label}
              <span className="ml-auto text-xs text-muted-foreground">{n.hint}</span>
            </CommandItem>
          ))}
          <CommandItem value={`${SETUP.label} ${SETUP.hint} /setup`} onSelect={() => go(SETUP.path)} data-cmd="setup">
            <SETUP.icon aria-hidden /> {t("cmd.setup")}
            <span className="ml-auto text-xs text-muted-foreground">/setup</span>
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading={t("cmd.projects")}>
          {others.map((p) => (
            <CommandItem key={p.slug} value={`project ${p.slug} ${projectName(p)}`} data-cmd-project={p.slug}
                         onSelect={() => goTo(projectPath(p.slug, page.startsWith("/projects") ? "/" : page))}>
              <FolderOpen aria-hidden /> {t("cmd.switchProject", { name: projectName(p) })}
              {p.archived && <span className="ml-auto text-xs text-muted-foreground">{t("projects.archived")}</span>}
            </CommandItem>
          ))}
          <CommandItem value={`${t("projects.all")} /projects`} onSelect={() => goTo("/projects")} data-cmd="projects">
            <FolderKanban aria-hidden /> {t("projects.all")}
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
