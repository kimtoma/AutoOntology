import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import { NAV } from "@/components/nav"
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command"

/** ⌘K / Ctrl+K: jump to a page or search the ontology's concepts. */
export function CommandMenu() {
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
    return () => document.removeEventListener("keydown", onKey)
  }, [])
  const go = (to: string) => { setOpen(false); setText(""); navigate(to) }
  return (
    <CommandDialog open={open} onOpenChange={setOpen} title="Go to"
                   description="Jump to a page or search concepts">
      <CommandInput placeholder="Pages, concepts…" value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>No results</CommandEmpty>
        {text.trim() && (
          <CommandGroup heading="Concepts">
            <CommandItem value={`concept-search ${text}`}
                         onSelect={() => go(`/ontology?q=${encodeURIComponent(text.trim())}`)}>
              Search the ontology for “{text.trim()}”
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading="Pages">
          {NAV.map((n) => (
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
