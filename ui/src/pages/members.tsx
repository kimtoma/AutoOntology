/* `/p/:project/members` (owners and admins): the project's members with their role, add one
   by email (admins only: an unknown email becomes a new account with an invitation link, shown
   with a copy button when mail is not set up; an owner who is not an admin adds existing users
   and gets 403 admin_required_for_new_user otherwise), change a role, remove a member (a
   project keeps an owner: 409 last_owner). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, ScrollText, Trash2, UserPlus, Users } from "lucide-react"
import { useRef, useState, type FormEvent } from "react"
import { toast } from "sonner"

import { CopyField } from "@/components/copy-field"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { NativeSelect } from "@/components/native-select"
import { PageHeader } from "@/components/page-header"
import { ProjectLink } from "@/components/project-link"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { fmtDate, useT, type MessageKey } from "@/i18n"
import { authApi, authErrorMessage, roleLabel, ROLES, useCan, useMe, type Member } from "@/lib/auth"
import { projectName, useProject } from "@/lib/project"

/** The role options with what each may do. */
function RoleOptions() {
  const t = useT()
  return <>{ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)} — {t(`auth.role.${r}.hint` as MessageKey)}</option>)}</>
}

interface Invite { email: string; link: string; expires: string }

function AddMember({ slug, onInvite }: { slug: string; onInvite: (i: Invite) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<string>("viewer")
  const [problem, setProblem] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const isAdmin = useMe().is_admin
  const add = useMutation({
    mutationFn: () => authApi.members.add(slug, { email: email.trim(), role }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: keys.members(slug) })
      const who = res.member.email
      setEmail("")
      setProblem(null)
      if (res.created && res.invite_link && !res.mail_sent) {
        onInvite({ email: who, link: res.invite_link, expires: res.expires_at ?? "" })
      } else {
        toast.success(t(res.created && res.mail_sent ? "members.added.mailed" : "members.added", { email: who }))
      }
      emailRef.current?.focus()
    },
    onError: (err) => setProblem(authErrorMessage(err)),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !emailRef.current?.checkValidity()) {
      setProblem(t("auth.error.invalid_email"))
      emailRef.current?.focus()
      return
    }
    add.mutate()
  }
  return (
    <Card className="gap-3 py-4" id="member-add">
      <CardHeader className="px-4">
        <CardTitle className="flex items-center gap-2 text-base"><UserPlus className="size-4" aria-hidden /> {t("members.add.title")}</CardTitle>
        <CardDescription>{t(isAdmin ? "members.add.description" : "members.add.description.owner")}</CardDescription>
      </CardHeader>
      <CardContent className="px-4">
        <form onSubmit={submit} noValidate className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_14rem_auto] sm:items-end">
          <div className="grid gap-2">
            <Label htmlFor="member-email">{t("members.add.email")}</Label>
            <Input ref={emailRef} id="member-email" type="email" autoComplete="off" spellCheck={false} value={email}
                   maxLength={320} required onChange={(e) => setEmail(e.target.value)}
                   aria-invalid={problem ? true : undefined} aria-describedby={problem ? "member-add-error" : undefined} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="member-role">{t("members.add.role")}</Label>
            <NativeSelect id="member-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <RoleOptions />
            </NativeSelect>
          </div>
          <Button type="submit" disabled={add.isPending} id="btn-member-add">
            {add.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <UserPlus aria-hidden />}
            {t("members.add.submit")}
          </Button>
        </form>
        {problem && <p id="member-add-error" role="alert" className="mt-2 text-sm text-destructive">{problem}</p>}
      </CardContent>
    </Card>
  )
}

function MemberRow({ m, slug, self, onRemove }: { m: Member; slug: string; self: boolean; onRemove: (m: Member) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const change = useMutation({
    mutationFn: (role: string) => authApi.members.setRole(slug, m.id, role),
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: keys.members(slug) })
      if (self) qc.invalidateQueries({ queryKey: keys.projects })
      toast.success(t("members.roleChanged", { email: row.email, role: roleLabel(row.role) }))
    },
    onError: (err) => toast.error(t("members.failed"), { description: authErrorMessage(err) }),
  })
  return (
    <li data-member={m.email} className="grid gap-2 rounded-lg border p-3 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_12rem_10.5rem_6.5rem]
                                          md:items-center md:gap-3 md:rounded-none md:border-0 md:border-t md:px-1 md:py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium" title={m.email}>{m.email}</span>
        {self && <Badge variant="secondary">{t("members.you")}</Badge>}
        {!m.active && <Badge variant="outline">{t("users.disabled")}</Badge>}
        {m.active && !m.has_password && <Badge variant="outline">{t("users.invited")}</Badge>}
      </div>
      <span className="truncate text-sm text-muted-foreground">{m.name || "—"}</span>
      <div>
        <Label htmlFor={`role-${m.id}`} className="sr-only">{t("members.roleLabel", { email: m.email })}</Label>
        <NativeSelect id={`role-${m.id}`} value={m.role} disabled={change.isPending} data-role-select={m.email}
                      onChange={(e) => change.mutate(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
        </NativeSelect>
      </div>
      <span className="text-sm text-muted-foreground"><span className="md:sr-only">{t("members.col.since")}: </span>{fmtDate(m.member_since)}</span>
      <div className="md:text-right">
        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" data-action="remove"
                aria-label={t("members.remove.aria", { email: m.email })} onClick={() => onRemove(m)}>
          <Trash2 aria-hidden /> {t("members.remove")}
        </Button>
      </div>
    </li>
  )
}

export default function MembersPage() {
  const t = useT()
  const qc = useQueryClient()
  const scope = useProject()
  const me = useMe()
  const slug = scope?.slug ?? ""
  const allowed = useCan("members.manage")
  const canAudit = useCan("audit.read")
  const members = useQuery({ queryKey: keys.members(slug), queryFn: () => authApi.members.list(slug), enabled: allowed && !!slug })
  const [invite, setInvite] = useState<Invite | null>(null)
  const [confirm, setConfirm] = useState<Member | null>(null)
  const remove = useMutation({
    mutationFn: (m: Member) => authApi.members.remove(slug, m.id),
    onSuccess: (_r, m) => {
      qc.invalidateQueries({ queryKey: keys.members(slug) })
      setConfirm(null)
      toast.success(t("members.removed", { email: m.email }))
    },
    onError: (err) => {
      setConfirm(null)
      toast.error(t("members.failed"), { description: authErrorMessage(err) })
    },
  })
  const name = projectName(scope?.project, slug)
  if (!allowed) {
    return (
      <>
        <PageHeader title={t("members.title")} />
        <EmptyState icon={Users} title={t("members.denied")} />
      </>
    )
  }
  return (
    <>
      <PageHeader title={t("members.title")} description={t("members.description", { name })}
                  actions={canAudit && (
                    <Button variant="outline" asChild>
                      <ProjectLink to="/audit" id="link-project-audit"><ScrollText aria-hidden /> {t("members.audit")}</ProjectLink>
                    </Button>
                  )} />
      <div className="grid min-w-0 gap-4">
        <AddMember slug={slug} onInvite={setInvite} />
        {invite && (
          <Card className="gap-3 border-primary/30 bg-primary/5 py-4" id="member-invite" role="status">
            <CardHeader className="px-4">
              <CardTitle className="text-base">{t("members.invite.title", { email: invite.email })}</CardTitle>
              <CardDescription>{t("members.invite.hint", { email: invite.email, expires: fmtDate(invite.expires) })}</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 px-4">
              <CopyField label={t("members.invite.label")} value={invite.link} id="member-invite-link" />
              <div><Button variant="outline" size="sm" onClick={() => setInvite(null)}>{t("common.done")}</Button></div>
            </CardContent>
          </Card>
        )}
        <Card className="gap-3 py-4" id="member-list">
          <CardContent className="px-4">
            {members.error ? <ErrorState error={members.error} onRetry={() => members.refetch()} />
              : members.isPending ? <Skeleton className="h-32" />
              : !members.data.length ? <EmptyState icon={Users} title={t("members.empty")} />
              : (
                <>
                  <div aria-hidden className="hidden gap-3 px-1 pb-2 text-xs text-muted-foreground
                                              md:grid md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_12rem_10.5rem_6.5rem]">
                    <span>{t("members.col.email")}</span><span>{t("members.col.name")}</span>
                    <span>{t("members.col.role")}</span><span>{t("members.col.since")}</span>
                    <span className="text-right">{t("members.col.actions")}</span>
                  </div>
                  <ul className="grid gap-2 md:gap-0" aria-label={t("members.title")}>
                    {members.data.map((m) => (
                      <MemberRow key={m.id} m={m} slug={slug} self={m.id === me.user?.id} onRemove={setConfirm} />
                    ))}
                  </ul>
                </>
              )}
          </CardContent>
        </Card>
      </div>
      <AlertDialog open={!!confirm} onOpenChange={(o) => { if (!o && !remove.isPending) setConfirm(null) }}>
        <AlertDialogContent id="member-remove-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm && t("members.remove.title", { email: confirm.email })}</AlertDialogTitle>
            <AlertDialogDescription>{t("members.remove.body")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>{t("common.cancel")}</AlertDialogCancel>
            <Button variant="destructive" disabled={remove.isPending} id="btn-member-remove"
                    onClick={() => confirm && remove.mutate(confirm)}>
              {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {t("members.remove")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
