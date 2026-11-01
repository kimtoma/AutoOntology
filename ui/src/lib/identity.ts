import { createContext, useContext } from "react"
import { request } from "@/lib/api"

export interface IdentitySession {
  principal: { id: string; username: string; name: string; kind: "human" | "agent"; status: string }
  workspace: { workspace_id: string; workspace_name: string; organization_id: string }
  credential_id: string
  expires_at: number
  policy_version: number
  grants: { role: string; domain_id: string }[]
  capabilities: Record<string, string[]>
}

export interface RestorationState { catalog_ready: boolean; source_authority_retired: boolean }

export const identityApi = {
  status: (signal?: AbortSignal) => request<{ configured: boolean; workspace_id: string | null; restoration?: RestorationState }>("/identity/status", { signal }),
  me: (signal?: AbortSignal) => request<IdentitySession>("/identity/me", { signal }),
  login: (username: string, password: string) => request("/identity/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password, client: "browser" }) }),
  logout: () => request("/identity/logout", { method: "POST" }),
}

export const IdentityContext = createContext<{ session: IdentitySession; restoration?: RestorationState; signOut: () => void; signingOut: boolean; logoutFailed: boolean } | null>(null)
export const useIdentity = () => useContext(IdentityContext)
export const canUse = (session: IdentitySession | null | undefined, capability: string) => {
  const scopes = session?.capabilities[capability] ?? []
  const projected = ["knowledge.read", "originals.read", "exports.read", "query.run", "results.read", "results.delete"].includes(capability)
  return !session || scopes.includes("*") || (projected && scopes.length > 0)
}
export const useCanUse = (capability: string) => canUse(useIdentity()?.session, capability)
