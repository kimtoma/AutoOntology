import { Lock } from "lucide-react"

import { useT, type MessageKey } from "@/i18n"
import { roleLabel, useCan, useRole, type InstallationPermission, type ProjectPermission } from "@/lib/auth"
import { useArchived } from "@/lib/project"

/** A line above a page whose actions the caller's role cannot use (accounts mode; an archived
    project has its own banner). */
export function RoleNotice({ permission, message }: {
  permission: ProjectPermission | InstallationPermission; message: MessageKey
}) {
  const t = useT()
  const can = useCan(permission)
  const role = useRole()
  const archived = useArchived()
  if (can || archived) return null
  return (
    <p role="note" id="role-notice" data-permission={permission}
       className="mb-4 flex items-start gap-2 rounded-lg border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
      <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{t(message, { role: roleLabel(role) })}</span>
    </p>
  )
}
