import {
  Activity, BookOpen, Database, Download, FlaskConical, Hammer, Network, type LucideIcon,
} from "lucide-react"

export interface NavItem { path: string; label: string; icon: LucideIcon; hint: string }

export const NAV: NavItem[] = [
  { path: "/sources", label: "Sources", icon: Database, hint: "Structured data and documents" },
  { path: "/build", label: "Build", icon: Hammer, hint: "One pipeline over every source" },
  { path: "/ontology", label: "Ontology", icon: BookOpen, hint: "The unified concept list" },
  { path: "/graph", label: "Graph", icon: Network, hint: "3D view of the ontology" },
  { path: "/playground", label: "Playground", icon: FlaskConical, hint: "Chat, SQL and SPARQL" },
  { path: "/exports", label: "Exports", icon: Download, hint: "Turtle, JSON-LD, Ossie, OKF" },
  { path: "/status", label: "Status", icon: Activity, hint: "Service health" },
]
