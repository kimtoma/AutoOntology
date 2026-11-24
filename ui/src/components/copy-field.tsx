/* A value to copy (a URL, a token, an invitation link) with a copy button. */

import { Check, Copy } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { t } from "@/i18n"
import { cn } from "@/lib/utils"

export function CopyButton({ text, what, className }: { text: string; what: string; className?: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setDone(true)
      window.setTimeout(() => setDone(false), 1500)
    } catch {
      toast.error(t("connect.copyFailed"))
    }
  }
  return (
    <Button type="button" size="sm" variant="outline" className={cn("shrink-0", className)}
            aria-label={t("connect.copyLabel", { what })} onClick={copy}>
      {done ? <Check aria-hidden /> : <Copy aria-hidden />}
      <span aria-live="polite">{done ? t("connect.copied") : t("connect.copy")}</span>
    </Button>
  )
}

/** A labelled one-line value (URL, token) with a copy button; long values wrap. */
export function CopyField({ label, value, note, id }: { label: string; value: string; note?: string; id?: string }) {
  return (
    <div className="grid gap-1" data-field={id}>
      <p className="text-xs font-medium text-muted-foreground">
        {label}{note && <span className="font-normal"> · {note}</span>}
      </p>
      <div className="flex min-w-0 items-start gap-2">
        <code className="min-w-0 flex-1 rounded-md border bg-muted/40 px-2 py-1.5 font-mono text-xs break-all"
              id={id}>{value}</code>
        <CopyButton text={value} what={label} />
      </div>
    </div>
  )
}
