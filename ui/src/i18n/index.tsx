/* React side of the UI language: the provider, the hooks and the header switch. */

import { Fragment, useSyncExternalStore, type ReactNode } from "react"

import { getLang, setLang, subscribeLang, t, type Lang } from "@/i18n/core"

export { fmtDate, fmtNumber, fmtSecs, getLang, LANG_NAMES, LANGS, setLang, t, tn, tx } from "@/i18n/core"
export type { Lang, MessageKey } from "@/i18n/core"

/** Re-renders (re-mounts) its children when the language changes, so that every string,
    including those of pure helpers, is read again. Query caches above it are kept. */
export function LangProvider({ children }: { children: ReactNode }) {
  const lang = useSyncExternalStore(subscribeLang, getLang, getLang)
  return <Fragment key={lang}>{children}</Fragment>
}

export function useLang(): [Lang, (lang: Lang) => void] {
  const lang = useSyncExternalStore(subscribeLang, getLang, getLang)
  return [lang, setLang]
}

/** `t(key, vars?)` of the current language (subscribes the component to changes). */
export function useT() {
  useSyncExternalStore(subscribeLang, getLang, getLang)
  return t
}
