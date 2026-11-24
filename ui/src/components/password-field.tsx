/* Password inputs: one with a show/hide toggle, and the "new password + confirm" pair with
   the minimum length and a strength hint (the invitation page and the change dialog). */

import { Eye, EyeOff } from "lucide-react"
import { useState, type ComponentProps } from "react"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useT, type MessageKey } from "@/i18n"
import { PASSWORD_MIN, passwordStrength } from "@/lib/auth"
import { cn } from "@/lib/utils"

export function PasswordInput({ id, label, describedBy, className, ...props }: {
  id: string; label: string; describedBy?: string
} & Omit<ComponentProps<"input">, "type" | "id">) {
  const t = useT()
  const [shown, setShown] = useState(false)
  return (
    <div className={cn("grid gap-2", className)}>
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input id={id} type={shown ? "text" : "password"} className="pr-10" aria-describedby={describedBy}
               spellCheck={false} autoCapitalize="off" {...props} />
        <button type="button" onClick={() => setShown((s) => !s)} aria-pressed={shown}
                aria-label={t(shown ? "auth.password.hide" : "auth.password.show")} aria-controls={id}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-muted-foreground
                           outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50">
          {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
        </button>
      </div>
    </div>
  )
}

const BAR = ["bg-destructive", "bg-warning", "bg-sky-500", "bg-success"]

/** New password + confirmation. *showErrors*: after a submit attempt. */
export function NewPasswordFields({ idPrefix, value, confirm, onValue, onConfirm, showErrors, labels }: {
  idPrefix: string
  value: string
  confirm: string
  onValue: (v: string) => void
  onConfirm: (v: string) => void
  showErrors: boolean
  labels: { password: MessageKey; confirm: MessageKey }
}) {
  const t = useT()
  const strength = passwordStrength(value)
  const short = value.length < PASSWORD_MIN
  const mismatch = confirm.length > 0 && confirm !== value
  const hintId = `${idPrefix}-hint`
  const confirmErrId = `${idPrefix}-confirm-error`
  return (
    <>
      <div className="grid gap-2">
        <PasswordInput id={`${idPrefix}-password`} label={t(labels.password)} value={value}
                       onChange={(e) => onValue(e.target.value)} autoComplete="new-password" required
                       minLength={PASSWORD_MIN} maxLength={1024} describedBy={hintId}
                       aria-invalid={showErrors && short ? true : undefined} />
        <div id={hintId} className="grid gap-1.5" aria-live="polite">
          <div className="flex gap-1" aria-hidden>
            {[1, 2, 3].map((i) => (
              <span key={i} className={cn("h-1 flex-1 rounded-full bg-muted",
                                         value && strength >= i && BAR[strength])} />
            ))}
          </div>
          <p className={cn("text-xs", showErrors && short ? "text-destructive" : "text-muted-foreground")}>
            {t("auth.password.min", { n: PASSWORD_MIN })}
            {value && <> · {t("auth.password.strength", { level: t(`auth.password.strength.${strength}` as MessageKey) })}</>}
          </p>
        </div>
      </div>
      <div className="grid gap-2">
        <PasswordInput id={`${idPrefix}-confirm`} label={t(labels.confirm)} value={confirm}
                       onChange={(e) => onConfirm(e.target.value)} autoComplete="new-password" required
                       maxLength={1024} describedBy={mismatch || (showErrors && confirm !== value) ? confirmErrId : undefined}
                       aria-invalid={(mismatch || (showErrors && confirm !== value)) ? true : undefined} />
        {(mismatch || (showErrors && confirm !== value)) && (
          <p id={confirmErrId} className="text-xs text-destructive">{t("auth.password.mismatch")}</p>
        )}
      </div>
    </>
  )
}
