import {
  Activity, BookOpen, Database, Download, FlaskConical, Network, PlayCircle, Settings,
  type LucideIcon,
} from "lucide-react"

export interface NavItem { path: string; label: string; icon: LucideIcon }

export const NAV: NavItem[] = [
  { path: "/sources", label: "소스", icon: Database },
  { path: "/runs", label: "실행", icon: PlayCircle },
  { path: "/concepts", label: "개념", icon: BookOpen },
  { path: "/graph", label: "그래프", icon: Network },
  { path: "/playground", label: "플레이그라운드", icon: FlaskConical },
  { path: "/exports", label: "내보내기", icon: Download },
  { path: "/settings", label: "설정", icon: Settings },
  { path: "/status", label: "상태", icon: Activity },
]
