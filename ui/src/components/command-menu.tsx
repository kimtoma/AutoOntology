import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import { NAV } from "@/components/nav"
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command"
import { useSources } from "@/hooks/queries"

/** ⌘K / Ctrl+K: jump to a page or a source's runs, or search concepts. */
export function CommandMenu() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const navigate = useNavigate()
  const sources = useSources()
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
    <CommandDialog open={open} onOpenChange={setOpen} title="이동·검색" description="페이지로 이동하거나 개념을 검색합니다">
      <CommandInput placeholder="페이지, 소스, 개념 검색…" value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>결과 없음</CommandEmpty>
        {text.trim() && (
          <CommandGroup heading="개념">
            <CommandItem value={`concept-search ${text}`} onSelect={() => go(`/concepts?q=${encodeURIComponent(text.trim())}`)}>
              개념에서 '{text.trim()}' 검색
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading="페이지">
          {NAV.map((n) => (
            <CommandItem key={n.path} value={n.label} onSelect={() => go(n.path)}>
              <n.icon aria-hidden /> {n.label}
            </CommandItem>
          ))}
        </CommandGroup>
        {!!sources.data?.length && (
          <CommandGroup heading="소스 실행">
            {sources.data.map((s) => (
              <CommandItem key={s.name} value={`source ${s.name}`} onSelect={() => go("/runs")}>
                {s.name}
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  )
}
