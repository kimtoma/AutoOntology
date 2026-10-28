import { t, type MessageKey } from "@/i18n/core"
import {
  Activity, BookOpen, Database, Download, FlaskConical, Hammer, Network, Rocket, type LucideIcon,
} from "lucide-react"

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
  item("/", Rocket, "home"),
  item("/sources", Database, "sources"),
  item("/build", Hammer, "build"),
  item("/ontology", BookOpen, "ontology"),
  item("/graph", Network, "graph"),
  item("/playground", FlaskConical, "playground"),
  item("/exports", Download, "exports"),
  item("/status", Activity, "status"),
]

/** Whether *item* is the current page ("/" only on the home page itself). */
export const isCurrent = (item: NavItem, pathname: string) =>
  item.path === "/" ? pathname === "/" : pathname.startsWith(item.path)
