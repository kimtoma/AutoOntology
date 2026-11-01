import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/api"
import { comparisonApi, type ComparisonReport, type ComparisonResult } from "@/lib/comparison"
import { useCanUse, useIdentity } from "@/lib/identity"
import { useT, type MessageKey } from "@/i18n"

const verdictKeys: Record<string, MessageKey> = {
  matches_reference: "comparison.matches", different: "comparison.different",
  expected_abstention: "comparison.abstained", unexpected_answer: "comparison.unexpectedAnswer",
  unexpected_abstention: "comparison.unexpectedAbstention", evidence_missing: "comparison.missingEvidence",
  execution_error: "comparison.executionError",
}
function ResultTable({ value }: { value: ComparisonResult }) {
  return <div className="max-w-full overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>{value.columns.map((col, index) => <th key={index} className="border-b py-2 pr-4 font-medium">{col}</th>)}</tr></thead><tbody>{value.rows.map((row, index) => <tr key={index}>{row.map((cell, col) => <td key={col} className="border-b py-2 pr-4 font-mono tabular-nums">{cell === null ? "NULL" : typeof cell === "object" ? cell.value : String(cell)}</td>)}</tr>)}</tbody></table></div>
}
interface InputSummary { input: string; title: string; tables: number; rows: number; questions: string[] }
function describeInput(input: string): InputSummary | null {
  try {
    const value = JSON.parse(input)
    const pack = value?.pack
    if (typeof pack?.title !== "string" || !Array.isArray(pack.tables) || !Array.isArray(pack.cases)) return null
    const tables = pack.tables as { rows?: unknown[] }[]
    const questions = (pack.cases as { question?: unknown }[]).slice(0, 20).flatMap(item => typeof item?.question === "string" ? [item.question] : [])
    return { input, title: pack.title.slice(0, 200), tables: tables.length, rows: tables.reduce((sum, item) => sum + (Array.isArray(item?.rows) ? item.rows.length : 0), 0), questions }
  } catch { return null }
}
function ComparisonForm() {
  const t = useT()
  const [input, setInput] = useState("")
  const [fileError, setFileError] = useState("")
  const [preview, setPreview] = useState<InputSummary | null>(null)
  const [saved, setSaved] = useState<{ report: ComparisonReport; input: string } | null>(null)
  const load = useMutation({ mutationFn: comparisonApi.sample, onSuccess: value => { const text = JSON.stringify(value, null, 2); setInput(text); setPreview(describeInput(text)); setSaved(null); setFileError("") } })
  const run = useMutation({
    mutationFn: (submitted: string) => {
      let value: unknown
      try { value = JSON.parse(submitted) } catch { throw new Error(t("comparison.invalidJson")) }
      setPreview(describeInput(submitted))
      return comparisonApi.compare(value)
    },
    onSuccess: (report, submitted) => setSaved({ report, input: submitted }),
  })
  const stale = saved && input !== saved.input
  const download = () => {
    if (!saved) return
    const object = URL.createObjectURL(new Blob([JSON.stringify({ input: JSON.parse(saved.input), report: saved.report }, null, 2)], { type: "application/json" }))
    const anchor = document.createElement("a")
    anchor.href = object
    anchor.download = `comparison-${saved.report.pack_id}-${saved.report.sha256.slice(0, 8)}.json`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(object), 0)
  }
  return <section id="frozen-comparison" className="space-y-4 border-t py-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">{t("comparison.title")}</h3><p className="mt-2 max-w-3xl text-xs leading-6 text-muted-foreground">{t("comparison.hint")}</p></div><Button size="sm" variant="outline" disabled={load.isPending || run.isPending} onClick={() => { run.reset(); load.mutate() }}>{t("comparison.sample")}</Button></div>
    <div className="flex flex-wrap items-center gap-3"><label className="text-xs">{t("comparison.upload")}<input className="mt-1 block max-w-full text-xs" type="file" accept="application/json,.json" disabled={run.isPending || load.isPending} onChange={async event => {
      const file = event.target.files?.[0]
      if (!file) return
      if (file.size > 2 * 1024 * 1024) { setFileError(t("comparison.tooLarge")); return }
      try { const text = await file.text(); setInput(text); setPreview(describeInput(text)); setSaved(null); setFileError(""); run.reset() } catch { setFileError(t("comparison.invalidJson")) }
    }} /></label><Button size="sm" disabled={!input.trim() || run.isPending || load.isPending} onClick={() => { setFileError(""); run.mutate(input) }}>{t(run.isPending ? "comparison.running" : "comparison.run")}</Button></div>
    {preview?.input === input && <div className="space-y-2 border-y py-3 text-xs"><p className="font-medium">{preview.title}</p><p className="text-muted-foreground">{t("comparison.inputCounts", { tables: preview.tables, rows: preview.rows, cases: preview.questions.length })}</p><details open={!saved}><summary className="cursor-pointer">{t("comparison.questions")}</summary><ol className="mt-2 list-inside list-decimal space-y-1 text-muted-foreground">{preview.questions.map((question, index) => <li key={index}>{question}</li>)}</ol></details></div>}
    <details className="text-xs" open={!input}><summary className="cursor-pointer">{t("comparison.input")}</summary><Textarea disabled={load.isPending} aria-label={t("comparison.input")} className="mt-3 min-h-36 font-mono text-xs" value={input} maxLength={2 * 1024 * 1024} onChange={event => setInput(event.target.value)} placeholder={t("comparison.inputHint")} /></details>
    {(!!fileError || !!load.error || !!run.error) && <p role="alert" className="text-xs text-destructive">{fileError || errorMessage(run.error || load.error)}</p>}
    {run.isPending && <p role="status" className="text-xs text-muted-foreground">{t("comparison.waiting")}</p>}
    {saved && <div className="space-y-4" data-comparison-sha={saved.report.sha256}>
      <div className="flex flex-wrap justify-between gap-3 border-y py-3"><div className="space-y-1"><h4 className="text-sm font-medium">{saved.report.title}</h4><p className="text-xs text-muted-foreground">{t("comparison.unapproved")} · {t("comparison.noBaseline")}</p><p className="text-xs">{t(saved.report.comparison_status === "constructed_examples" ? "comparison.examples" : "comparison.reported")}</p>{stale && <p role="status" className="text-xs text-warning">{t("comparison.stale")}</p>}</div><Button size="sm" variant="outline" onClick={download}>{t("comparison.download")}</Button></div>
      {saved.report.cases.map(item => <article key={item.id} className="space-y-3 border-b pb-4"><div><span className="font-mono text-[11px] text-muted-foreground">{item.id}</span><h5 className="text-sm font-medium">{item.question}</h5><p className="mt-1 text-xs leading-6 text-muted-foreground">{item.definition}</p></div><div className="grid gap-4 lg:grid-cols-3"><section className="min-w-0 border-l pl-3"><h6 className="mb-2 text-xs font-medium">{t("comparison.reference")}</h6>{item.reference ? <ResultTable value={item.reference} /> : <p className="text-xs">{t("comparison.defer")} · {item.expected_state}</p>}<details className="mt-2 text-xs"><summary>{t("comparison.sql")}</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-[11px]">{item.reference_sql || item.expected_state}</pre></details></section>{item.arms.map(arm => <section key={arm.id} className="min-w-0 border-l pl-3"><h6 className="text-xs font-medium">{arm.id} · {arm.label}</h6><p className={`my-2 text-xs ${arm.verdict === "different" || arm.verdict === "execution_error" ? "text-destructive" : "text-muted-foreground"}`}>{t(verdictKeys[arm.verdict] || "comparison.defer")}</p>{arm.result && <ResultTable value={arm.result} />}<details className="mt-2 text-xs"><summary>{t("comparison.sql")}</summary><pre className="mt-2 whitespace-pre-wrap break-words text-[11px]">{arm.answer.sql || arm.answer.reason}</pre></details><p className="mt-2 text-[11px] text-muted-foreground">{t("comparison.citations")} · {arm.answer.citations.join(" / ") || "—"}</p></section>)}</div></article>)}
      <details className="text-xs"><summary className="cursor-pointer">{t("comparison.evidence")}</summary>{saved.report.evidence.map(item => <section key={item.id} className="border-t py-3"><strong className="font-medium">{item.title}</strong><pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-6">{item.text}</pre></section>)}</details>
      <details className="text-xs"><summary className="cursor-pointer">{t("comparison.receipt")}</summary><dl className="mt-3 grid gap-2 break-all font-mono text-[11px]"><div>report · {saved.report.sha256}</div><div>pack · {saved.report.pack_sha256}</div><div>attempts · {saved.report.attempts_sha256}</div><div>engine · {saved.report.engine.name} / SQLite {saved.report.engine.sqlite_version}</div><div>validator · {saved.report.engine.validator_sha256}</div></dl></details>
      <p className="text-xs leading-6 text-muted-foreground">{t("comparison.limit")}</p>
    </div>}
  </section>
}
export function ComparisonPanel() {
  const identity = useIdentity()?.session
  const canRun = useCanUse("validation.run")
  if (!canRun) return null
  const key = identity ? `${identity.workspace.workspace_id}:${identity.credential_id}:${identity.policy_version}` : "legacy"
  return <ComparisonForm key={key} />
}
