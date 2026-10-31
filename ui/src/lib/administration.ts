import { request } from "@/lib/api"

export interface Domain { id: string; slug: string; name: string }
export interface Principal {
  id: string; username: string; name: string; kind: "human" | "agent"; status: string
  grants: { role: string; domain_id: string }[]
  delegation: null | { delegator_id: string; actions: string[]; domains: string[]; expires_at: number; revoked_at: number | null }
}
export interface Administration {
  workspace_id: string; policy_version: number; principals: Principal[]; domains: Domain[]
  sources: { id: string; name: string; domain_id: string | null; version: number | null }[]
  roles: Record<string, string[]>; delegatable_actions: string[]
}
export interface Session {
  id: string; principal_id: string; username: string; kind: string; status: string; current: boolean
  created_at: number; expires_at: number; revoked_at: number | null
}
const write = (path: string, method: string, body?: unknown) => request(path, { method, ...(body === undefined ? {} : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }) })
export const administrationApi = {
  packet: () => request<Administration>("/identity/management"),
  sessions: (all: boolean) => request<{ items: Session[] }>(`/identity/credentials${all ? "?principal_id=*" : ""}`),
  revokeSession: (id: string) => write(`/identity/credentials/${encodeURIComponent(id)}`, "DELETE"),
  add: (body: unknown) => write("/identity/principals", "POST", body),
  roles: (id: string, body: unknown) => write(`/identity/principals/${encodeURIComponent(id)}/roles`, "PUT", body),
  password: (id: string, password: string) => write(`/identity/principals/${encodeURIComponent(id)}/password`, "PUT", { password }),
  disable: (id: string) => write(`/identity/principals/${encodeURIComponent(id)}/disable`, "POST"),
  enable: (id: string) => write(`/identity/principals/${encodeURIComponent(id)}/enable`, "POST"),
  domain: (body: unknown) => write("/identity/domains", "POST", body),
  classify: (id: string, domain: string) => write(`/identity/sources/${encodeURIComponent(id)}/domain`, "PUT", { domain_id: domain }),
  delegate: (body: unknown) => request<{ token: string; credential_id: string; principal: { id: string; name: string }; expires_at: number }>("/identity/delegations", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  revokeAgent: (id: string) => write(`/identity/delegations/${encodeURIComponent(id)}`, "DELETE"),
  audit: () => request<{ verification: { ok: boolean; events: number }; items: { id: number; action: string; actor_id: string | null; target: string; created_at: number }[] }>("/identity/audit"),
}
