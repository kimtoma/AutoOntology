import type { ExportEntry, OntologySummary, Run, SampleSet, ServiceStatus, Source } from "@/lib/api"
import {
  allDone, checklist, expectedTime, loginCommand, modelsReady, otherSources, sampleSetFor,
} from "@/lib/checklist"
import { acceptList, classifyFile, extensionOf, extensionsByEngine, type Formats } from "@/lib/formats"
import { humanStep, runSeconds, splitStep, stepFailure, stepLabel, stepSeconds } from "@/lib/labels"
import FORMATS_JSON from "@/test/fixtures/formats.json"

const FORMATS = FORMATS_JSON as Formats

const status = (over: Partial<ServiceStatus["providers"]["chat"]> = {}, checks: ServiceStatus["checks"] = {}): ServiceStatus => ({
  status: "ok", version: "0.2.0", kordoc_available: true, service_url: "http://127.0.0.1:7860",
  providers: {
    chat_provider: "claude", chat_model: "openai/claude:claude-sonnet-5-5", embed_provider: "ollama",
    embed_model: "bge-m3", openai_base_url: "",
    chat: { name: "claude", model: "openai/claude:claude-sonnet-5-5", available: true, logged_in: true,
            detail: "logged in (claude.ai, max)", remedy: "", last_test: null, ...over },
    embed: { name: "ollama", model: "bge-m3" },
  },
  checks: { backend: { status: "ok" }, postgres: { status: "ok" }, ...checks },
  doctor: "uv run autoontology doctor",
})
const SOURCE = { name: "quick", kind: "files" } as Source
const ONTOLOGY = { revision: 2 } as OntologySummary
const EXPORT = (revision: number) => ({ revision, dir: `r000${revision}` }) as ExportEntry

describe("checklist (the Home step-state reducer)", () => {
  it("is all checking before anything answered", () => {
    expect(checklist({})).toEqual({
      models: "checking", sources: "checking", build: "checking", explore: "checking", export: "checking",
    })
  })

  it("makes the first incomplete step active and the later ones pending", () => {
    expect(checklist({ status: status(), sources: [], ontology: null, exports: [] })).toEqual({
      models: "done", sources: "active", build: "pending", explore: "pending", export: "pending",
    })
    expect(checklist({ status: status(), sources: [SOURCE], ontology: null, exports: [] })).toMatchObject({
      sources: "done", build: "active", explore: "pending",
    })
  })

  it("asks to log in first while the chat provider is not ready", () => {
    const s = checklist({ status: status({ logged_in: false }), sources: [SOURCE], ontology: null, exports: [] })
    expect(s.models).toBe("active")
    expect(s.sources).toBe("done")
    expect(s.build).toBe("pending")
  })

  it("needs the backend, Postgres and the Ollama models", () => {
    expect(modelsReady(status())).toBe(true)
    expect(modelsReady(status({}, { postgres: { status: "error" } }))).toBe(false)
    expect(modelsReady(status({}, { backend: { status: "error" } }))).toBe(false)
    expect(modelsReady(status({}, { ollama: { status: "error", detail: "missing models: bge-m3" } }))).toBe(false)
    expect(modelsReady(status({ available: false }))).toBe(false)
  })

  it("is done when the latest revision is exported, and asks again after a new build", () => {
    const done = checklist({ status: status(), sources: [SOURCE], ontology: ONTOLOGY, exports: [EXPORT(2), EXPORT(1)] })
    expect(allDone(done)).toBe(true)
    const stale = checklist({ status: status(), sources: [SOURCE], ontology: ONTOLOGY, exports: [EXPORT(1)] })
    expect(stale).toMatchObject({ build: "done", explore: "done", export: "active" })
    expect(allDone(stale)).toBe(false)
  })

  it("keeps a step checking while its own answer is missing", () => {
    expect(checklist({ status: status(), sources: [SOURCE], exports: [] })).toMatchObject({
      build: "checking", explore: "checking", export: "checking",
    })
  })
})

describe("provider login commands", () => {
  it("names the CLI login or the Ollama fix", () => {
    expect(loginCommand(status({ logged_in: false }))?.command).toBe("claude auth login")
    const codex = status({ name: "codex", logged_in: false })
    expect(loginCommand(codex)?.command).toBe("codex login")
    const down = status({ name: "ollama", available: false })
    expect(loginCommand(down)?.command).toBe("ollama serve")
    expect(loginCommand(status({ name: "ollama" }))?.command).toBe("uv run autoontology setup")
  })
})

describe("formats from /api/status", () => {
  it("builds the accept lists from the API, per family", () => {
    const structured = acceptList(FORMATS, "structured").split(",")
    const documents = acceptList(FORMATS, "documents").split(",")
    expect(structured).toEqual(Object.keys(FORMATS.structured).sort())
    expect(structured).toContain(".xls")
    expect(structured).not.toContain(".doc")
    for (const ext of [".doc", ".csv", ".xls", ".png", ".jpg", ".webp", ".hwp", ".pdf"]) expect(documents).toContain(ext)
    expect(acceptList(undefined, "documents")).toBe("")
  })

  it("groups extensions by engine", () => {
    const by = extensionsByEngine(FORMATS, "documents")
    expect(by.native).toContain(".doc")
    expect(by.kordoc).toContain(".hwpx")
    expect(by["kordoc-ocr"]).toEqual([".jpeg", ".jpg", ".png", ".webp"])
  })

  it("lowercases the extension and ignores names without one", () => {
    expect(extensionOf("Report.PDF")).toBe(".pdf")
    expect(extensionOf("dir/sub/a.b.csv")).toBe(".csv")
    expect(extensionOf(".env")).toBe("")
    expect(extensionOf("README")).toBe("")
  })
})

describe("the per-file preview classifier", () => {
  const all = { kordoc: true, ocr: true }
  it("reads .doc natively as a document, and refers it to Documents from a table source", () => {
    expect(classifyFile("거래처관리매뉴얼.doc", 10, "documents", FORMATS, all))
      .toMatchObject({ state: "supported", engine: "native", ext: ".doc" })
    const asTable = classifyFile("manual.doc", 10, "structured", FORMATS, all)
    expect(asTable.state).toBe("unsupported")
    expect(asTable.alternative).toMatch(/Documents source/)
    expect(asTable.alternative).toMatch(/natively/)
  })

  it("explains unknown formats with an alternative", () => {
    const xyz = classifyFile("data.xyz", 1, "documents", FORMATS, all)
    expect(xyz).toMatchObject({ state: "unsupported", engine: null })
    expect(xyz.reason).toMatch(/\.xyz/)
    expect(classifyFile("memo.rtf", 1, "documents", FORMATS, all).alternative).toBe("save it as .docx")
    expect(classifyFile("noext", 1, "documents", FORMATS, all).reason).toMatch(/no extension/)
  })

  it("flags files that need kordoc or the OCR models", () => {
    expect(classifyFile("a.hwp", 1, "documents", FORMATS, { kordoc: false, ocr: false }))
      .toMatchObject({ state: "needs_kordoc", engine: "kordoc" })
    expect(classifyFile("scan.PNG", 1, "documents", FORMATS, { kordoc: true, ocr: false }))
      .toMatchObject({ state: "needs_ocr", engine: "kordoc-ocr" })
    expect(classifyFile("scan.png", 1, "documents", FORMATS, { kordoc: false, ocr: true }).state).toBe("needs_ocr")
    expect(classifyFile("scan.png", 1, "documents", FORMATS, all).state).toBe("supported")
    expect(classifyFile("suppliers.xls", 1, "structured", FORMATS, { kordoc: false, ocr: false }))
      .toMatchObject({ state: "supported", engine: "native" })
  })
})

describe("build step labels", () => {
  it("maps every build step to plain words", () => {
    expect(humanStep("structured:hanbit:load")).toBe("hanbit · Load tables")
    expect(humanStep("structured:hanbit:register")).toBe("hanbit · Register with the engine")
    expect(humanStep("structured:hanbit:ingest")).toBe("hanbit · Ingest rows")
    expect(humanStep("structured:hanbit:compile")).toBe("hanbit · Compile business concepts (LLM)")
    expect(humanStep("documents:hanbit-docs:read")).toBe("hanbit-docs · Read documents")
    expect(humanStep("documents:hanbit-docs:extract")).toBe("hanbit-docs · Extract concepts (LLM)")
    expect(humanStep("documents:hanbit-docs:consolidate")).toBe("hanbit-docs · Merge duplicate concepts")
    expect(humanStep("align:hanbit-docs")).toBe("hanbit-docs · Match document concepts to table concepts")
    expect(humanStep("merge")).toBe("Build the unified ontology")
    expect(humanStep("snapshot")).toBe("Save revision")
    expect(humanStep("serialize")).toBe("Serialize")  // export steps keep their labels
    expect(humanStep("something:new")).toBe("something:new")
  })

  it("labels chips inside their group", () => {
    expect(splitStep("merge").title).toBe("Build the unified ontology")
    expect(splitStep("align:docs").title).toBe("Match document concepts to table concepts")
    expect(stepLabel("compile", "structured:hanbit")).toBe("Compile business concepts (LLM)")
    expect(stepLabel("docs", "align")).toBe("docs")
    expect(stepLabel("merge", "merge")).toBe("")
  })

  it("times steps and runs", () => {
    const step = { started_at: "2026-10-29T00:00:00Z", finished_at: "2026-10-29T00:01:30Z", detail: {} }
    expect(stepSeconds(step)).toBe(90)
    expect(stepSeconds({ ...step, detail: { seconds: 89.5 } })).toBe(89.5)
    expect(stepSeconds({ ...step, finished_at: null }, Date.parse("2026-10-29T00:00:10Z"))).toBe(10)
    expect(stepSeconds({ started_at: null, finished_at: null, detail: {} })).toBeNull()
    const run = { started_at: "2026-10-29T00:00:00Z", finished_at: null } as Run
    expect(runSeconds(run, [{ ...step, name: "merge", status: "succeeded" }])).toBe(90)
    expect(runSeconds(run, [], Date.parse("2026-10-29T00:00:05Z"))).toBe(5)
  })

  it("collects the error and remedies of a failed step", () => {
    expect(stepFailure({ status: "succeeded", detail: {} })).toBeNull()
    expect(stepFailure({
      status: "failed",
      detail: { error: "2 documents failed", remedy: "run doctor",
                failed: [{ path: "a.doc", remedy: "Save the file as .docx and add it again" },
                         { path: "b.doc", remedy: "Save the file as .docx and add it again" }] },
    })).toEqual({ error: "2 documents failed", remedies: ["run doctor", "Save the file as .docx and add it again"] })
  })
})

describe("sample sets", () => {
  const SETS = [
    { set: "quickstart", path: "/s/quickstart", registered: true,
      sources: [{ name: "quick" }, { name: "quick-docs" }],
      durations: { claude: { text: "~5 min", measured: false }, ollama: { text: "20-50 min", measured: true } } },
    { set: "hanbit", path: "/s/hanbit", registered: false, sources: [{ name: "hanbit" }] },
  ] as SampleSet[]

  it("finds the set a build covers, and only then", () => {
    expect(sampleSetFor(["quick", "quick-docs"], SETS)?.set).toBe("quickstart")
    expect(sampleSetFor(["quick-docs"], SETS)?.set).toBe("quickstart")
    expect(sampleSetFor(["quick", "mine"], SETS)).toBeUndefined()
    expect(sampleSetFor([], SETS)).toBeUndefined()
  })

  it("says whether the expected time is measured", () => {
    expect(expectedTime(SETS[0], "claude")).toBe("~5 min (estimate)")
    expect(expectedTime(SETS[0], "ollama")).toBe("20-50 min")
    expect(expectedTime(SETS[0], "codex")).toBeNull()
    expect(expectedTime(SETS[1], "claude")).toBeNull()
  })
})

describe("other sources of a sample set", () => {
  const SETS = [
    { set: "quickstart", path: "/s/q", registered: true, sources: [
      { name: "quick", kind: "structured", path: "/s/q/structured", files: 3, bytes: 1 },
      { name: "quick-docs", kind: "documents", path: "/s/q/documents", files: 5, bytes: 1 }] },
    { set: "hanbit", path: "/s/h", registered: false, sources: [
      { name: "hanbit", kind: "structured", path: "/s/h/structured", files: 9, bytes: 1 }] },
  ] as SampleSet[]
  const src = (name: string, kind: string, locator: string) => ({ name, kind, locator }) as Source

  it("counts a source as a sample source only by kind and folder, never by name alone", () => {
    const sources = [
      src("quick", "files", "/s/q/structured"),            // the sample
      src("quick-docs", "documents", "/home/me/docs"),       // same name, own folder
      src("mine", "files", "/s/q/documents"),                // sample folder, other name and kind
      src("hanbit", "files", "/s/h/structured"),
    ]
    const { others, otherSamples } = otherSources(SETS[1], sources, SETS)
    expect(others.map((s) => s.name)).toEqual(["quick", "quick-docs", "mine"])
    expect(otherSamples.map((s) => s.name)).toEqual(["quick"])
    expect(otherSources(SETS[1], [src("quick", "documents", "/s/q/structured")], SETS).otherSamples).toEqual([])
  })
})
