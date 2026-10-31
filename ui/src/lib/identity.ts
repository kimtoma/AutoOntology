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

export const identityApi = {
  status: (signal?: AbortSignal) => request<{ configured: boolean; workspace_id: string | null }>("/identity/status", { signal }),
  me: (signal?: AbortSignal) => request<IdentitySession>("/identity/me", { signal }),
  login: (username: string, password: string) => request("/identity/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password, client: "browser" }) }),
  logout: () => request("/identity/logout", { method: "POST" }),
}

export const IdentityContext = createContext<{ session: IdentitySession; signOut: () => void; signingOut: boolean; logoutFailed: boolean } | null>(null)
export const useIdentity = () => useContext(IdentityContext)
export const useCanUse = (capability: string) => {
  const identity = useIdentity()
  return !identity || (identity.session.capabilities[capability] ?? []).includes("*")
}
