/* Pieces shared by the Playground tabs: citation chips, answer text with [E1] tokens, a capped
   result table with CSV download, a copy button. Every model / ontology string is rendered as
   text (React escapes it), never as HTML. */

import { Check, Copy, Download, Network } from "lucide-react"
import { useContext, useState } from "react"
import { Link } from "react-router"

import { ConceptOpener } from "@/components/playground/concept-opener"
import { Button } from "@/components/ui/button"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { conceptIdOfChip, type ElementChip } from "@/lib/api"
import { cell, ELEMENT_LABEL, toCsv } from "@/lib/playground"
import { cn } from "@/lib/utils"

const CHIP_CLASS = "inline-flex min-w-0 items-center gap-1 rounded-md border bg-muted/60 px-1.5 py-0.5 text-xs font-medium hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none"

export function ChipLink({ chip, refName, className }: {
  chip: ElementChip; refName?: string; className?: string
}) {
  const open = useContext(ConceptOpener)
  const conceptId = conceptIdOfChip(chip)
  const title = `${ELEMENT_LABEL[chip.type] ?? chip.type}: ${chip.label}`
  const body = (
    <>
      {refName && <span className="text-muted-foreground tabular-nums">{refName}</span>}
      <span className="truncate">{chip.label}</span>
    </>
  )
  return (
    <span className={cn("inline-flex max-w-full items-center gap-0.5 align-baseline", className)}
          data-citation={refName ?? chip.id} data-concept={conceptId ?? undefined}>
      {conceptId && open ? (
        <button type="button" title={title} className={CHIP_CLASS} onClick={() => open(conceptId)}>{body}</button>
      ) : (
        <Link to={conceptId ? `/ontology?concept=${encodeURIComponent(conceptId)}` : chip.graph_link || "/graph"}
              title={title} className={CHIP_CLASS}>{body}</Link>
      )}
      {chip.graph_link && (
        <Link to={chip.graph_link} aria-label={`Show ${chip.label} in the graph`} title="Show in graph"
              className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none">
          <Network className="size-3" aria-hidden />
        </Link>
      )}
    </span>
  )
}

const CITE = /\[\s*(E\d+(?:\s*[,;/]?\s*E\d+)*)\s*\]/g

/** Answer text with its [E1] / [E1, E2] tokens replaced by chips of the cited elements. */
export function CitedText({ text, citations }: {
  text: string; citations: Record<string, ElementChip>
}) {
  const parts: React.ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(CITE)) {
    const at = m.index ?? 0
    if (at > last) parts.push(text.slice(last, at))
    const refs = m[1].match(/E\d+/g) ?? []
    refs.forEach((r, i) => {
      const chip = citations[r]
      parts.push(chip
        ? <ChipLink key={`${at}-${i}`} chip={chip} refName={r} className="mx-0.5" />
        : <span key={`${at}-${i}`} className="text-xs text-muted-foreground">[{r}]</span>)
    })
    last = at + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return <div className="whitespace-pre-wrap break-words leading-relaxed">{parts}</div>
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button type="button" size="sm" variant="ghost" aria-label={label}
            onClick={() => {
              navigator.clipboard?.writeText(text).then(() => {
                setDone(true)
                window.setTimeout(() => setDone(false), 1500)
              }).catch(() => {})
            }}>
      {done ? <Check aria-hidden /> : <Copy aria-hidden />} {done ? "Copied" : label}
    </Button>
  )
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }))
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ResultTable({ columns, rows, max = 200, csvName, id }: {
  columns: string[]; rows: unknown[][]; max?: number; csvName?: string; id?: string
}) {
  const shown = rows.slice(0, max)
  return (
    <div className="grid gap-2" id={id}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span data-rows={rows.length}>
          {rows.length.toLocaleString()} row{rows.length === 1 ? "" : "s"}{rows.length > max && ` (first ${max} shown)`}
        </span>
        {csvName && rows.length > 0 && (
          <Button type="button" size="sm" variant="outline"
                  onClick={() => download(csvName, toCsv(columns, rows))}>
            <Download aria-hidden /> CSV
          </Button>
        )}
      </div>
      <div className="max-h-[28rem] overflow-auto rounded-md border">
        <Table>
          <TableHeader className="sticky top-0 bg-background">
            <TableRow>
              {columns.map((c) => <TableHead key={c} className="whitespace-nowrap">{c}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((r, i) => (
              <TableRow key={i}>
                {columns.map((_, j) => (
                  <TableCell key={j} className="max-w-[24rem] align-top text-xs whitespace-normal break-words">
                    {cell(r[j])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
