import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import { navigationFor } from "@/components/nav"
import { useCanUse, useIdentity } from "@/lib/identity"
import { useT } from "@/i18n"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"

import { OPEN_COMMAND_MENU } from "@/lib/command-menu"

/** ⌘K / Ctrl+K: jump to a page or search the ontology's concepts. */
export function CommandMenu() {
  const identity = useIdentity()
  const canReadKnowledge = useCanUse("knowledge.read")
  const t = useT()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const navigate = useNavigate()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    document.addEventListener("keydown", onKey)
    const openMenu = () => setOpen(true)
    document.addEventListener(OPEN_COMMAND_MENU, openMenu)
    return () => {
      document.removeEventListener("keydown", onKey)
      document.removeEventListener(OPEN_COMMAND_MENU, openMenu)
    }
  }, [])
  const go = (to: string) => {
    setOpen(false)
    setText("")
    navigate(to)
  }
  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title={t("cmd.title")}
      description={t("cmd.description")}
    >
      <CommandInput placeholder={t("cmd.placeholder")} value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>{t("cmd.empty")}</CommandEmpty>
        {text.trim() && canReadKnowledge && (
          <CommandGroup heading={t("cmd.concepts")}>
            <CommandItem
              value={`concept-search ${text}`}
              onSelect={() => go(`/ontology?q=${encodeURIComponent(text.trim())}`)}
            >
              {t("cmd.search", { text: text.trim() })}
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading={t("cmd.pages")}>
          {navigationFor(identity?.session).map((n) => (
            <CommandItem key={n.path} value={`${n.label} ${n.hint}`} onSelect={() => go(n.path)}>
              <n.icon aria-hidden /> {n.label}
              <span className="ml-auto text-xs text-muted-foreground">{n.hint}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
