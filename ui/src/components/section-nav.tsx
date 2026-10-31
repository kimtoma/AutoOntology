import { Link, useLocation } from "react-router"
import { useT, type MessageKey } from "@/i18n"
import { CanUse } from "@/components/identity-gate"
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
  const authority: Record<string, string> = { "/ontology": "knowledge.read", "/graph": "knowledge.read", "/playground": "query.run", "/exports": "exports.read", "/sources": "source.read", "/build": "candidate.prepare", "/settings": "settings.read", "/status": "settings.read" }
  if (!group) return null
  return (
    <nav aria-label={t("shell.mainMenu")} className="console-section-nav">
      {group.map(([prefix, path, label]) => (
        <CanUse key={path} capability={authority[path]}><Link to={path} aria-current={pathname.startsWith(prefix) ? "page" : undefined}>
          {t(label)}
        </Link></CanUse>
      ))}
    </nav>
  )
}
