import { useState, type FormEvent, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { administrationApi as api, type Administration, type Principal } from "@/lib/administration"
import { ApiError } from "@/lib/api"
import { AUTHORITY_CHANGED, IDENTITY_REQUIRED } from "@/lib/auth-events"
import { useCanUse, useIdentity } from "@/lib/identity"
import { useT, type MessageKey } from "@/i18n"

type Pending = { description: string; run: () => Promise<unknown>; locksOwnSession?: boolean }
type Form = "person" | "domain" | "agent" | { type: "roles" | "password"; person: Principal } | null
const date = (value: number) => new Date(value * 1000).toLocaleString()

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return <div className="grid gap-2"><Label htmlFor={id}>{label}</Label>{children}</div>
}

function Scope({ data, value, onChange }: { data: Administration; value: string[]; onChange: (value: string[]) => void }) {
  const t = useT()
  return <Field id="access-scope" label={t("access.scope")}><select id="access-scope" className="min-h-28 rounded border p-2 text-sm" multiple required value={value} onChange={e => { const chosen = Array.from(e.target.selectedOptions, o => o.value); onChange(chosen.includes("*") && chosen.length > 1 ? chosen.filter(v => v !== "*") : chosen) }}><option value="*">{t("access.all")}</option>{data.domains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select><p className="text-xs text-muted-foreground">{t("access.scopedHint")}</p></Field>
}

function Editor({ form, data, prepare, cancel, setSecret }: { form: Exclude<Form, null>; data: Administration; prepare: (p: Pending) => void; cancel: () => void; setSecret: (secret: string) => void }) {
  const t = useT()
  const person = typeof form === "object" ? form.person : null
  const type = typeof form === "object" ? form.type : form
  const [username, setUsername] = useState("")
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [roles, setRoles] = useState(person?.grants.map(g => g.role).filter((r, i, a) => a.indexOf(r) === i) ?? ["viewer"])
  const [domains, setDomains] = useState(person?.grants.map(g => g.domain_id).filter((d, i, a) => a.indexOf(d) === i) ?? ["*"])
  const [actions, setActions] = useState(["candidate.read", "candidate.prepare"].filter(a => data.delegatable_actions.includes(a)))
  const [ttl, setTtl] = useState(3600)
  const domainNames = domains.map(id => data.domains.find(d => d.id === id)?.name ?? (id === "*" ? t("access.all") : id)).join(", ")
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const roleNames = roles.map(r => t(`access.role.${r}` as MessageKey)).join(", ")
    if (type === "person") prepare({ description: `${name} (${username}) · ${roleNames} · ${domainNames}`, run: () => api.add({ username, name, password, roles, domains }) })
    if (type === "roles" && person) prepare({ description: `${person.name} (${person.username}) · ${roleNames} · ${domainNames}`, run: () => api.roles(person.id, { roles, domains }) })
    if (type === "password" && person) prepare({ description: `${person.name} · ${t("access.resetEffect")}`, run: () => api.password(person.id, password) })
    if (type === "domain") prepare({ description: `${name} (${username})`, run: () => api.domain({ slug: username, name }) })
    if (type === "agent") prepare({ description: `${name} (${username}) · ${actions.join(", ")} · ${domainNames} · ${ttl}s`, run: async () => { const issued = await api.delegate({ username, name, actions, domains, ttl }); setSecret(issued.token) } })
  }
  return <form onSubmit={submit} className="my-5 grid max-w-2xl gap-4 border-y py-5" aria-label={t("access.review")}>
    {(type === "person" || type === "agent" || type === "domain") && <><Field id="access-username" label={t(type === "domain" ? "access.domainSlug" : "access.username")}><Input id="access-username" required minLength={2} maxLength={64} pattern="[a-z][a-z0-9_-]+" autoComplete="off" value={username} onChange={e => setUsername(e.target.value)} /></Field><Field id="access-name" label={t(type === "domain" ? "access.domainName" : "access.name")}><Input id="access-name" required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field></>}
    {(type === "person" || type === "password") && <Field id="access-password" label={t(type === "person" ? "access.password" : "access.newPassword")}><Input id="access-password" type="password" autoComplete="new-password" required minLength={15} maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /><p className="text-xs text-muted-foreground">{t("access.passwordHint")}</p></Field>}
    {(type === "person" || type === "roles") && <Field id="access-roles" label={t("access.role")}><select id="access-roles" multiple required className="min-h-32 rounded border p-2 text-sm" value={roles} onChange={e => setRoles(Array.from(e.target.selectedOptions, o => o.value))}>{Object.keys(data.roles).map(role => <option key={role} value={role}>{t(`access.role.${role}` as MessageKey)}</option>)}</select></Field>}
    {(type === "person" || type === "roles" || type === "agent") && <Scope data={data} value={domains} onChange={setDomains} />}
    {type === "agent" && <><p className="text-sm text-muted-foreground">{t("access.agentHint")}</p><Field id="access-actions" label={t("access.actions")}><select id="access-actions" multiple required className="min-h-40 rounded border p-2 text-sm" value={actions} onChange={e => setActions(Array.from(e.target.selectedOptions, o => o.value))}>{data.delegatable_actions.map(a => <option key={a} value={a}>{a}</option>)}</select></Field><Field id="access-ttl" label={t("access.ttl")}><Input id="access-ttl" type="number" min={1} max={604800} required value={ttl} onChange={e => setTtl(Number(e.target.value))} /></Field></>}
    <div className="flex gap-3"><Button type="submit">{t("access.review")}</Button><Button type="button" variant="outline" onClick={cancel}>{t("access.cancel")}</Button></div>
  </form>
}

export default function AccessPage() {
  const identity = useIdentity(), t = useT(), client = useQueryClient()
  const admin = useCanUse("identity.admin") && !!identity
  const [tab, setTab] = useState<"people" | "agents" | "domains" | "sessions" | "audit">("sessions")
  const [form, setForm] = useState<Form>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [completed, setCompleted] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [allSessions, setAllSessions] = useState(false)
  const [classification, setClassification] = useState({ source: "", domain: "" })
  const packet = useQuery({ queryKey: ["access", "management"], queryFn: api.packet, enabled: admin })
  const sessions = useQuery({ queryKey: ["access", "sessions", allSessions], queryFn: () => api.sessions(admin && allSessions), enabled: !!identity })
  const audit = useQuery({ queryKey: ["access", "audit"], queryFn: api.audit, enabled: admin && tab === "audit" })
  const mutation = useMutation({ mutationFn: async () => { if (pending) await pending.run() }, retry: false, onSuccess: () => {
    const lock = pending?.locksOwnSession
    setPending(null); setForm(null); setCompleted(true)
    void client.invalidateQueries({ queryKey: ["access"] })
    window.dispatchEvent(new Event(lock ? IDENTITY_REQUIRED : AUTHORITY_CHANGED))
  } })
  const prepare = (action: Pending) => { mutation.reset(); setCompleted(false); setPending(action); setForm(null) }
  const error = mutation.error instanceof ApiError && ["last_admin", "last_administrator"].includes(mutation.error.code ?? "") ? t("access.lastAdmin") : mutation.error instanceof ApiError && ["username_exists", "domain_exists"].includes(mutation.error.code ?? "") ? t("access.exists") : t("access.failed")
  const stateLabel = (state: string) => t(`access.${state}` as MessageKey)
  if (!identity) return <div className="space-y-5"><PageHeader title={t("access.title")} /><p>{t("access.unconfigured")}</p></div>
  const data = packet.data
  return <div className="space-y-6">
    <PageHeader title={t("access.title")} description={t("access.hint")} actions={<Button variant="outline" onClick={() => { void client.invalidateQueries({ queryKey: ["access"] }); window.dispatchEvent(new Event(AUTHORITY_CHANGED)) }}>{t("access.reload")}</Button>} />
    <div className="border-y py-3 text-sm"><strong>{identity.session.principal.name}</strong><span className="ml-3 text-muted-foreground">{identity.session.workspace.workspace_name}</span></div>
    <nav className="flex flex-wrap gap-5 border-b" aria-label={t("access.title")}>{(admin ? ["people", "agents", "domains", "sessions", "audit"] as const : ["sessions"] as const).map(item => <button type="button" key={item} aria-current={tab === item ? "page" : undefined} className={`border-b-2 py-3 text-sm ${tab === item ? "border-foreground" : "border-transparent text-muted-foreground"}`} onClick={() => { setTab(item); setForm(null); setCompleted(false) }}>{t(`access.${item}` as MessageKey)}</button>)}</nav>
    {completed && <p role="status" className="text-sm">{t("access.done")}</p>}
    {(packet.isError || sessions.isError || (tab === "audit" && audit.isError)) && <p role="alert" className="text-sm text-destructive">{t("access.loadFailed")}</p>}
    {((admin && packet.isPending) || sessions.isPending) && <p role="status">{t("access.loading")}</p>}
    {tab === "sessions" && <section className="space-y-4">{admin && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allSessions} onChange={e => setAllSessions(e.target.checked)} />{t("access.allSessions")}</label>}<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-xs text-muted-foreground"><th className="py-3">{t("access.username")}</th><th>{t("access.state")}</th><th>{t("access.created")}</th><th>{t("access.expires")}</th><th /></tr></thead><tbody>{sessions.data?.items.map(s => <tr key={s.id} className="border-b"><td className="py-4 pr-4">{s.username}<p className="text-xs text-muted-foreground">{s.current ? t("access.current") : s.id.slice(0, 12)}</p></td><td className="pr-4">{stateLabel(s.status)}</td><td className="pr-4 text-xs">{date(s.created_at)}</td><td className="pr-4 text-xs">{date(s.expires_at)}</td><td>{s.status === "active" && <Button size="sm" variant="outline" onClick={() => prepare({ description: `${s.username} · ${s.current ? t("access.current") : s.id} · ${t("access.sessionEffect")}`, run: () => api.revokeSession(s.id), locksOwnSession: s.current })}>{t("access.revokeSession")}</Button>}</td></tr>)}</tbody></table></div>{sessions.data?.items.length === 0 && <p>{t("access.none")}</p>}</section>}
    {admin && data && <>
      {form && <Editor key={typeof form === "string" ? form : `${form.type}-${form.person.id}`} form={form} data={data} prepare={p => prepare({ ...p, locksOwnSession: typeof form === "object" && form.type === "password" && form.person.id === identity.session.principal.id })} cancel={() => setForm(null)} setSecret={setSecret} />}
      {tab === "people" && <section className="space-y-4"><Button onClick={() => setForm("person")}>{t("access.add")}</Button>{data.principals.filter(p => p.kind === "human").map(p => <article key={p.id} className="flex flex-wrap items-start justify-between gap-4 border-b py-5"><div><h2 className="font-medium">{p.name}</h2><p className="text-sm text-muted-foreground">{p.username} · {stateLabel(p.status)}</p><p className="mt-2 text-xs">{p.grants.map(g => `${t(`access.role.${g.role}` as MessageKey)} / ${g.domain_id === "*" ? t("access.all") : data.domains.find(d => d.id === g.domain_id)?.name ?? g.domain_id}`).join(" · ")}</p></div>{p.status === "active" && <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setForm({ type: "roles", person: p })}>{t("access.editRoles", { name: p.name })}</Button><Button variant="outline" size="sm" onClick={() => setForm({ type: "password", person: p })}>{t("access.reset", { name: p.name })}</Button><Button variant="outline" size="sm" onClick={() => prepare({ description: `${p.name} · ${t("access.disableEffect")}`, run: () => api.disable(p.id), locksOwnSession: p.id === identity.session.principal.id })}>{t("access.disable", { name: p.name })}</Button></div>}{p.status === "disabled" && <Button variant="outline" size="sm" onClick={() => prepare({ description: `${p.name} · ${t("access.enableEffect")}`, run: () => api.enable(p.id) })}>{t("access.enable", { name: p.name })}</Button>}</article>)}</section>}
      {tab === "agents" && <section className="space-y-4"><p className="text-sm text-muted-foreground">{t("access.agentHint")}</p><Button onClick={() => setForm("agent")}>{t("access.addAgent")}</Button>{data.principals.filter(p => p.kind === "agent").map(p => <article key={p.id} className="flex flex-wrap justify-between gap-4 border-b py-5"><div><h2 className="font-medium">{p.name}</h2><p className="text-sm text-muted-foreground">{p.username} · {p.delegation?.revoked_at ? t("access.revoked") : p.status === "disabled" ? t("access.disabled") : date(p.delegation?.expires_at ?? 0)}</p><p className="mt-2 text-xs">{p.delegation?.actions.join(" · ")}</p><p className="text-xs text-muted-foreground">{p.delegation?.domains.map(id => id === "*" ? t("access.all") : data.domains.find(d => d.id === id)?.name ?? id).join(" · ")}</p></div>{p.delegation?.revoked_at == null && <Button variant="outline" onClick={() => prepare({ description: `${p.name} · ${t("access.agentEffect")}`, run: () => api.revokeAgent(p.id) })}>{t("access.revokeAgent", { name: p.name })}</Button>}</article>)}</section>}
      {tab === "domains" && <section className="space-y-5"><Button onClick={() => setForm("domain")}>{t("access.addDomain")}</Button>{data.domains.map(d => <div key={d.id} className="flex gap-6 border-b py-3 text-sm"><strong>{d.name}</strong><span className="text-muted-foreground">{d.slug}</span></div>)}<form className="grid max-w-xl gap-4 border-y py-5" onSubmit={event => { event.preventDefault(); prepare({ description: `${data.sources.find(s => s.id === classification.source)?.name} → ${data.domains.find(d => d.id === classification.domain)?.name} · ${t("access.sourceEffect")}`, run: () => api.classify(classification.source, classification.domain) }) }}><Field id="access-source" label={t("access.source")}><select id="access-source" required className="rounded border p-2 text-sm" value={classification.source} onChange={e => setClassification({ ...classification, source: e.target.value })}><option value="">—</option>{data.sources.map(s => <option key={s.id} value={s.id}>{s.name} · {data.domains.find(d => d.id === s.domain_id)?.name ?? "—"}</option>)}</select></Field><Field id="access-domain" label={t("access.domainName")}><select id="access-domain" required className="rounded border p-2 text-sm" value={classification.domain} onChange={e => setClassification({ ...classification, domain: e.target.value })}><option value="">—</option>{data.domains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field><Button type="submit" disabled={!data.sources.length}>{t("access.classify")}</Button></form></section>}
      {tab === "audit" && audit.data && <section><p className={`mb-4 text-sm ${audit.data.verification.ok ? "" : "text-destructive"}`}>{t(audit.data.verification.ok ? "access.auditVerified" : "access.auditFailed")} · {audit.data.verification.events}</p><div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b"><th className="py-3">{t("access.created")}</th><th>{t("access.action")}</th><th>{t("access.actor")}</th><th>{t("access.target")}</th></tr></thead><tbody>{audit.data.items.slice(-100).reverse().map(e => <tr key={e.id} className="border-b"><td className="py-3 pr-4">{date(e.created_at)}</td><td className="pr-4">{e.action}</td><td className="pr-4">{data.principals.find(p => p.id === e.actor_id)?.name ?? "—"}</td><td className="max-w-56 truncate">{e.target}</td></tr>)}</tbody></table></div></section>}
    </>}
    <AlertDialog open={!!pending} onOpenChange={open => { if (!open && !mutation.isPending) { setPending(null); mutation.reset() } }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t("access.confirmTitle")}</AlertDialogTitle><AlertDialogDescription>{t("access.confirmHint")}</AlertDialogDescription></AlertDialogHeader><p className="break-words text-sm">{pending?.description}</p>{mutation.isError && <p role="alert" className="text-sm text-destructive">{error}</p>}{mutation.isPending && <p role="status" className="text-sm">{t("access.working")}</p>}<AlertDialogFooter><AlertDialogCancel disabled={mutation.isPending}>{t("access.cancel")}</AlertDialogCancel><Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>{t(mutation.isPending ? "access.working" : "access.confirm")}</Button></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={!!secret} onOpenChange={open => { if (!open) { setSecret(null); setRevealed(false) } }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{t("access.secretTitle")}</AlertDialogTitle><AlertDialogDescription>{t("access.secretHint")}</AlertDialogDescription></AlertDialogHeader><Input aria-label={t("access.secret")} type={revealed ? "text" : "password"} readOnly autoComplete="off" value={secret ?? ""} /><Button variant="outline" onClick={() => setRevealed(!revealed)}>{t(revealed ? "access.hide" : "access.reveal")}</Button><AlertDialogFooter><AlertDialogCancel>{t("access.closeSecret")}</AlertDialogCancel></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
