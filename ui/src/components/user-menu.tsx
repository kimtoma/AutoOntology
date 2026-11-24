/* The header's account menu (accounts mode only): name and email, the role in the current
   project, an admin badge, "Change password", "Users" (admins) and "Sign out". */

import { useMutation } from "@tanstack/react-query"
import { KeyRound, Loader2, LogOut, ShieldCheck, Users } from "lucide-react"
import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { useSignedOut } from "@/hooks/use-auth"
import { NewPasswordFields, PasswordInput } from "@/components/password-field"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useT } from "@/i18n"
import { authApi, authErrorMessage, newPasswordOk, roleLabel, useMe, useRole } from "@/lib/auth"
import { FormError } from "@/pages/login"

function initials(name: string, email: string): string {
  const base = name.trim() || email
  return base.charAt(0).toUpperCase() || "?"
}

export function ChangePasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT()
  const me = useMe()
  const needsCurrent = me.user?.has_password ?? true
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [tried, setTried] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const reset = () => { setCurrent(""); setNext(""); setConfirm(""); setTried(false); setProblem(null) }
  const change = useMutation({
    mutationFn: () => authApi.changePassword(needsCurrent ? current : null, next),
    onSuccess: () => {
      toast.success(t("auth.changePassword.done"))
      reset()
      onOpenChange(false)
    },
    onError: (err) => setProblem(authErrorMessage(err)),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if ((needsCurrent && !current) || !newPasswordOk(next, confirm)) return
    setProblem(null)
    change.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!change.isPending) { if (!o) reset(); onOpenChange(o) } }}>
      <DialogContent className="sm:max-w-md" id="password-dialog">
        <DialogHeader>
          <DialogTitle>{t("auth.changePassword.title")}</DialogTitle>
          <DialogDescription>{t("auth.changePassword.description")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate id="password-form" method="post">
          <input type="text" name="username" autoComplete="username" hidden readOnly value={me.user?.email ?? ""} />
          {needsCurrent && (
            <PasswordInput id="password-current" label={t("auth.changePassword.current")} value={current}
                           onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required
                           maxLength={1024} autoFocus aria-invalid={tried && !current ? true : undefined} />
          )}
          <NewPasswordFields idPrefix="password-new" value={next} confirm={confirm} onValue={setNext}
                             onConfirm={setConfirm} showErrors={tried}
                             labels={{ password: "auth.changePassword.new", confirm: "auth.changePassword.confirm" }} />
          <FormError id="password-error" message={problem} />
          <DialogFooter>
            <Button type="button" variant="outline" disabled={change.isPending} onClick={() => { reset(); onOpenChange(false) }}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={change.isPending} id="btn-password-save">
              {change.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {t("auth.changePassword.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function UserMenu() {
  const t = useT()
  const me = useMe()
  const role = useRole()
  const navigate = useNavigate()
  const signedOut = useSignedOut()
  const [changing, setChanging] = useState(false)
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      navigate("/login", { replace: true })
      signedOut()
      toast.success(t("auth.logout.done"))
    },
    onError: (err) => toast.error(t("auth.logout.failed"), { description: authErrorMessage(err) }),
  })
  if (me.auth !== "accounts" || !me.user) return null
  const { user } = me
  const display = user.name.trim() || user.email
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" id="user-menu" className="gap-2 px-1.5"
                  aria-label={t("auth.menu.label", { name: display })}>
            <span aria-hidden className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
              {initials(user.name, user.email)}
            </span>
            {me.is_admin && <ShieldCheck className="hidden size-4 text-primary sm:block" aria-hidden />}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64" id="user-menu-content">
          <DropdownMenuLabel className="grid gap-1 font-normal">
            <span className="flex items-center gap-2">
              <span className="truncate font-medium" id="user-menu-name">{display}</span>
              {me.is_admin && <Badge variant="secondary" id="user-menu-admin"><ShieldCheck aria-hidden /> {t("auth.menu.admin")}</Badge>}
            </span>
            {user.name.trim() && <span className="truncate text-xs text-muted-foreground" id="user-menu-email">{user.email}</span>}
            {role && role !== "admin" && (
              <span className="text-xs text-muted-foreground" id="user-menu-role" data-role={role}>
                {t("auth.menu.role", { role: roleLabel(role) })}
              </span>
            )}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setChanging(true)} id="user-menu-password">
            <KeyRound aria-hidden /> {t("auth.menu.password")}
          </DropdownMenuItem>
          {me.is_admin && (
            <DropdownMenuItem onSelect={() => navigate("/users")} id="user-menu-users">
              <Users aria-hidden /> {t("auth.menu.users")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => logout.mutate()} disabled={logout.isPending} id="user-menu-logout">
            <LogOut aria-hidden /> {t("auth.menu.logout")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePasswordDialog open={changing} onOpenChange={setChanging} />
    </>
  )
}
