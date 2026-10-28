import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Database, Loader2, Plus, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { keys, useSources, useStatus } from "@/hooks/queries"
import { api, errorMessage, type Source, type SourceKind } from "@/lib/api"
import { fmtBytes, fmtTime, KIND_KO, RUN_KO, sourceState } from "@/lib/labels"

/** Server default of AUTOONTOLOGY_UPLOAD_MAX_MB when /status has not answered yet. */
const DEFAULT_UPLOAD_MAX_MB = 200

const ACCEPT = {
  documents: ".hwp,.hwpx,.hwpml,.pdf,.docx,.pptx,.xlsx,.md,.markdown,.txt,.html,.htm,.png,.jpg,.jpeg",
  files: ".csv,.tsv,.xlsx,.xlsm,.parquet,.pq,.json,.jsonl,.ndjson,.sqlite,.sqlite3,.db",
}

export function KindBadge({ kind }: { kind: string }) {
  return <Badge variant="secondary" className="font-normal">{KIND_KO[kind] ?? kind}</Badge>
}

function LastRun({ s }: { s: Source }) {
  if (s.active_run) {
    return <StatusChip status="running" label={`${RUN_KO[s.active_run.kind]} 실행 중`} />
  }
  if (!s.last_run) return <span className="text-muted-foreground">-</span>
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span>{RUN_KO[s.last_run.kind] ?? s.last_run.kind}</span>
        <StatusChip status={s.last_run.status} />
      </div>
      <span className="text-xs text-muted-foreground">{fmtTime(s.last_run.finished_at)}</span>
    </div>
  )
}

export default function SourcesPage() {
  const { data: sources, isPending, error, refetch } = useSources()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Source | null>(null)

  return (
    <>
      <PageHeader
        title="소스"
        description={<>정형 소스(CSV·XLSX·Parquet·DB)는 <b>빌드</b>로 upstream Term과 시맨틱 FK를
          만들고, 문서 소스(HWP·PDF·DOCX·MD 등)는 <b>문서 추출</b>로 개념·관계·근거를 만듭니다.</>}
        actions={<Button onClick={() => setAdding(true)} id="btn-new-source">
          <Plus aria-hidden /> 새 소스</Button>}
      />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-48 w-full" />
        : !sources.length ? (
          <EmptyState icon={Database} title="등록된 소스가 없습니다"
                      hint="새 소스로 파일을 올리거나 디렉터리·DB URL을 지정하세요."
                      action={<Button onClick={() => setAdding(true)}><Plus aria-hidden /> 새 소스</Button>} />
        ) : (
          <Card className="py-0">
            <CardContent className="px-0">
              <Table id="sources-table">
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">이름</TableHead>
                    <TableHead>종류</TableHead>
                    <TableHead className="hidden lg:table-cell">위치</TableHead>
                    <TableHead className="hidden md:table-cell">상태</TableHead>
                    <TableHead className="hidden sm:table-cell">최근 실행</TableHead>
                    <TableHead className="w-12 pr-4"><span className="sr-only">작업</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sources.map((s) => (
                    <TableRow key={s.name} data-source={s.name}>
                      <TableCell className="max-w-48 pl-4 whitespace-normal">
                        <div className="font-medium">{s.name}</div>
                        {s.database && <div className="text-xs text-muted-foreground">{s.database}</div>}
                        <div className="text-xs text-muted-foreground md:hidden">{sourceState(s)}</div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          <KindBadge kind={s.kind} />
                          {s.uploaded && <span className="text-xs text-muted-foreground">업로드</span>}
                        </div>
                      </TableCell>
                      <TableCell className="hidden max-w-72 lg:table-cell">
                        <span className="block truncate font-mono text-xs" title={s.locator}>{s.locator}</span>
                      </TableCell>
                      <TableCell className="hidden md:table-cell">{sourceState(s)}</TableCell>
                      <TableCell className="hidden sm:table-cell"><LastRun s={s} /></TableCell>
                      <TableCell className="pr-4 text-right">
                        <Button variant="ghost" size="icon" aria-label={`${s.name} 삭제`}
                                data-delete={s.name} disabled={!!s.active_run}
                                onClick={() => setRemoving(s)}>
                          <Trash2 aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      <NewSourceDialog open={adding} onOpenChange={setAdding} />
      <DeleteSourceDialog key={removing?.name ?? ""} source={removing} onClose={() => setRemoving(null)} />
    </>
  )
}

type Kind = SourceKind
type Mode = "upload" | "path"

export function NewSourceDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState("")
  const [kind, setKind] = useState<Kind>("files")
  const [mode, setMode] = useState<Mode>("upload")
  const [files, setFiles] = useState<File[]>([])
  const [locator, setLocator] = useState("")
  const [recursive, setRecursive] = useState(false)
  const [error, setError] = useState("")
  const effectiveMode: Mode = kind === "database" ? "path" : mode

  const reset = () => {
    setName(""); setKind("files"); setMode("upload"); setFiles([]); setLocator("")
    setRecursive(false); setError("")
  }

  const status = useStatus()
  const limitMb = status.data?.upload_max_mb ?? DEFAULT_UPLOAD_MAX_MB
  const totalBytes = files.reduce((sum, f) => sum + f.size, 0)
  const tooLarge = totalBytes > limitMb * 1024 * 1024

  const add = useMutation({
    mutationFn: () => {
      if (effectiveMode === "upload") {
        if (!files.length) throw new Error("파일을 하나 이상 선택하세요")
        if (tooLarge) {
          throw new Error(`파일 합계 ${fmtBytes(totalBytes)}가 한도 ${limitMb} MB를 넘습니다 (AUTOONTOLOGY_UPLOAD_MAX_MB)`)
        }
        return api.addSourceUpload(name.trim(), kind as "files" | "documents", files)
      }
      return api.addSourceLocator({ name: name.trim(), kind, locator: locator.trim(), recursive })
    },
    onSuccess: (s) => {
      toast.success(`소스 '${s.name}'를 등록했습니다`)
      qc.invalidateQueries({ queryKey: keys.sources })
      onOpenChange(false)
      reset()
    },
    onError: (e) => setError(errorMessage(e)),
  })

  const submit = (ev: FormEvent) => {
    ev.preventDefault()
    setError("")
    add.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4" id="form-source">
          <DialogHeader>
            <DialogTitle>새 소스</DialogTitle>
            <DialogDescription>정형 파일·문서를 올리거나 디렉터리 경로·DB URL을 지정합니다.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="source-name">이름</Label>
            <Input id="source-name" required value={name} onChange={(e) => setName(e.target.value)}
                   pattern="[a-z0-9][a-z0-9_\-]*" autoComplete="off"
                   placeholder="예: hanbit (정형은 소문자·숫자·_, 문서는 - 도 가능)" />
          </div>
          <div className="grid gap-2">
            <Label>종류</Label>
            <Tabs value={kind} onValueChange={(v) => setKind(v as Kind)}>
              <TabsList className="w-full">
                <TabsTrigger value="files">정형 파일</TabsTrigger>
                <TabsTrigger value="documents">문서</TabsTrigger>
                <TabsTrigger value="database">DB URL</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          {kind !== "database" && (
            <div className="grid gap-2">
              <Label>가져오기</Label>
              <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
                <TabsList className="w-full">
                  <TabsTrigger value="upload">파일 업로드</TabsTrigger>
                  <TabsTrigger value="path">디렉터리 경로</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
          )}
          {effectiveMode === "upload" ? (
            <div className="grid gap-2">
              <Label htmlFor="source-files">파일</Label>
              <Input id="source-files" type="file" multiple accept={ACCEPT[kind as "files" | "documents"]}
                     onChange={(e) => setFiles([...(e.target.files ?? [])])} />
              <p className={tooLarge ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
                 id="upload-limit">
                {files.length > 0 && `${files.length}개 선택됨 · 합계 ${fmtBytes(totalBytes)} · `}
                한 번에 최대 {limitMb} MB
              </p>
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="source-locator">위치</Label>
                <Input id="source-locator" required value={locator}
                       onChange={(e) => setLocator(e.target.value)} autoComplete="off"
                       className="font-mono text-xs"
                       placeholder={kind === "database" ? "postgresql://user:pass@host:5432/db"
                         : "/path/to/data (절대 경로)"} />
              </div>
              {kind !== "database" && (
                <div className="flex items-center gap-2">
                  <Switch id="source-recursive" checked={recursive} onCheckedChange={setRecursive} />
                  <Label htmlFor="source-recursive">하위 폴더 포함</Label>
                </div>
              )}
            </>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>취소</Button>
            <Button type="submit" disabled={add.isPending || (effectiveMode === "upload" && tooLarge)}>
              {add.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {add.isPending ? "등록 중…" : "등록"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteSourceDialog({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [dropDb, setDropDb] = useState(false)
  const remove = useMutation({
    mutationFn: (s: Source) => api.deleteSource(s.name, s.kind === "files" && dropDb),
    onSuccess: (r) => {
      toast.success(`소스 '${r.removed}'를 삭제했습니다`, {
        description: r.messages.length ? r.messages.join(" · ") : undefined,
      })
      setDropDb(false)  // a prop-driven close fires no onOpenChange
      onClose()
    },
    onError: (e) => toast.error("삭제 실패", { description: errorMessage(e) }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.sources }),
  })
  const what = source?.kind === "documents" ? "문서·청크·개념·관계·근거"
    : "upstream 연결과 인제스트된 그래프, 실행 기록"
  return (
    <AlertDialog open={!!source} onOpenChange={(o) => { if (!o && !remove.isPending) { onClose(); setDropDb(false) } }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>소스 '{source?.name}'를 삭제할까요?</AlertDialogTitle>
          <AlertDialogDescription>
            {what}가 함께 삭제됩니다. 되돌릴 수 없습니다.
            {remove.isPending && " 그래프 정리를 기다리는 동안 최대 2분 걸릴 수 있습니다."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {source?.kind === "files" && (
          <div className="flex items-start gap-3 rounded-md border p-3">
            <Switch id="drop-db" checked={dropDb} onCheckedChange={setDropDb} />
            <Label htmlFor="drop-db" className="grid gap-1 leading-snug">
              <span>데이터베이스도 삭제(DROP)</span>
              <span className="font-mono text-xs font-normal text-muted-foreground">{source.database}</span>
            </Label>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>취소</AlertDialogCancel>
          <Button variant="destructive" disabled={remove.isPending}
                  onClick={() => source && remove.mutate(source)}>
            {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {remove.isPending ? "삭제 중…" : "삭제"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
