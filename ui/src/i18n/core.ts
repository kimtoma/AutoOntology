/* The UI language: English or Korean. The dictionaries are plain objects (en.ts, ko.ts) with
   the same keys; `ko` is typed against `en`, so a missing key fails tsc. The current language
   lives here (not only in React state) so that pure helpers (labels, formats, durations) can
   translate too; LangProvider re-renders the tree when it changes. */

import { en } from "@/i18n/en"
import { ko } from "@/i18n/ko"

export type Lang = "en" | "ko"
export const LANGS: readonly Lang[] = ["en", "ko"]
export type MessageKey = keyof typeof en
export type Vars = Record<string, string | number | null | undefined>

export const LANG_STORAGE_KEY = "autoontology.lang"
export const LANG_NAMES: Record<Lang, string> = { en: "English", ko: "한국어" }

const DICTS: Record<Lang, Record<MessageKey, string>> = { en, ko }

const isLang = (v: unknown): v is Lang => v === "en" || v === "ko"

function readStorage(): string | null {
  try { return localStorage.getItem(LANG_STORAGE_KEY) } catch { return null }
}
function writeStorage(lang: Lang) {
  try { localStorage.setItem(LANG_STORAGE_KEY, lang) } catch { /* private mode */ }
}

/** `?lang=` -> localStorage -> the browser language (ko* = Korean) -> English. A `?lang=`
    value is also remembered, so the choice survives a reload without the parameter. */
export function resolveLang(): Lang {
  if (typeof window === "undefined") return "en"
  const param = new URLSearchParams(window.location.search).get("lang")?.toLowerCase()
  if (isLang(param)) {
    writeStorage(param)
    return param
  }
  const stored = readStorage()
  if (isLang(stored)) return stored
  const browser = typeof navigator !== "undefined" ? navigator.language ?? "" : ""
  return browser.toLowerCase().startsWith("ko") ? "ko" : "en"
}

let current: Lang = resolveLang()
const listeners = new Set<() => void>()

function applyDocumentLang(lang: Lang) {
  if (typeof document !== "undefined") document.documentElement.lang = lang
}
applyDocumentLang(current)

export const getLang = (): Lang => current

/** Switch the language (and remember it unless *persist* is false). */
export function setLang(lang: Lang, persist = true) {
  if (persist) writeStorage(lang)
  if (lang === current) return
  current = lang
  applyDocumentLang(lang)
  for (const l of listeners) l()
}

/** Re-read `?lang=`, storage and the browser (tests, and a page that changed its URL). */
export function initLang() {
  setLang(resolveLang(), false)
}

export function subscribeLang(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

const fill = (text: string, vars?: Vars) =>
  vars ? text.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? "") : m)) : text

/** The message *key* in the current language, with `{name}` placeholders filled from *vars*.
    Falls back to English, then to the key itself. */
export function t(key: MessageKey, vars?: Vars): string {
  const text = DICTS[current][key] ?? en[key] ?? key
  return fill(text, vars)
}

/** `{base}.one` for 1, else `{base}.other` (Korean uses the same text for both). */
export function tn(base: string, n: number, vars?: Vars): string {
  return t(`${base}.${n === 1 ? "one" : "other"}` as MessageKey, { n: fmtNumber(n), ...vars })
}

/** Like `t`, but a placeholder may be filled with a React node (bold numbers, code, links). */
export function tx<N>(key: MessageKey, vars: Record<string, string | number | N>): (string | N)[] {
  const text = DICTS[current][key] ?? en[key] ?? key
  const out: (string | N)[] = []
  let last = 0
  for (const m of text.matchAll(/\{(\w+)\}/g)) {
    if (!(m[1] in vars)) continue
    if (m.index > last) out.push(text.slice(last, m.index))
    const v = vars[m[1]]
    out.push(typeof v === "number" ? fmtNumber(v) : v)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

/** The BCP 47 locale for Intl. */
export const locale = () => (current === "ko" ? "ko-KR" : "en-US")

export const fmtNumber = (n: number) => new Intl.NumberFormat(locale()).format(n)

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-"
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(d)
}

/** "12.3 s", "7 min 41 s", "1 h 5 min" (dur.* keys; Korean puts the unit after each number). */
export function fmtSecs(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return ""
  if (s < 60) return t("dur.s", { s: s.toFixed(1) })
  if (s < 3600) {
    const m = Math.floor(s / 60)
    return t("dur.ms", { m, s: Math.round(s - m * 60) })
  }
  const h = Math.floor(s / 3600)
  return t("dur.hm", { h, m: Math.round((s - h * 3600) / 60) })
}

/** An object whose properties read `prefix + name` in the current language: keeps the
    `LABEL[name] ?? name` call sites of the label tables working in both languages. */
export function labelTable<K extends string>(keys: Record<K, MessageKey>): Record<string, string> {
  const out = {} as Record<string, string>
  for (const [name, key] of Object.entries(keys) as [K, MessageKey][]) {
    Object.defineProperty(out, name, { enumerable: true, get: () => t(key) })
  }
  return out
}
