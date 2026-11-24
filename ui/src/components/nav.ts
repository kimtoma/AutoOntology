import { t, type MessageKey } from "@/i18n/core"
import {
  Activity, BookOpen, Boxes, Database, Download, FlaskConical, Hammer, Inbox, LayoutDashboard, Network, Plug, Rocket,
  ScrollText, Settings, UserCog, Users, Workflow, type LucideIcon,
} from "lucide-react"

import { allows, useMe, type InstallationPermission, type ProjectPermission } from "@/lib/auth"
import { useProject } from "@/lib/project"

export interface NavItem {
  path: string
  label: string
  icon: LucideIcon
  hint: string
  id: string
  /** shown only to callers with this permission (accounts mode) */
  permission?: ProjectPermission | InstallationPermission
}

/** One sidebar entry; its label and hint are read in the current language. */
function item(path: string, icon: LucideIcon, id: string, permission?: NavItem["permission"]): NavItem {
  return {
    path, icon, id, permission,
    get label() { return t(`nav.${id}` as MessageKey) },
    get hint() { return t(`nav.${id}.hint` as MessageKey) },
  }
}

export const NAV: NavItem[] = [
  item("/", LayoutDashboard, "overview"),
  item("/review", Inbox, "review"),
  item("/sources", Database, "sources"),
  item("/build", Hammer, "build"),
  item("/ontology", BookOpen, "ontology"),
  item("/graph", Network, "graph"),
  item("/objects", Boxes, "objects", "objects.read"),
  item("/logic", Workflow, "logic", "logic.read"),
  item("/playground", FlaskConical, "playground"),
  item("/exports", Download, "exports"),
  item("/connect", Plug, "connect"),
  item("/status", Activity, "status"),
  item("/settings", Settings, "settings", "settings.manage"),
]

/** Installation-wide, accounts mode only (no project prefix): accounts, invitations and the
    whole audit log; listed under Settings for admins. */
export const USERS: NavItem = item("/users", UserCog, "users", "users.manage")

/** Not in the sidebar: the first-run checklist (Settings and the command menu link it; `/`
    shows it by itself while nothing is built). */
export const SETUP: NavItem = item("/setup", Rocket, "setup")
/** Project pages for owners (the project switcher and the projects page link them). */
export const MEMBERS: NavItem = item("/members", Users, "members", "members.manage")
export const AUDIT: NavItem = item("/audit", ScrollText, "audit", "audit.read")

/** Pages outside the sidebar, for the breadcrumb. */
export const OTHER_PAGES: NavItem[] = [SETUP, MEMBERS, AUDIT, USERS]

/** Whether *item* is the current page ("/" only on the home page itself). */
export const isCurrent = (item: NavItem, pathname: string) =>
  item.path === "/" ? pathname === "/" : pathname.startsWith(item.path)

/** The sidebar entries the caller may use (accounts off: NAV as it is). */
export function useNav(): NavItem[] {
  const me = useMe()
  const scope = useProject()
  const items = NAV.filter((n) => !n.permission || allows(me, n.permission, scope?.slug, scope?.project?.permissions))
  return me.auth === "accounts" && allows(me, "users.manage") ? [...items, USERS] : items
}
