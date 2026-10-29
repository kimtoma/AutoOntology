import { en } from "@/i18n/en"
import { ko } from "@/i18n/ko"
import {
  fmtSecs, getLang, initLang, LANG_STORAGE_KEY, resolveLang, setLang, t, tn, type MessageKey,
} from "@/i18n/core"
import { expectedTime, localDuration } from "@/lib/checklist"
import { classifyFile, type Formats } from "@/lib/formats"
import { humanStep, progressText, STATUS_LABEL } from "@/lib/labels"
import FORMATS_JSON from "@/test/fixtures/formats.json"

const FORMATS = FORMATS_JSON as Formats

/** Values that are the same in both languages on purpose (names, formats, commands). */
const SAME_OK = new Set<string>(["nav.exports.hint"])

const words = (s: string) => s.split(/\s+/).filter(Boolean).length

describe("dictionaries", () => {
  it("have the same keys in English and Korean", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort())
  })

  it("have no empty strings", () => {
    for (const dict of [en, ko] as Record<string, string>[]) {
      const empty = Object.entries(dict).filter(([, v]) => !v.trim()).map(([k]) => k)
      expect(empty).toEqual([])
    }
  })

  it("translate every sentence (Korean value identical to English fails)", () => {
    const enDict = en as Record<string, string>
    const koDict = ko as Record<string, string>
    const same = Object.keys(enDict)
      .filter((k) => words(enDict[k]) > 3 && koDict[k] === enDict[k] && !SAME_OK.has(k))
    expect(same).toEqual([])
  })

  it("use the same placeholders in both languages", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    const enDict = en as Record<string, string>
    const koDict = ko as Record<string, string>
    const diff = Object.keys(enDict).filter((k) => names(enDict[k]).join() !== names(koDict[k]).join())
    expect(diff).toEqual([])
  })
})

describe("t()", () => {
  it("fills placeholders and switches language", () => {
    expect(t("home.progress", { done: 2 })).toBe("2 of 5 done")
    setLang("ko", false)
    expect(t("home.progress", { done: 2 })).toBe("5단계 중 2단계 완료")
    expect(getLang()).toBe("ko")
  })

  it("falls back to English, then to the key, for a missing entry", () => {
    setLang("ko", false)
    const dict = ko as Record<string, string>
    const saved = dict["home.title"]
    delete dict["home.title"]
    try {
      expect(t("home.title")).toBe("Get started")
    } finally {
      dict["home.title"] = saved
    }
    expect(t("no.such.key" as MessageKey)).toBe("no.such.key")
  })

  it("picks the plural form and formats durations", () => {
    expect(tn("samples.docs", 1)).toBe("1 document")
    expect(tn("samples.docs", 5)).toBe("5 documents")
    expect(fmtSecs(461.3)).toBe("7 min 41 s")
    expect(fmtSecs(12.34)).toBe("12.3 s")
    setLang("ko", false)
    expect(tn("samples.docs", 5)).toBe("문서 5개")
    expect(fmtSecs(461.3)).toBe("7분 41초")
    expect(fmtSecs(3900)).toBe("1시간 5분")
  })

  it("translates the pure helpers (labels, step names, classifier, estimates)", () => {
    setLang("ko", false)
    expect(STATUS_LABEL.failed).toBe("실패")
    expect(humanStep("structured:quick:compile")).toBe("quick · 비즈니스 개념 컴파일 (LLM)")
    const doc = classifyFile("규정.doc", 10, "structured", FORMATS, { kordoc: true, ocr: true })
    expect(doc.alternative).toBe("문서 소스로 추가하세요 (그쪽에서 .doc 파일을 바로 읽습니다)")
    const set = { set: "q", path: "", registered: false, sources: [],
                  durations: { ollama: { text: "20-50 min", measured: false } } }
    expect(expectedTime(set, "ollama")).toBe("20-50분 (추정)")
    // a note from the service after the duration stays verbatim
    expect(localDuration("20-50 min (qwen3:8b, 1-2 min per chunk)")).toBe("20-50분 (qwen3:8b, 1-2 min per chunk)")
    expect(localDuration("about an hour")).toBe("about an hour")
    // progress counters are named in Korean
    expect(progressText({ chunks: { succeeded: 28 }, concepts: { succeeded: 2, failed: 1 } }))
      .toBe("청크 28/28 · 개념 2/3, 실패 1")
    expect(progressText({ chunks: { succeeded: 1 } })).not.toMatch(/chunks/)
  })
})

describe("language choice", () => {
  it("resolves ?lang=, then the stored choice, then the browser language, then English", () => {
    window.history.replaceState(null, "", "/?lang=ko")
    expect(resolveLang()).toBe("ko")
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe("ko")  // ?lang= is remembered
    window.history.replaceState(null, "", "/")
    expect(resolveLang()).toBe("ko")
    localStorage.clear()
    const nav = vi.spyOn(navigator, "language", "get")
    nav.mockReturnValue("ko-KR")
    expect(resolveLang()).toBe("ko")
    nav.mockReturnValue("en-GB")
    expect(resolveLang()).toBe("en")
    nav.mockRestore()
    localStorage.setItem(LANG_STORAGE_KEY, "fr")  // unknown values are ignored
    expect(resolveLang()).toBe("en")
  })

  it("sets <html lang>", () => {
    window.history.replaceState(null, "", "/?lang=ko")
    initLang()
    expect(document.documentElement.lang).toBe("ko")
    setLang("en")
    expect(document.documentElement.lang).toBe("en")
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe("en")
  })
})
