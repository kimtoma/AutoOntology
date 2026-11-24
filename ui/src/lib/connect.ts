/* The Connect page's endpoints and ready-to-copy client settings (MCP and REST for outside
   agents, web/agent_api.py). Pure functions: the page fills them with the real token right
   after creating or rotating one, and with TOKEN_PLACEHOLDER everywhere else. */

import type { AgentToken, AgentTools, TokenScope } from "@/lib/api"

export const TOKEN_PLACEHOLDER = "<TOKEN>"
/** The server name agents see (`claude mcp add ... autoontology`). */
export const SERVER_NAME = "autoontology"

/** *server*: the MCP server name the snippets suggest (`autoontology-<project slug>`). */
export interface Endpoints { mcp: string; rest: string; openapi: string; server?: string }

/** The suggested MCP server name of project *slug*: agents can add one per project. */
export const serverName = (slug?: string | null) => (slug ? `${SERVER_NAME}-${slug}` : SERVER_NAME)

/** Absolute URLs of the agent surfaces, from the page's own origin (the paths are the same for
    every project: a token reads the project it was created in). */
export function endpoints(origin: string, tools: Pick<AgentTools, "mcp_path" | "rest_base">,
                          project?: string | null): Endpoints {
  const base = origin.replace(/\/+$/, "")
  const rest = base + tools.rest_base
  return { mcp: base + tools.mcp_path, rest, openapi: `${rest}/openapi.json`, server: serverName(project) }
}

export type SnippetId = "claudeCode" | "desktop" | "curl"
export const SNIPPETS: SnippetId[] = ["claudeCode", "desktop", "curl"]

const SPARQL_EXAMPLE = "SELECT ?class ?label WHERE { ?class a owl:Class ; rdfs:label ?label } LIMIT 5"

/** The client setting *id* for *token* (*query*: the search word of the curl example). */
export function snippet(id: SnippetId, ep: Endpoints, token: string, query = "contract"): string {
  const auth = `Authorization: Bearer ${token}`
  const server = ep.server ?? SERVER_NAME
  if (id === "claudeCode") {
    return `claude mcp add --transport http ${server} ${ep.mcp} --header "${auth}"`
  }
  if (id === "desktop") {
    return JSON.stringify(
      { mcpServers: { [server]: { type: "http", url: ep.mcp, headers: { Authorization: `Bearer ${token}` } } } },
      null, 2)
  }
  const body = JSON.stringify({ query: SPARQL_EXAMPLE })
  return [
    `curl -H "${auth}" "${ep.rest}/concepts?q=${query}&limit=5"`,
    "",
    `curl -X POST "${ep.rest}/sparql" \\`,
    `  -H "${auth}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '${body}'`,
  ].join("\n")
}

/** The parameter names of a tool's input schema, required ones first. */
export function toolParams(schema: { properties?: Record<string, unknown>; required?: string[] }) {
  const required = new Set(schema.required ?? [])
  return Object.keys(schema.properties ?? {})
    .map((name) => ({ name, required: required.has(name) }))
    .sort((a, b) => Number(b.required) - Number(a.required))
}

/** The broadest scope of a token: read, propose or execute (older services: read). */
export function tokenScope(token: Pick<AgentToken, "scopes">): TokenScope {
  const scopes = token.scopes ?? []
  return scopes.includes("execute") ? "execute" : scopes.includes("propose") ? "propose" : "read"
}

/** The actions a token is limited to (its `action:<name>` scopes); empty = no limit. */
export const tokenActions = (token: Pick<AgentToken, "scopes">) =>
  (token.scopes ?? []).filter((s) => s.startsWith("action:")).map((s) => s.slice(7))

