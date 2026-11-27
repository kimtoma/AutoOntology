/* Accounts: who is signed in and what they may do (`GET /api/auth/me`), the sign-in calls,
   and the admin/owner APIs (users, members, audit log). With `ONTOLOGY_AUTH=off` (the
   default) `me.auth` is "off" and every check here allows everything: the UI is the same as
   before accounts. The server stays the judge: a hidden button is a convenience, a refused
   request is 403 `forbidden` (see `setAuthErrorHandler` in api.ts). */

import { createContext, useContext } from "react"

import { t, type MessageKey } from "@/i18n/core"
import { ApiError, errorMessage, jsonInit, request } from "@/lib/api"
import { ProjectContext } from "@/lib/project"

export const ROLES = ["viewer", "reviewer", "editor", "owner"] as const
export type Role = (typeof ROLES)[number]

/** Installation-wide permissions (admins only); `INSTALLATION_PERMISSIONS` in web/projects.py. */
export const INSTALLATION_PERMISSIONS = ["project.create", "settings.manage", "users.manage", "audit.read_all"] as const
export type InstallationPermission = (typeof INSTALLATION_PERMISSIONS)[number]

export type ProjectPermission =
  | "project.read" | "exports.read" | "playground.ask" | "playground.manage" | "review.decide" | "review.apply"
  | "sources.write" | "runs.start" | "agent.tokens" | "project.manage" | "members.manage" | "audit.read"
  | "objects.read" | "objects.manage" | "logic.read" | "functions.call" | "logic.edit" | "logic.activate"
  | "action.run" | "action.approve" | "workflow.run"

export interface User {
  id: string
  email: string
  name: string
  is_admin: boolean
  created_at: string
  disabled_at: string | null
  last_login_at: string | null
  has_password: boolean
  active: boolean
}

export interface Membership {
  project_id: string
  slug: string
  name: string
  archived: boolean
  role: Role | string
  permissions: string[]
}

/** `GET /api/auth/me` (ontology/web/auth.py::AuthService.me). */
export interface Me {
  auth: "off" | "accounts"
  authenticated: boolean
  actor: string | null
  via: string | null
  user: User | null
  is_admin: boolean
  memberships: Membership[]
  permissions: Record<string, string[]>
  installation_permissions: string[]
  magic_link_enabled: boolean
  session_expires_at: string | null
}

/** The answer when accounts are off, or when the service has no `/api/auth/me` (older
    versions): no sign-in, everything allowed. */
export const OFF_ME: Me = {
  auth: "off", authenticated: true, actor: null, via: null, user: null, is_admin: true, memberships: [],
  permissions: {}, installation_permissions: [...INSTALLATION_PERMISSIONS], magic_link_enabled: false,
  session_expires_at: null,
}

export const AuthContext = createContext<Me | null>(null)

/** The signed-in state (null outside the auth gate: tests rendering a bare page = off). */
export const useMe = (): Me => useContext(AuthContext) ?? OFF_ME

/** Whether accounts are on (sign-in, roles, user menu). */
export const useAccounts = (): boolean => useMe().auth === "accounts"

const isInstallation = (p: string): p is InstallationPermission =>
  (INSTALLATION_PERMISSIONS as readonly string[]).includes(p)

/** Whether *me* has *permission* (in project *slug*, for a project permission). */
export function allows(me: Me, permission: string, slug?: string | null, projectPermissions?: string[]): boolean {
  if (me.auth !== "accounts") return true
  if (!me.authenticated) return false
  if (isInstallation(permission)) return me.installation_permissions.includes(permission)
  if (me.is_admin) return true
  const perms = projectPermissions ?? (slug ? me.permissions[slug] : undefined) ?? []
  return perms.includes(permission)
}

/** Whether the caller may do *permission* in the current project (or installation-wide). */
export function useCan(permission: ProjectPermission | InstallationPermission): boolean {
  const me = useMe()
  const scope = useContext(ProjectContext)
  return allows(me, permission, scope?.slug, scope?.project?.permissions)
}

/** The caller's role in the current project ("admin" for admins), null when off. */
export function useRole(): Role | "admin" | null {
  const me = useMe()
  const scope = useContext(ProjectContext)
  if (me.auth !== "accounts" || !me.authenticated) return null
  if (me.is_admin) return "admin"
  const role = scope?.project?.role ?? me.memberships.find((m) => m.slug === scope?.slug)?.role
  return (ROLES as readonly string[]).includes(role ?? "") ? role as Role : null
}

/** A role's name in the UI language. */
export const roleLabel = (role: string | null | undefined) =>
  role ? t(`auth.role.${role}` as MessageKey) : "—"

/** Why the caller cannot do *permission* here (a title for the disabled control), or
    undefined when they can. */
export function useDenied(permission: ProjectPermission | InstallationPermission): string | undefined {
  const can = useCan(permission)
  const role = useRole()
  if (can) return undefined
  return t("auth.denied", { role: roleLabel(role) })
}

/* --- API ---------------------------------------------------------------------------------- */

export interface Member extends User {
  role: Role | string
  member_since: string
}

export interface UserRow extends User {
  memberships: { project_id: string; slug: string; role: string }[]
}

export interface IssuedLink {
  email: string
  purpose: "magic" | "reset" | "invite"
  link: string
  expires_at: string
  mail_sent: boolean
}

export interface AuditEntry {
  id: number
  at: string
  user_id: string | null
  actor: string
  project_id: string | null
  project: string | null
  action: string
  target: string
  detail: Record<string, unknown>
  ip: string | null
}

export interface AuditPage { entries: AuditEntry[]; next_before_id: number | null }

export interface AuditQuery { project?: string | null; user?: string; action?: string; before_id?: number | null; limit?: number }

const enc = encodeURIComponent

/** `GET /api/auth/me`; a service without it (404) or without accounts answers "off". */
async function fetchMe(): Promise<Me> {
  try {
    const me = await request<Me>("/auth/me")
    return me && typeof me === "object" && me.auth === "accounts" ? me : { ...OFF_ME, ...me, auth: "off" }
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return OFF_ME
    throw err
  }
}

export const authApi = {
  me: fetchMe,
  login: (email: string, password: string) => request<Me>("/auth/login", jsonInit("POST", { email, password })),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  magicLink: (email: string) => request<{ ok: boolean }>("/auth/magic-link", jsonInit("POST", { email })),
  acceptInvite: (token: string, password: string) =>
    request<Me>("/auth/accept-invite", jsonInit("POST", { token, password })),
  changePassword: (current: string | null, next: string) =>
    request<{ ok: boolean; revoked_sessions: number }>(
      "/auth/password", jsonInit("POST", current === null ? { new: next } : { current, new: next })),
  users: {
    list: () => request<{ users: UserRow[] }>("/users").then((r) => r.users),
    invite: (body: { email: string; name?: string; is_admin?: boolean; send?: boolean }) =>
      request<{ user: User; invite_link: string; expires_at: string; mail_sent: boolean }>("/users", jsonInit("POST", body)),
    update: (id: string, body: { name?: string; is_admin?: boolean; disabled?: boolean }) =>
      request<User>(`/users/${enc(id)}`, jsonInit("PATCH", body)),
    link: (id: string, purpose: "magic" | "reset" | "invite", send = false) =>
      request<IssuedLink>(`/users/${enc(id)}/link`, jsonInit("POST", { purpose, send })),
  },
  members: {
    list: (slug: string) => request<{ members: Member[] }>(`/projects/${enc(slug)}/members`).then((r) => r.members),
    add: (slug: string, body: { email: string; role: string; send?: boolean }) =>
      request<{ member: Member; created: boolean; mail_sent: boolean; invite_link?: string; expires_at?: string }>(
        `/projects/${enc(slug)}/members`, jsonInit("POST", body)),
    setRole: (slug: string, userId: string, role: string) =>
      request<Member>(`/projects/${enc(slug)}/members/${enc(userId)}`, jsonInit("PATCH", { role })),
    remove: (slug: string, userId: string) =>
      request<{ removed: string }>(`/projects/${enc(slug)}/members/${enc(userId)}`, { method: "DELETE" }),
  },
  audit: (q: AuditQuery) => {
    const sp = new URLSearchParams()
    if (q.project) sp.set("project", q.project)
    if (q.user?.trim()) sp.set("user", q.user.trim())
    if (q.action?.trim()) sp.set("action", q.action.trim())
    if (q.before_id) sp.set("before_id", String(q.before_id))
    sp.set("limit", String(q.limit ?? 50))
    return request<AuditPage>(`/audit?${sp}`)
  },
}

/* --- messages ----------------------------------------------------------------------------- */

const AUTH_ERRORS = new Set([
  "invalid_credentials", "invalid_token", "weak_password", "wrong_password", "invalid_email", "user_exists",
  "member_exists", "last_owner", "last_admin", "not_a_member", "user_disabled", "project_required",
  "admin_required_for_new_user",
])

/** An accounts API error in the UI language (429 names the wait). */
export function authErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.code === "too_many_attempts") {
    return err.retryAfter ? t("auth.error.too_many_attempts.wait", { s: err.retryAfter }) : t("auth.error.too_many_attempts")
  }
  if (err instanceof ApiError && err.code && AUTH_ERRORS.has(err.code)) return t(`auth.error.${err.code}` as MessageKey)
  return errorMessage(err)
}

/** The minimum password length (`PASSWORD_MIN` in store/accounts.py). */
export const PASSWORD_MIN = 12

/** 0-3: a rough strength hint (length and character classes); never a gate past the minimum. */
export function passwordStrength(pw: string): 0 | 1 | 2 | 3 {
  if (pw.length < PASSWORD_MIN) return 0
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(pw)).length
  const score = (pw.length >= 16 ? 1 : 0) + (classes >= 3 ? 1 : 0) + 1
  return Math.min(3, score) as 1 | 2 | 3
}

/** A same-app path to return to after signing in (`?next=`), or "/" for anything else. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/"
  if (/^\/(login|invite)(\/|\?|#|$)/.test(next)) return "/"
  return next
}

/** Whether a new password pair can be sent. */
export const newPasswordOk = (value: string, confirm: string) => value.length >= PASSWORD_MIN && value === confirm

/** `{token, purpose}` of a `#token=...&purpose=...` fragment. */
export function readInviteHash(hash: string): { token: string; purpose: "invite" | "reset" } | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""))
  const token = params.get("token")?.trim() ?? ""
  if (!token || token.length > 200) return null
  return { token, purpose: params.get("purpose") === "reset" ? "reset" : "invite" }
}

/* --- audit log ---------------------------------------------------------------------------- */

/** The actions the service records (web/auth.py, web/accounts_api.py, web/api.py ...). */
export const AUDIT_ACTIONS = [
  "login_ok", "login_failed", "logout", "magic_link_sent", "password_set", "password_change", "password_change_failed",
  "user.invite", "user.update", "user.link", "member.add", "member.role", "member.remove",
  "token.create", "token.rotate", "token.revoke", "run.start", "run.cancel", "review.decide", "review.undo",
  "source.add", "source.delete", "samples.load", "settings.update", "settings.restart",
  "project.create", "project.rename", "project.archive", "project.unarchive",
  "object.bind", "logic.save", "logic.status", "logic.accept", "logic.reject",
  "action.submit", "action.approve", "action.reject", "action.apply", "action.revert", "action.fail",
  "workflow.run", "workflow.finish", "workflow.cancel", "skill.export",
]

export const AUDIT_PAGE_SIZE = 50

/** A short "key: value" summary of an entry's detail. */
export function detailSummary(detail: Record<string, unknown>): string {
  return Object.entries(detail ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ")
}
