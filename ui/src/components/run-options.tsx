import { Loader2, Play } from "lucide-react"
import { useState, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { RunKind, RunOptions, Source } from "@/lib/api"
import { actionsFor, RUN_KO } from "@/lib/labels"

type Action = Exclude<RunKind, "ingest">
const AUTO = "__auto"

function Toggle({ id, label, hint, checked, onChange }: {
  id: string; label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-start gap-3">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id} className="grid gap-0.5 leading-snug">
        <span>{label}</span>
        {hint && <span className="text-xs font-normal text-muted-foreground">{hint}</span>}
      </Label>
    </div>
  )
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

const num = (v: string): number | null => (v.trim() === "" ? null : Number(v))

/** Every option `POST /runs` accepts, per kind (`BuildOptions` … `ExportOptions` in api.py). */
export function RunOptionsDialog({ source, sources, pending, onClose, onStart }: {
  source: Source | null
  sources: Source[]
  pending: boolean
  onClose: () => void
  onStart: (kind: Action, options: RunOptions) => void
}) {
  return (
    <Dialog open={!!source} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        {source && <OptionsForm key={source.name} source={source} sources={sources} pending={pending}
                                onClose={onClose} onStart={onStart} />}
      </DialogContent>
    </Dialog>
  )
}

function OptionsForm({ source, sources, pending, onClose, onStart }: {
  source: Source
  sources: Source[]
  pending: boolean
  onClose: () => void
  onStart: (kind: Action, options: RunOptions) => void
}) {
  const kinds = actionsFor(source)
  const [kind, setKind] = useState<Action>(kinds[0])
  const [resume, setResume] = useState(false)
  const [skipCompile, setSkipCompile] = useState(false)
  const [skipExport, setSkipExport] = useState(false)
  const [workers, setWorkers] = useState("")
  const [maxChunks, setMaxChunks] = useState("")
  const [structured, setStructured] = useState(AUTO)
  const [dryRun, setDryRun] = useState(false)
  const [threshold, setThreshold] = useState("")
  const [minConf, setMinConf] = useState("")
  const [exportSources, setExportSources] = useState<string[]>(
    source.kind === "documents" ? [] : [source.name])

  const options = (): RunOptions => {
    switch (kind) {
      case "build": return { resume, skip_compile: skipCompile, skip_export: skipExport }
      case "extract": return { resume, workers: num(workers), max_chunks: num(maxChunks) }
      case "align": return {
        resume, dry_run: dryRun, structured_source: structured === AUTO ? null : structured,
        threshold: num(threshold), min_conf: num(minConf),
      }
      default: return { sources: exportSources.length ? exportSources : null }
    }
  }
  const structuredSources = sources.filter((s) => s.kind !== "documents")

  return (
    <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); onStart(kind, options()) }}>
      <DialogHeader>
        <DialogTitle>옵션과 함께 실행 · {source.name}</DialogTitle>
        <DialogDescription>CLI와 같은 옵션입니다. 비워 둔 값은 기본값을 씁니다.</DialogDescription>
      </DialogHeader>
      <Tabs value={kind} onValueChange={(v) => setKind(v as Action)}>
        <TabsList className="w-full">
          {kinds.map((k) => <TabsTrigger key={k} value={k}>{RUN_KO[k]}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      {kind !== "export" && (
        <Toggle id="opt-resume" label="이어서 실행 (--resume)" hint="성공한 단계는 건너뜁니다"
                checked={resume} onChange={setResume} />
      )}
      {kind === "build" && (
        <>
          <Toggle id="opt-skip-compile" label="컴파일 건너뛰기 (--skip-compile)"
                  checked={skipCompile} onChange={setSkipCompile} />
          <Toggle id="opt-skip-export" label="내보내기 건너뛰기 (--skip-export)"
                  checked={skipExport} onChange={setSkipExport} />
        </>
      )}
      {kind === "extract" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="opt-workers" label="작업자 수 (--workers)" hint="1–16, 비우면 프로파일 기본값">
            <Input id="opt-workers" type="number" min={1} max={16} value={workers}
                   onChange={(e) => setWorkers(e.target.value)} />
          </Field>
          <Field id="opt-max-chunks" label="최대 청크 (--max-chunks)" hint="비우면 전체">
            <Input id="opt-max-chunks" type="number" min={0} value={maxChunks}
                   onChange={(e) => setMaxChunks(e.target.value)} />
          </Field>
        </div>
      )}
      {kind === "align" && (
        <>
          <Field id="opt-structured" label="정형 소스 (--structured-source)">
            <Select value={structured} onValueChange={setStructured}>
              <SelectTrigger id="opt-structured" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>자동 (빌드된 정형 소스 전체)</SelectItem>
                {structuredSources.map((s) => <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Toggle id="opt-dry-run" label="시험 실행 (--dry-run)" hint="판정만 저장하고 Term은 바꾸지 않습니다"
                  checked={dryRun} onChange={setDryRun} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="opt-threshold" label="유사도 임계값 (--threshold)" hint="0–1">
              <Input id="opt-threshold" type="number" min={0} max={1} step={0.01} value={threshold}
                     onChange={(e) => setThreshold(e.target.value)} />
            </Field>
            <Field id="opt-min-conf" label="최소 확신도 (--min-conf)" hint="0–1">
              <Input id="opt-min-conf" type="number" min={0} max={1} step={0.01} value={minConf}
                     onChange={(e) => setMinConf(e.target.value)} />
            </Field>
          </div>
        </>
      )}
      {kind === "export" && (
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium">포함할 소스 (--sources)</legend>
          <p className="text-xs text-muted-foreground">아무것도 고르지 않으면 이 소스만(문서 소스는 정렬로 연결된 정형 소스 포함) 내보냅니다.</p>
          {sources.map((s) => (
            <Toggle key={s.name} id={`opt-src-${s.name}`} label={s.name}
                    checked={exportSources.includes(s.name)}
                    onChange={(on) => setExportSources((cur) =>
                      on ? [...cur, s.name] : cur.filter((n) => n !== s.name))} />
          ))}
        </fieldset>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>닫기</Button>
        <Button type="submit" disabled={pending}>
          {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
          {RUN_KO[kind]} 시작
        </Button>
      </DialogFooter>
    </form>
  )
}
