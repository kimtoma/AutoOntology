/* The Home page's five steps, computed from the live API answers. Pure: the page feeds it
   the query results, the tests feed it fixtures. */

import type { ExportEntry, OntologySummary, Run, SampleSet, ServiceStatus, Source } from "@/lib/api"

export type StepId = "models" | "sources" | "build" | "explore" | "export"
/** done = nothing left to do; active = the next thing to do; pending = after an earlier step;
    checking = its data has not answered yet. */
export type StepState = "done" | "active" | "pending" | "checking"

export const STEP_ORDER: StepId[] = ["models", "sources", "build", "explore", "export"]

/** One answer: `undefined` while loading, `null` = the API said "nothing" (404 no snapshot). */
export interface ChecklistInput {
  status?: ServiceStatus
  sources?: Source[]
  ontology?: OntologySummary | null
  runs?: Run[]
  exports?: ExportEntry[]
}

/** Is the chat model usable? backend + Postgres up, the chat provider available and logged
    in, and (Ollama) its models present. */
export function modelsReady(status: ServiceStatus): boolean {
  const checks = status.checks ?? {}
  const chat = status.providers?.chat
  const up = checks.backend?.status === "ok" && checks.postgres?.status === "ok"
  const chatOk = chat ? chat.available && chat.logged_in : true
  const ollamaOk = !checks.ollama || checks.ollama.status === "ok"
  return up && chatOk && ollamaOk
}

export const isActiveRun = (r: Run) => r.status === "running" || r.status === "pending"

/** The latest build run (runs come newest first). */
export function latestRun(runs: Run[] | undefined, kind: "build" | "export"): Run | undefined {
  return (runs ?? []).find((r) => r.kind === kind)
}

/** The newest export folder, if any. */
export function latestExport(exports: ExportEntry[] | undefined): ExportEntry | undefined {
  return [...(exports ?? [])].sort((a, b) => b.revision - a.revision)[0]
}

/** Whether each step is complete; undefined while its data loads. */
function completion(input: ChecklistInput): Record<StepId, boolean | undefined> {
  const built = input.ontology === undefined ? undefined : input.ontology !== null
  const exported = input.exports === undefined || input.ontology === undefined ? undefined
    : !!input.ontology && (latestExport(input.exports)?.revision ?? 0) >= input.ontology.revision
  return {
    models: input.status ? modelsReady(input.status) : undefined,
    sources: input.sources ? input.sources.length > 0 : undefined,
    build: built,
    // exploring needs nothing but a built ontology
    explore: built,
    export: exported,
  }
}

/** done / active / pending per step: the first incomplete step is the active one. */
export function checklist(input: ChecklistInput): Record<StepId, StepState> {
  const done = completion(input)
  const out = {} as Record<StepId, StepState>
  let activeGiven = false
  for (const id of STEP_ORDER) {
    const d = done[id]
    if (d === undefined) out[id] = "checking"
    else if (d) out[id] = "done"
    else if (!activeGiven) { out[id] = "active"; activeGiven = true }
    else out[id] = "pending"
  }
  return out
}

/** Keep polling while anything is incomplete or still loading. */
export const allDone = (states: Record<StepId, StepState>) =>
  STEP_ORDER.every((id) => states[id] === "done")

/** What to run when the chat provider is not ready (shown as code on the provider card):
    the CLI login for claude/codex; for Ollama `ollama serve` when it is down, else `setup`,
    which pulls the models and builds the chat alias (`ollama pull` alone does not). */
export function loginCommand(status: ServiceStatus): { label: string; command: string } | null {
  const chat = status.providers?.chat
  const name = chat?.name ?? status.providers?.chat_provider
  if (name === "claude") return { label: "Log in", command: "claude auth login" }
  if (name === "codex") return { label: "Log in", command: "codex login" }
  if (name === "ollama") {
    if (chat && !chat.available) return { label: "Start Ollama", command: "ollama serve" }
    return { label: "Pull the models", command: "uv run autoontology setup" }
  }
  if (name === "openai") {
    return { label: "Set the endpoint", command: "uv run autoontology provider use openai" }
  }
  return null
}

export const RESTART_COMMAND = "uv run autoontology down && uv run autoontology up"

/** The sample set a build over *sourceNames* belongs to: every name is one of the set's
    sources (so its expected build time applies). undefined for any other mix. */
export function sampleSetFor(sourceNames: string[], sets: SampleSet[] | undefined): SampleSet | undefined {
  if (!sourceNames.length) return undefined
  return (sets ?? []).find((set) => {
    const names = new Set(set.sources.map((s) => s.name))
    return sourceNames.every((n) => names.has(n))
  })
}

export const LAST_SAMPLE_KEY = "autoontology.sampleSet"

/** The expected build time of *set* with *provider*, e.g. "~5 min (estimate)". */
export function expectedTime(set: SampleSet | undefined, provider: string | undefined): string | null {
  const d = provider ? set?.durations?.[provider] : undefined
  return d ? `${d.text}${d.measured ? "" : " (estimate)"}` : null
}

/** The registered sources a build would add to *set*'s own: `others` (every other source)
    and `otherSamples` (those that belong to another sample set). */
export function otherSources(set: SampleSet, sources: Source[] | undefined, sets: SampleSet[] | undefined) {
  const mine = new Set(set.sources.map((s) => s.name))
  const sampleNames = new Set((sets ?? []).filter((x) => x.set !== set.set).flatMap((x) => x.sources.map((s) => s.name)))
  const others = (sources ?? []).filter((s) => !mine.has(s.name))
  return { others, otherSamples: others.filter((s) => sampleNames.has(s.name)) }
}

/** The sample sets *sourceNames* touch (a build over two sets has no single estimate). */
export function sampleSetsTouched(sourceNames: string[], sets: SampleSet[] | undefined): SampleSet[] {
  const names = new Set(sourceNames)
  return (sets ?? []).filter((set) => set.sources.some((s) => names.has(s.name)))
}
