import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"
import { useT } from "@/i18n"

export function EmptyState({ icon: Icon, title, hint, action, className }: {
  icon?: LucideIcon
  title: string
  hint?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn(
      "flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center",
      className)}>
      {Icon && <Icon className="size-8 text-muted-foreground" aria-hidden />}
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-md text-sm text-muted-foreground">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const t = useT()
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div role="alert"
         className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
      <p className="font-medium text-destructive">{t("common.couldNotLoad")}</p>
      <p className="mt-1 break-words text-muted-foreground">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}
                className="mt-2 text-sm font-medium underline underline-offset-4">
          {t("common.tryAgain")}
        </button>
      )}
    </div>
  )
}
