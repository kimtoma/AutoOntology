import { Link, useLocation } from "react-router"
import { Button } from "@/components/ui/button"
import { useT, type MessageKey } from "@/i18n"
/** Existing deep links remain valid while navigation follows the operational task. */
export function SectionNav() {
  const { pathname } = useLocation(),
    t = useT()
  const groups: [string, string, MessageKey][][] = [
    [
      ["/ontology", "/ontology", "ops.concepts"],
      ["/graph", "/graph", "ops.graph"],
    ],
    [
      ["/playground", "/playground", "ops.usage"],
      ["/exports", "/exports", "ops.exports"],
    ],
    [
      ["/sources", "/sources", "nav.sources"],
      ["/build", "/build", "ops.analyze"],
    ],
    [
      ["/settings", "/settings", "ops.models"],
      ["/status", "/status", "ops.service"],
    ],
  ]
  const group = groups.find((g) => g.some(([prefix]) => pathname.startsWith(prefix)))
  if (!group) return null
  return (
    <nav aria-label={t("shell.mainMenu")} className="mb-5 flex flex-wrap gap-2">
      {group.map(([prefix, path, label]) => (
        <Button key={path} asChild variant={pathname.startsWith(prefix) ? "secondary" : "ghost"} size="sm">
          <Link to={path}>{t(label)}</Link>
        </Button>
      ))}
    </nav>
  )
}
