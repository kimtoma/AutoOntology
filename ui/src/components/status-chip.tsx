import { CheckCircle2, CircleDashed, Loader2, MinusCircle, XCircle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { STATUS_KO } from "@/lib/labels"
import { cn } from "@/lib/utils"

const TONE: Record<string, string> = {
  succeeded: "border-success/30 bg-success/10 text-success",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  running: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  cancelled: "border-warning/40 bg-warning/10 text-amber-700 dark:text-amber-300",
  pending: "border-border bg-muted text-muted-foreground",
  skipped: "border-border bg-muted text-muted-foreground",
}

const ICON: Record<string, typeof CheckCircle2> = {
  succeeded: CheckCircle2,
  failed: XCircle,
  running: Loader2,
  cancelled: MinusCircle,
  pending: CircleDashed,
  skipped: MinusCircle,
}

/** Run / step status as a colored chip (label defaults to the Korean status name). */
export function StatusChip({ status, label, className }: {
  status: string | null | undefined
  label?: string
  className?: string
}) {
  const key = status || "pending"
  const Icon = ICON[key] ?? CircleDashed
  return (
    <Badge variant="outline" data-status={key} className={cn("font-normal", TONE[key], className)}>
      <Icon className={cn(key === "running" && "animate-spin")} aria-hidden />
      {label ?? STATUS_KO[key] ?? key}
    </Badge>
  )
}

const CHECK_TONE: Record<string, string> = {
  ok: "succeeded",
  warn: "cancelled",
  error: "failed",
}

/** ok / warn / error (status checks, providers). */
export function CheckChip({ status, label }: { status: string; label: string }) {
  return <StatusChip status={CHECK_TONE[status] ?? "pending"} label={label} />
}
