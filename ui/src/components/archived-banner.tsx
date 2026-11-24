import { Archive } from "lucide-react"

import { ProjectLink } from "@/components/project-link"
import { useT } from "@/i18n"
import { projectName, useProject } from "@/lib/project"

/** On every page of an archived project: read-only. */
export function ArchivedBanner() {
  const t = useT()
  const scope = useProject()
  if (!scope?.archived) return null
  return (
    <div role="status" id="archived-banner"
         className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-warning/40
                    bg-warning/10 px-4 py-3 text-sm">
      <Archive className="size-4 shrink-0 text-amber-700 dark:text-amber-300" aria-hidden />
      <span className="font-medium">{t("projects.banner.title", { name: projectName(scope.project, scope.slug) })}</span>
      <span className="text-muted-foreground">{t("projects.banner.body")}</span>
      <ProjectLink to="/projects" className="font-medium underline underline-offset-4">{t("projects.banner.manage")}</ProjectLink>
    </div>
  )
}
