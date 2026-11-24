/* `/users` (admins): every account with its state, last sign-in and projects; invite a user
   (the invitation link with a copy button, or a note that it was mailed), disable/enable,
   make or remove an admin (the last active admin stays: 409 last_admin), and create a
   sign-in or password-reset link to pass on. `/users/audit` is the whole audit log. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link2, Loader2, MoreHorizontal, ShieldCheck, ShieldOff, UserCheck, UserPlus, UserX, Users } from "lucide-react"
import { useState, type FormEvent } from "react"
import { NavLink } from "react-router"
import { toast } from "sonner"

import { CopyField } from "@/components/copy-field"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { NativeSelect } from "@/components/native-select"
import { PageHeader } from "@/components/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { fmtDate, useT } from "@/i18n"
import { authApi, authErrorMessage, roleLabel, useCan, useMe, type IssuedLink, type UserRow } from "@/lib/auth"
import { cn } from "@/lib/utils"

/** "Users | Audit log" above both admin pages. */
export function UsersTabs({ current }: { current: "users" | "audit" }) {
  const t = useT()
  const tab = (to: string, id: "users" | "audit", label: string) => (
    <NavLink to={to} end aria-current={current === id ? "page" : undefined} data-tab={id}
             className={cn("rounded-md px-3 py-1.5 text-sm font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                           current === id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
      {label}
    </NavLink>
  )
  return (
    <nav aria-label={t("users.title")} className="mb-4 inline-flex gap-1 rounded-lg bg-muted p-1" id="users-tabs">
      {tab("/users", "users", t("users.tab.users"))}
      {tab("/users/audit", "audit", t("users.tab.audit"))}
    </nav>
  )
}

function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [email, setEmail] = useState("")
  const [name, setName] = useState("")
  const [admin, setAdmin] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const invite = useMutation({
    mutationFn: () => authApi.users.invite({ email: email.trim(), name: name.trim() || undefined, is_admin: admin }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.users }),
    onError: (err) => setProblem(authErrorMessage(err)),
  })
  const close = () => {
    setEmail(""); setName(""); setAdmin(false); setProblem(null); invite.reset()
    onOpenChange(false)
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const form = e.currentTarget as HTMLFormElement
    if (!email.trim() || !form.checkValidity()) { setProblem(t("auth.error.invalid_email")); return }
    setProblem(null)
    invite.mutate()
  }
  const done = invite.data
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !invite.isPending) close() }}>
      <DialogContent className="sm:max-w-lg" id="invite-dialog">
        <DialogHeader>
          <DialogTitle>{t("users.invite.title")}</DialogTitle>
          <DialogDescription>{t("users.invite.description")}</DialogDescription>
        </DialogHeader>
        {done ? (
          <div className="grid gap-3" id="invite-result" role="status">
            {done.mail_sent
              ? <p className="text-sm">{t("users.invite.mailed", { email: done.user.email })}</p>
              : <p className="text-sm text-muted-foreground">{t("users.invite.notMailed", { email: done.user.email, expires: fmtDate(done.expires_at) })}</p>}
            <CopyField label={t("users.invite.label")} value={done.invite_link} id="invite-link" />
            <DialogFooter><Button onClick={close} id="btn-invite-done">{t("common.done")}</Button></DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="grid gap-4" id="invite-form">
            <div className="grid gap-2">
              <Label htmlFor="invite-email">{t("users.invite.email")}</Label>
              <Input id="invite-email" type="email" autoComplete="off" spellCheck={false} required autoFocus maxLength={320}
                     value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={problem ? true : undefined} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="invite-name">{t("users.invite.name")}</Label>
              <Input id="invite-name" autoComplete="off" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <label className="flex items-start gap-2 text-sm" htmlFor="invite-admin">
              <input id="invite-admin" type="checkbox" className="mt-0.5 size-4 accent-primary" checked={admin}
                     onChange={(e) => setAdmin(e.target.checked)} />
              <span>{t("users.invite.admin")}</span>
            </label>
            {problem && <p role="alert" className="text-sm text-destructive" id="invite-error">{problem}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close} disabled={invite.isPending}>{t("common.cancel")}</Button>
              <Button type="submit" disabled={invite.isPending} id="btn-invite-submit">
                {invite.isPending && <Loader2 className="animate-spin" aria-hidden />}
                {t("users.invite.submit")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

function LinkDialog({ user, onClose }: { user: UserRow | null; onClose: () => void }) {
  const t = useT()
  const [purpose, setPurpose] = useState<"magic" | "reset">("magic")
  const [issued, setIssued] = useState<IssuedLink | null>(null)
  const make = useMutation({
    mutationFn: () => authApi.users.link(user!.id, purpose),
    onSuccess: setIssued,
    onError: (err) => toast.error(t("users.failed"), { description: authErrorMessage(err) }),
  })
  const close = () => { setIssued(null); setPurpose("magic"); make.reset(); onClose() }
  return (
    <Dialog open={!!user} onOpenChange={(o) => { if (!o && !make.isPending) close() }}>
      <DialogContent className="sm:max-w-lg" id="link-dialog">
        <DialogHeader>
          <DialogTitle>{user && t("users.link.title", { email: user.email })}</DialogTitle>
          <DialogDescription>{t("users.link.description")}</DialogDescription>
        </DialogHeader>
        {issued ? (
          <div className="grid gap-3" role="status" id="link-result">
            <CopyField label={t(issued.purpose === "reset" ? "users.link.reset" : "users.link.magic")} value={issued.link}
                       note={t("users.link.expires", { expires: fmtDate(issued.expires_at) })} id="issued-link" />
            <DialogFooter><Button onClick={close}>{t("common.done")}</Button></DialogFooter>
          </div>
        ) : (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); make.mutate() }}>
            <div className="grid gap-2">
              <Label htmlFor="link-purpose">{t("users.link.purpose")}</Label>
              <NativeSelect id="link-purpose" value={purpose} onChange={(e) => setPurpose(e.target.value as "magic" | "reset")}>
                <option value="magic">{t("users.link.magic")}</option>
                <option value="reset">{t("users.link.reset")}</option>
              </NativeSelect>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close} disabled={make.isPending}>{t("common.cancel")}</Button>
              <Button type="submit" disabled={make.isPending} id="btn-link-create">
                {make.isPending && <Loader2 className="animate-spin" aria-hidden />}
                {t("users.link.create")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

const COLS = "md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_7rem_9.5rem_minmax(0,1.4fr)_3rem]"

function UserRowItem({ u, self, onLink }: { u: UserRow; self: boolean; onLink: (u: UserRow) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const update = useMutation({
    mutationFn: (body: { is_admin?: boolean; disabled?: boolean }) => authApi.users.update(u.id, body),
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: keys.users })
      toast.success(t("users.updated", { email: row.email }))
    },
    onError: (err) => toast.error(t("users.failed"), { description: authErrorMessage(err) }),
  })
  return (
    <li data-user={u.email} data-active={u.active} className={cn(
      "grid gap-2 rounded-lg border p-3 md:items-center md:gap-3 md:rounded-none md:border-0 md:border-t md:px-1 md:py-2", COLS,
      !u.active && "text-muted-foreground")}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="truncate font-medium" title={u.email}>{u.email}</span>
        {self && <Badge variant="secondary">{t("users.you")}</Badge>}
        {u.is_admin && <Badge variant="outline" className="border-primary/30 text-primary" data-badge="admin"><ShieldCheck aria-hidden /> {t("auth.role.admin")}</Badge>}
      </div>
      <span className="truncate text-sm">{u.name || "—"}</span>
      <span>
        <Badge variant={u.active ? "secondary" : "outline"} data-state={u.active ? "active" : "disabled"}>
          {!u.active ? t("users.disabled") : u.has_password ? t("users.active") : t("users.invited")}
        </Badge>
      </span>
      <span className="text-xs text-muted-foreground">
        <span className="md:sr-only">{t("users.col.lastLogin")}: </span>{u.last_login_at ? fmtDate(u.last_login_at) : t("users.never")}
      </span>
      <span className="flex min-w-0 flex-wrap gap-1">
        {u.memberships.length ? u.memberships.map((m) => (
          <Badge key={m.project_id} variant="outline" className="font-normal">
            <span className="font-mono">{m.slug}</span><span className="text-muted-foreground">· {roleLabel(m.role)}</span>
          </Badge>
        )) : <span className="text-xs text-muted-foreground">{u.is_admin ? "—" : t("users.noProjects")}</span>}
      </span>
      <div className="md:text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" aria-label={t("users.actions", { email: u.email })} data-user-actions={u.email}
                    disabled={update.isPending}>
              {update.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <MoreHorizontal aria-hidden />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => onLink(u)} disabled={!u.active} data-action="link">
              <Link2 aria-hidden /> {t("users.link")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => update.mutate({ is_admin: !u.is_admin })} data-action={u.is_admin ? "remove-admin" : "make-admin"}>
              {u.is_admin ? <ShieldOff aria-hidden /> : <ShieldCheck aria-hidden />}
              {u.is_admin ? t("users.removeAdmin") : t("users.makeAdmin")}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => update.mutate({ disabled: u.active })} data-action={u.active ? "disable" : "enable"}
                              variant={u.active ? "destructive" : "default"}>
              {u.active ? <UserX aria-hidden /> : <UserCheck aria-hidden />}
              {u.active ? t("users.disable") : t("users.enable")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  )
}

export default function UsersPage() {
  const t = useT()
  const me = useMe()
  const allowed = useCan("users.manage") && me.auth === "accounts"
  const users = useQuery({ queryKey: keys.users, queryFn: authApi.users.list, enabled: allowed })
  const [inviting, setInviting] = useState(false)
  const [linkFor, setLinkFor] = useState<UserRow | null>(null)
  if (!allowed) {
    return (
      <>
        <PageHeader title={t("users.title")} />
        <EmptyState icon={Users} title={t("users.denied")} />
      </>
    )
  }
  return (
    <>
      <PageHeader title={t("users.title")} description={t("users.description")}
                  actions={<Button onClick={() => setInviting(true)} id="btn-invite"><UserPlus aria-hidden /> {t("users.invite")}</Button>} />
      <UsersTabs current="users" />
      <Card className="gap-3 py-4" id="user-list">
        <CardContent className="px-4">
          {users.error ? <ErrorState error={users.error} onRetry={() => users.refetch()} />
            : users.isPending ? <Skeleton className="h-40" />
            : (
              <>
                <div aria-hidden className={cn("hidden gap-3 px-1 pb-2 text-xs text-muted-foreground md:grid", COLS)}>
                  <span>{t("users.col.email")}</span><span>{t("users.col.name")}</span><span>{t("users.col.state")}</span>
                  <span>{t("users.col.lastLogin")}</span><span>{t("users.col.projects")}</span>
                  <span className="text-right"><span className="sr-only">{t("users.col.actions")}</span></span>
                </div>
                <ul className="grid gap-2 md:gap-0" aria-label={t("users.title")}>
                  {users.data.map((u) => <UserRowItem key={u.id} u={u} self={u.id === me.user?.id} onLink={setLinkFor} />)}
                </ul>
              </>
            )}
        </CardContent>
      </Card>
      <InviteDialog open={inviting} onOpenChange={setInviting} />
      <LinkDialog user={linkFor} onClose={() => setLinkFor(null)} />
    </>
  )
}
