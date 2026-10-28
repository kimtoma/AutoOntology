import {
  Activity, BookOpen, Database, Download, FlaskConical, Hammer, Network, Rocket, type LucideIcon,
} from "lucide-react"

export interface NavItem { path: string; label: string; icon: LucideIcon; hint: string; id: string }

export const NAV: NavItem[] = [
  { path: "/", label: "Get started", icon: Rocket, hint: "Five steps from data to ontology", id: "home" },
  { path: "/sources", label: "Sources", icon: Database, hint: "Structured data and documents", id: "sources" },
  { path: "/build", label: "Build", icon: Hammer, hint: "One pipeline over every source", id: "build" },
  { path: "/ontology", label: "Ontology", icon: BookOpen, hint: "The unified concept list", id: "ontology" },
  { path: "/graph", label: "Graph", icon: Network, hint: "3D view of the ontology", id: "graph" },
  { path: "/playground", label: "Playground", icon: FlaskConical, hint: "Chat, SQL and SPARQL", id: "playground" },
  { path: "/exports", label: "Exports", icon: Download, hint: "Turtle, JSON-LD, Ossie, OKF", id: "exports" },
  { path: "/status", label: "Status", icon: Activity, hint: "Service health", id: "status" },
]

/** Whether *item* is the current page ("/" only on the home page itself). */
export const isCurrent = (item: NavItem, pathname: string) =>
  item.path === "/" ? pathname === "/" : pathname.startsWith(item.path)
