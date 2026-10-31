import { useState } from "react"
import { useQuery,useMutation,useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription } from "@/components/ui/dialog"
import { request,errorMessage } from "@/lib/api"
import { useCanUse,useIdentity } from "@/lib/identity"
import { useT } from "@/i18n"
interface Root {path:string;active:number;reason:string;updated_at:string}
export function HostRoots(){
 const t=useT(),identity=useIdentity(),allowed=useCanUse("identity.admin"),qc=useQueryClient()
 const query=useQuery({queryKey:["host-roots"],queryFn:()=>request<{enforced:boolean;defaults:string[];roots:Root[]}>("/host-files"),enabled:allowed})
 const [task,setTask]=useState<"allow"|"revoke"|null>(null),[path,setPath]=useState(""),[reason,setReason]=useState(""),[ack,setAck]=useState(false)
 const mutation=useMutation({mutationFn:()=>request(`/host-files/${task}`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({path,reason,confirm:true})}),onSuccess:()=>{setTask(null);qc.invalidateQueries({queryKey:["host-roots"]});qc.invalidateQueries({queryKey:["ops","freshness"]})}})
 if(!allowed)return null
 const open=(value:"allow"|"revoke",root="")=>{mutation.reset();setTask(value);setPath(root);setReason("");setAck(false)}
 return <details className="my-5 border-y py-4" id="host-root-policy"><summary className="cursor-pointer text-sm font-medium">{t("host.title")}</summary><div className="mt-4 space-y-4"><p className="max-w-3xl text-xs leading-6 text-muted-foreground">{t("host.hint")}</p><p className="text-xs">{t(query.data?.enforced?"host.enforced":"host.legacy")}</p>{query.error&&<p role="alert" className="text-xs text-destructive">{errorMessage(query.error)}</p>}<ul className="space-y-2 text-xs">{query.data?.defaults.map(root=><li key={root} className="break-all">{t("host.default")} · {root}</li>)}{query.data?.roots.map(root=><li key={root.path} className="flex flex-wrap items-center justify-between gap-3 border-t pt-2"><span className="break-all">{root.path} · {t(root.active?"host.allowed":"host.revoked")} · {root.reason}</span>{!!root.active&&<Button size="sm" variant="ghost" onClick={()=>open("revoke",root.path)}>{t("host.revoke")}</Button>}</li>)}</ul><Button variant="outline" size="sm" disabled={identity?.session.principal.kind!=="human"} onClick={()=>open("allow")}>{t("host.allow")}</Button><p className="text-xs text-muted-foreground">{t("host.retained")}</p></div><Dialog open={!!task} onOpenChange={open=>{if(!open&&!mutation.isPending)setTask(null)}}><DialogContent><DialogHeader><DialogTitle>{t(task==="allow"?"host.allow":"host.revoke")}</DialogTitle><DialogDescription>{t(task==="allow"?"host.allowHint":"host.revokeHint")}</DialogDescription></DialogHeader><label className="grid gap-2 text-sm">{t("host.path")}<Input id="host-root-path" value={path} disabled={task==="revoke"} onChange={e=>setPath(e.target.value)}/></label><label className="grid gap-2 text-sm">{t("host.reason")}<Textarea id="host-root-reason" value={reason} onChange={e=>setReason(e.target.value)}/></label><label className="flex gap-2 text-sm"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>{t("host.ack")}</label>{mutation.error&&<p role="alert" className="text-sm text-destructive">{errorMessage(mutation.error)}</p>}<Button disabled={mutation.isPending||!ack||!path.trim()||!reason.trim()} onClick={()=>mutation.mutate()}>{t("host.apply")}</Button></DialogContent></Dialog></details>
}
