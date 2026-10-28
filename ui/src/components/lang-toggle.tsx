import { Globe } from "lucide-react"
import { useEffect, useRef } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { LANG_NAMES, LANGS, useLang, useT, type Lang } from "@/i18n"

/** Set before the switch re-mounts the tree, so the new trigger takes the focus back. */
let refocus = false

/** The header's language switch: a globe button with English and Korean (each in its own language). */
export function LangToggle() {
  const t = useT()
  const [lang, setLang] = useLang()
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (refocus) { refocus = false; trigger.current?.focus() }
  }, [])
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button ref={trigger} variant="ghost" size="sm" id="lang-toggle" data-lang={lang}
                aria-label={t("shell.lang.label", { lang: LANG_NAMES[lang] })} className="gap-1.5 px-2">
          <Globe className="size-4" aria-hidden />
          <span className="text-xs font-medium uppercase">{lang}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" id="lang-menu">
        <DropdownMenuRadioGroup value={lang}
                                onValueChange={(v) => { if (v !== lang) refocus = true; setLang(v as Lang) }}>
          {LANGS.map((l) => (
            <DropdownMenuRadioItem key={l} value={l} lang={l} data-lang-option={l}>
              {LANG_NAMES[l]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
