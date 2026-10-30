import { Link, useLocation } from "react-router"
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
    <nav aria-label={t("shell.mainMenu")} className="console-section-nav">
      {group.map(([prefix, path, label]) => (
        <Link key={path} to={path} aria-current={pathname.startsWith(prefix) ? "page" : undefined}>
          {t(label)}
        </Link>
      ))}
    </nav>
  )
}
