/* The Settings page's form model: what the form holds, the change it sends (only what differs
   from GET /api/settings), which keys the shell locks, and the wait for a restart. */

import {
  api, ApiError,
  type ChatProviderName, type EmbedProviderName, type RestartState, type SettingsChange, type SettingsState,
} from "@/lib/api"

export const CHAT_PROVIDERS: ChatProviderName[] = ["ollama", "claude", "codex", "openai"]
export const EMBED_PROVIDERS: EmbedProviderName[] = ["ollama", "openai"]

export const PROVIDER_VAR = "AUTOONTOLOGY_CHAT_PROVIDER"
export const EMBED_PROVIDER_VAR = "AUTOONTOLOGY_EMBED_PROVIDER"
export const BASE_URL_VAR = "AUTOONTOLOGY_OPENAI_BASE_URL"
export const KEY_VAR = "AUTOONTOLOGY_OPENAI_API_KEY"
export const CHAT_MODEL_VAR: Record<ChatProviderName, string> = {
  ollama: "AUTOONTOLOGY_OLLAMA_CHAT_MODEL",
  openai: "AUTOONTOLOGY_OPENAI_CHAT_MODEL",
  claude: "AUTOONTOLOGY_CLAUDE_MODEL",
  codex: "AUTOONTOLOGY_CODEX_MODEL",
}
export const EMBED_MODEL_VAR: Record<EmbedProviderName, string> = {
  ollama: "AUTOONTOLOGY_OLLAMA_EMBED_MODEL",
  openai: "AUTOONTOLOGY_OPENAI_EMBED_MODEL",
}

export interface SettingsForm {
  chatProvider: ChatProviderName
  chatModels: Record<ChatProviderName, string>
  embedProvider: EmbedProviderName
  embedModels: Record<EmbedProviderName, string>
  baseUrl: string
  /** a new key typed in this session ("" = keep the saved one) */
  apiKey: string
  clearKey: boolean
}

export const savedChatModels = (s: SettingsState): Record<ChatProviderName, string> => ({
  ollama: s.ollama.chat_model,
  openai: s.openai.chat_model,
  claude: s.cli.claude.model,
  codex: s.cli.codex.model,
})

export const savedEmbedModels = (s: SettingsState): Record<EmbedProviderName, string> => ({
  ollama: s.ollama.embed_model,
  openai: s.openai.embed_model,
})

export function formFrom(s: SettingsState): SettingsForm {
  return {
    chatProvider: s.chat.provider,
    chatModels: savedChatModels(s),
    embedProvider: s.embed.provider,
    embedModels: savedEmbedModels(s),
    baseUrl: s.openai.base_url,
    apiKey: "",
    clearKey: false,
  }
}

/** The body for test/PUT: only the fields that differ from the saved settings. */
export function changeOf(s: SettingsState, f: SettingsForm): SettingsChange {
  const out: SettingsChange = {}
  if (f.chatProvider !== s.chat.provider) out.chat_provider = f.chatProvider
  const chatModel = f.chatModels[f.chatProvider].trim()
  if (chatModel !== savedChatModels(s)[f.chatProvider]) out.chat_model = chatModel
  if (f.embedProvider !== s.embed.provider) out.embed_provider = f.embedProvider
  const embedModel = f.embedModels[f.embedProvider].trim()
  if (embedModel !== savedEmbedModels(s)[f.embedProvider]) out.embed_model = embedModel
  const url = f.baseUrl.trim().replace(/\/+$/, "")
  if (url !== s.openai.base_url) out.openai_base_url = url
  if (f.apiKey.trim()) out.api_key = f.apiKey.trim()
  else if (f.clearKey && s.openai.api_key_set) out.clear_api_key = true
  return out
}

export const hasChanges = (c: SettingsChange) => Object.keys(c).length > 0

export const usesOpenai = (f: SettingsForm) => f.chatProvider === "openai" || f.embedProvider === "openai"

export const isLocked = (s: SettingsState, key: string) => s.locked_keys.includes(key)

/** `scheme://host:port` of *url* ("" when it is not a URL): a saved key belongs to one. */
export function urlOrigin(url: string): string {
  try {
    const u = new URL(url.trim())
    const port = u.port || (u.protocol === "https:" ? "443" : u.protocol === "http:" ? "80" : "")
    return `${u.protocol}//${u.hostname.toLowerCase()}:${port}`
  } catch {
    return ""
  }
}

/** The saved key cannot be used with the form's base URL (another endpoint): the server
    refuses it (400 key_required) until the key for that endpoint is typed. */
export const savedKeyElsewhere = (s: SettingsState, f: SettingsForm) =>
  s.openai.api_key_set && !f.clearKey && !f.apiKey.trim()
  && urlOrigin(f.baseUrl) !== urlOrigin(s.openai.base_url)

export type ProviderState = "ready" | "notInstalled" | "notLoggedIn" | "unavailable" | "needsSetup"

/** The badge of a provider card, from the saved state (and the form for the openai fields). */
export function providerState(s: SettingsState, name: ChatProviderName, f?: SettingsForm): ProviderState {
  if (name === "claude" || name === "codex") {
    const cli = s.cli[name]
    return !cli.found ? "notInstalled" : !cli.logged_in ? "notLoggedIn" : "ready"
  }
  if (name === "ollama") return s.ollama.reachable ? "ready" : "unavailable"
  const url = f ? f.baseUrl.trim() : s.openai.base_url
  const key = f
    ? (!!f.apiKey.trim() || (s.openai.api_key_set && !f.clearKey && !savedKeyElsewhere(s, f)))
    : s.openai.api_key_set
  return url && key ? "ready" : "needsSetup"
}

export class RestartTimeout extends Error {
  last: RestartState | null
  constructor(last: RestartState | null) {
    super("restart_timeout")
    this.name = "RestartTimeout"
    this.last = last
  }
}

export interface WaitOptions {
  timeoutMs?: number
  intervalMs?: number
  sleep?: (ms: number) => Promise<void>
  now?: () => number
  onTick?: (elapsedMs: number) => void
}

/** Poll until the restart *id* is over: `/health/live` answers again, then the (new) service
    reports the helper's record. Resolves with a `done` record served by another process than
    *previousPid*, or with a `failed` one; throws RestartTimeout after *timeoutMs*. */
export async function waitForRestart(id: string, previousPid: number, opts: WaitOptions = {}): Promise<RestartState> {
  const {
    timeoutMs = 240_000, intervalMs = 1_500,
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)), now = () => Date.now(), onTick,
  } = opts
  const start = now()
  let last: RestartState | null = null
  while (now() - start < timeoutMs) {
    await sleep(intervalMs)
    onTick?.(now() - start)
    try {
      const live = await fetch("/health/live", { cache: "no-store" })
      if (!live.ok) continue
      const st = await api.restartState(id)
      last = st
      if (st.state === "failed") return st
      if (st.state === "done" && st.service_pid !== previousPid) return st
    } catch (err) {
      // the service is down while it restarts; a 404 means the record is gone (another home)
      if (err instanceof ApiError && err.status === 404) throw err
    }
  }
  throw new RestartTimeout(last)
}
