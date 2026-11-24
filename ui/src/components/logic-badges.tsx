import { Bot, CalendarClock, Terminal, User } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { useT, type MessageKey } from "@/i18n"
import { cn } from "@/lib/utils"

const TONE: Record<string, string> = {
  active: "border-success/30 bg-success/10 text-success",
  applied: "border-success/30 bg-success/10 text-success",
  succeeded: "border-success/30 bg-success/10 text-success",
  approved: "border-success/30 bg-success/10 text-success",
  draft: "border-border bg-muted text-muted-foreground",
  proposed: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  running: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  pending_approval: "border-warning/40 bg-warning/10 text-amber-700 dark:text-amber-300",
  paused: "border-warning/40 bg-warning/10 text-amber-700 dark:text-amber-300",
  retired: "border-border bg-muted text-muted-foreground",
  reverted: "border-border bg-muted text-muted-foreground",
  cancelled: "border-border bg-muted text-muted-foreground",
  previewed: "border-border bg-muted text-muted-foreground",
  broken: "border-destructive/30 bg-destructive/10 text-destructive",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  rejected: "border-destructive/30 bg-destructive/10 text-destructive",
}

/** The status of a definition, an object type, an action run or a workflow run. */
export function StateBadge({ status, title, className }: { status: string; title?: string; className?: string }) {
  const t = useT()
  const key = `logic.state.${status}` as MessageKey
  const label = t(key)
  return (
    <Badge variant="outline" data-state={status} title={title}
           className={cn("font-normal", TONE[status] ?? TONE.draft, className)}>
      {label === key ? status : label}
    </Badge>
  )
}

/** Who asked for an action run: a person (UI / CLI), an agent token or a workflow. */
export function ViaBadge({ via, who }: { via: string; who?: string | null }) {
  const t = useT()
  const Icon = via === "agent" ? Bot : via === "workflow" ? CalendarClock : via === "cli" ? Terminal : User
  const key = `logic.via.${via}` as MessageKey
  const label = t(key)
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs" data-via={via}>
      <Badge variant="secondary" className="gap-1 font-normal"><Icon aria-hidden />{label === key ? via : label}</Badge>
      {who && <span className="truncate text-muted-foreground">{who}</span>}
    </span>
  )
}

/** function / action / workflow */
export function KindBadge({ kind }: { kind: string }) {
  const t = useT()
  const key = `logic.kind.${kind}` as MessageKey
  const label = t(key)
  return <Badge variant="outline" className="font-normal" data-kind={kind}>{label === key ? kind : label}</Badge>
}
