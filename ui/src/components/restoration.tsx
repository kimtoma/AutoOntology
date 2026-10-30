import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Loader2 } from "lucide-react"
import { OriginalLink } from "@/components/original-link"
import { ErrorState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { VersionDiff } from "@/components/version-diff"
import { operations, type Restoration } from "@/lib/operations"
import { errorMessage } from "@/lib/api"
import { fmtTime } from "@/lib/labels"
import { useT } from "@/i18n"

function RestoreSheet({ revision, saved, onClose, onRun }: { revision:number; saved?:Restoration; onClose:()=>void; onRun:(id:string)=>void }) {
  const t=useT(), qc=useQueryClient()
  const [visibleChanges,showChanges]=useState(50)
  const [reason,setReason]=useState(saved?.packet.reason ?? ""), [draftPacket,setPacket]=useState<Restoration|undefined>(), [confirm,setConfirm]=useState(false)
  const prepare=useMutation({mutationFn:()=>operations.prepareRestore(revision,reason.trim()),onSuccess:r=>{setPacket(r);setConfirm(false);qc.invalidateQueries({queryKey:["restorations"]})}})
  const execute=useMutation({mutationFn:()=>operations.restore((saved ?? draftPacket)!.id,(saved ?? draftPacket)!.confirmation),onSuccess:r=>{qc.invalidateQueries({queryKey:["restorations"]});onClose();onRun(r.run_id)}})
  const packet=saved ?? draftPacket
  const p=packet?.packet, validation=p?.validation
  const fieldLabels:Record<string,string>={definitions:t("ops.field.definitions"),label:t("ops.field.label"),comment:t("restore.field.comment"),derived_from:t("restore.field.evidence")}
  const expired=!!p?.expires_at && Date.now()>=Date.parse(p.expires_at)
  const ready=packet?.status==="ready" && validation?.ok && !expired
  return <Sheet open onOpenChange={open=>{if(!open)onClose()}}>
    <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader><SheetTitle>{t("restore.title",{n:revision})}</SheetTitle><SheetDescription>{t("restore.hint")}</SheetDescription></SheetHeader>
      <div className="space-y-5 px-4 pb-6 text-sm">
        {!packet && <form className="grid gap-3" onSubmit={e=>{e.preventDefault();prepare.mutate()}}>
          <label className="grid gap-2">{t("restore.reason")}<Textarea value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000} /></label>
          <Button disabled={!reason.trim()||prepare.isPending}>{prepare.isPending && <Loader2 aria-hidden className="animate-spin" />}{t("restore.validate")}</Button>
        </form>}
        {prepare.isPending && <p role="status">{t("restore.validating")}</p>}
        {packet && p && <>
          <p className="font-medium">{t("restore.effect",{base:p.base_revision,target:p.target_revision})}</p>
          <p>{p.reason}</p>
          <p className="text-warning">{t("restore.liveSql")}</p>
          <p className="text-muted-foreground">{t("restore.reviews")}</p>
          {p.expires_at && <p className="text-xs text-muted-foreground">{t("restore.expires")} · {fmtTime(p.expires_at)}</p>}
          {p.diff && <VersionDiff value={{from:p.base_revision,to:p.target_revision,...p.diff}} />}
          {!!p.changes?.length && <section><h3 className="font-medium">{t("restore.changedValues")}</h3><ul className="mt-3 divide-y">{p.changes.slice(0,visibleChanges).map(change=><li key={change.collection+change.key} className="py-4"><h4 className="mb-3 font-medium">{change.label}</h4>{change.fields.map(field=><div key={field.name} className="mt-3"><p className="mb-2 text-xs text-muted-foreground">{fieldLabels[field.name] ?? field.name}</p><div className="grid gap-3 sm:grid-cols-2"><div><p className="text-xs font-medium">{t("restore.currentValue")}</p><pre className="mt-1 whitespace-pre-wrap break-words bg-muted/50 p-2 text-xs">{typeof field.before === "string" ? field.before : JSON.stringify(field.before,null,2) ?? "—"}</pre></div><div><p className="text-xs font-medium">{t("restore.targetValue")}</p><pre className="mt-1 whitespace-pre-wrap break-words bg-muted/50 p-2 text-xs">{typeof field.after === "string" ? field.after : JSON.stringify(field.after,null,2) ?? "—"}</pre></div></div></div>)}</li>)}</ul>{p.changes.length>visibleChanges && <Button variant="outline" onClick={()=>showChanges(n=>n+50)}>{t("restore.moreChanges")}</Button>}</section>}
          {!!p.originals?.length && <section><h3 className="font-medium">{t("restore.originals")}</h3><ul className="mt-2 divide-y">{p.originals.map(doc=><li key={doc.revision+doc.document_id} className="py-3"><p>{doc.title} · r{doc.revision}</p><OriginalLink original={doc.original} /></li>)}</ul></section>}
          <ul className="divide-y">{validation?.questions.map(q=><li key={q.id} className="py-3"><Badge variant="outline">{t(q.ok?"ops.pass":"ops.fail")}</Badge> {q.question}<p className="mt-2 whitespace-pre-wrap">{q.answer}</p></li>)}</ul>
          {packet.status==="blocked" && <p role="alert">{t("restore.blocked")}</p>}
          {expired && packet.status==="ready" && <p role="alert">{t("restore.expired")}</p>}
          {packet.status==="failed" && <p role="alert">{t("restore.failed")}</p>}
          {packet.status==="preparing" && <p role="status">{t("restore.validating")}</p>}
          {packet.status==="committed" && <p role="status">{t("restore.completed",{n:packet.result_revision??"—"})}</p>}
          {ready && <div className="border-t pt-4">
            {!confirm ? <Button variant="outline" onClick={()=>setConfirm(true)}>{t("restore.reviewEffect")}</Button> : <div className="space-y-3">
              <p>{t("restore.confirmHint",{base:p.base_revision,target:p.target_revision})}</p>
              <div className="flex gap-2"><Button disabled={execute.isPending} onClick={()=>execute.mutate()}>{execute.isPending && <Loader2 aria-hidden className="animate-spin" />}{t("restore.confirm")}</Button><Button variant="outline" disabled={execute.isPending} onClick={()=>setConfirm(false)}>{t("common.cancel")}</Button></div>
            </div>}
          </div>}
          <details><summary className="cursor-pointer">{t("restore.packet")}</summary><pre className="mt-2 max-h-96 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(packet,null,2)}</pre></details>
        </>}
        {(prepare.error||execute.error) && <p role="alert" className="text-destructive">{errorMessage(prepare.error||execute.error)}</p>}
      </div>
    </SheetContent>
  </Sheet>
}

export function RestoreButton({revision,onRun}:{revision:number;onRun:(id:string)=>void}) {
  const t=useT(), [open,setOpen]=useState(false)
  return <><Button size="sm" variant="outline" onClick={()=>setOpen(true)}>{t("restore.prepare")}</Button>{open && <RestoreSheet revision={revision} onClose={()=>setOpen(false)} onRun={onRun} />}</>
}

export function RestorationHistory({onRun}:{onRun:(id:string)=>void}) {
  const t=useT(), [selected,setSelected]=useState<Restoration>()
  const history=useQuery({queryKey:["restorations"],queryFn:operations.restorations,refetchInterval:5000})
  if(history.error)return <ErrorState error={history.error} onRetry={()=>history.refetch()} />
  if(!history.data?.items.length)return null
  return <section className="mt-7"><h2 className="mb-3 font-semibold">{t("restore.history")}</h2><ul className="divide-y">{history.data.items.map(item=><li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"><div><p>r{item.packet.base_revision} → r{item.packet.target_revision} · {t(`restore.status.${item.status}`)}</p><p className="mt-1 text-muted-foreground">{item.packet.reason}</p></div><Button variant="ghost" size="sm" onClick={()=>setSelected(item)}>{t("restore.inspect")}</Button></li>)}</ul>{selected && <RestoreSheet key={selected.id} revision={selected.packet.target_revision} saved={history.data.items.find(item=>item.id===selected.id) ?? selected} onClose={()=>setSelected(undefined)} onRun={onRun} />}</section>
}
