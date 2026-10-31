import { useMutation,useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useNavigate } from "react-router"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription } from "@/components/ui/dialog"
import { axApi } from "@/lib/ax"
import { errorMessage } from "@/lib/api"
import { useCanUse,useIdentity } from "@/lib/identity"
import { useT } from "@/i18n"
export function FeedbackCaseButton({id,question}:{id:number;question:string}){
 const t=useT(),qc=useQueryClient(),navigate=useNavigate(),identity=useIdentity(),allowed=useCanUse("review.suggest")
 const [open,setOpen]=useState(false),[title,setTitle]=useState(question.slice(0,200)),[reason,setReason]=useState("")
 const mutation=useMutation({mutationFn:()=>axApi.feedbackCase(id,title,reason,identity?.session.principal.kind==="human"?identity.session.principal.id:null),onSuccess:value=>{setOpen(false);qc.invalidateQueries({queryKey:["cases"]});navigate(`/review?tab=cases&case=${value.id}`)}})
 if(!allowed)return null
 return <><Button size="sm" variant="outline" onClick={()=>{mutation.reset();setOpen(true)}}>{t("ax.prepareFeedbackCase")}</Button><Dialog open={open} onOpenChange={value=>{if(!mutation.isPending)setOpen(value)}}><DialogContent><DialogHeader><DialogTitle>{t("ax.prepareFeedbackCase")}</DialogTitle><DialogDescription>{t("ax.feedbackCaseHint")}</DialogDescription></DialogHeader><p className="text-sm">{question}</p><label className="grid gap-2 text-sm">{t("cases.titleField")}<Input id="feedback-case-title" maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label><label className="grid gap-2 text-sm">{t("ax.reason")}<Textarea id="feedback-case-reason" value={reason} maxLength={3000} onChange={e=>setReason(e.target.value)}/></label>{mutation.error&&<p role="alert" className="text-sm text-destructive">{errorMessage(mutation.error)}</p>}<Button disabled={mutation.isPending||!title.trim()||!reason.trim()} onClick={()=>mutation.mutate()}>{t("cases.create")}</Button></DialogContent></Dialog></>
}
