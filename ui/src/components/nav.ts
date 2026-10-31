import { t, type MessageKey } from "@/i18n/core"
import { DEMO_PAGES, SHARED_DEMO } from "@/lib/demo"
import {
  BookOpen, Database, Download, FlaskConical, History, Inbox, LayoutDashboard, Settings, type LucideIcon,
} from "lucide-react"
import { canUse, type IdentitySession } from "@/lib/identity"

export interface NavItem { path: string; label: string; icon: LucideIcon; hint: string; id: string }

/** One sidebar entry; its label and hint are read in the current language. */
function item(path: string, icon: LucideIcon, id: string): NavItem {
  return {
    path, icon, id,
    get label() { return t(`nav.${id}` as MessageKey) },
    get hint() { return t(`nav.${id}.hint` as MessageKey) },
  }
}

export const NAV: NavItem[] = [
  item("/", LayoutDashboard, "home"),
  item("/review", Inbox, "review"),
  item("/sources", Database, "sources"),
  item("/ontology", BookOpen, "ontology"),
  item("/playground", FlaskConical, "playground"),
  item("/history", History, "history"),
  item("/settings", Settings, "settings"),
].filter((entry) => !SHARED_DEMO || DEMO_PAGES.has(entry.path))

const authority: Record<string, string> = { home: "candidate.read", review: "candidate.read", sources: "source.read", ontology: "knowledge.read", playground: "query.run", history: "candidate.read", settings: "settings.read" }
export function navigationFor(session?: IdentitySession) {
  return NAV.flatMap(entry => {
    if (entry.id === "playground" && !canUse(session, "query.run") && canUse(session, "exports.read")) {
      return [{ id: "exports", path: "/exports", icon: Download, get label() { return t("ops.exports") }, get hint() { return t("identity.scope.exports") } }]
    }
    return authority[entry.id] && canUse(session, authority[entry.id]) ? [entry] : []
  })
}

/** Whether *item* is the current page ("/" only on the home page itself). */
export const isCurrent = (item: NavItem, pathname: string) =>
  item.path === "/" ? pathname === "/"
    : item.id === "ontology" ? pathname.startsWith("/ontology") || pathname.startsWith("/graph")
    : item.id === "playground" ? pathname.startsWith("/playground") || pathname.startsWith("/exports")
    : item.id === "sources" ? pathname.startsWith("/sources") || pathname.startsWith("/build")
    : item.id === "settings" ? ["/settings", "/status", "/setup"].some(p => pathname.startsWith(p))
    : pathname.startsWith(item.path)
