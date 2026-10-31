import { request } from "@/lib/api"
export interface CaseContextPayload {resolution_intent?:"knowledge_change"|"no_change";workflow:string;complexity:string;effective_at:string|null;required_suites:{id:string;sha256:string}[];feedback_ids:number[];measurement_scope:"business"|"simulation"}
export interface ContextVersion {id:string;case_id:string;version:number;previous_id:string|null;payload:CaseContextPayload;sha256:string;reason:string;created_at:string}
export interface Effort {id:string;case_id:string;status:string;started_at:number;finished_at:number|null;elapsed_seconds:number|null;start_note:string;stop_note:string|null}
export interface Baseline {id:string;workflow:string;complexity:string;lead_seconds:number;active_seconds:number;source:"observed"|"reported";reference:string;measurement_scope:"business"|"simulation";voided?:boolean;void_reason?:string|null;created_at?:string}
export interface BaselineInput extends Omit<Baseline,"id"> {request_key:string}
export interface AXTarget {workflow:string;complexity:string;lead_reduction_percent:number;effort_reduction_percent:number;reason:string;baseline_sha256:string}
interface Distribution {count:number;median:number|null;p90:number|null}
export interface AXCohort {workflow:string;complexity:string;measurement_scope:string;baseline:{observed_count:number;reported_count:number;lead:Distribution;effort:Distribution};baseline_sha256:string|null;assisted:{lead:Distribution;effort:Distribution};time_returned_seconds:number|null;target:{id:string;payload:AXTarget}|null;target_stale:boolean;active_cases:number;cancelled_cases:number;completed_cases:number;cases:{case_id:string;title:string;status:string;lead_seconds:number|null;recorded_effort_seconds:number|null;effort_attested:boolean;running_timers:number;discarded_timers:number;elapsed_seconds:number}[]}
export interface ContextDetail {current:ContextVersion|null;versions:ContextVersion[];feedback:{id:number;question:string;answer:string;note:string;resolved:number}[];effort_summary:{stopped_count:number;running_count:number;discarded_count:number;recorded_seconds:number;attested:boolean}|null}
const send=(method:string,value:unknown):RequestInit=>({method,headers:{"content-type":"application/json"},body:JSON.stringify(value)})
const path=(id:string)=>`/ax/cases/${encodeURIComponent(id)}`
export const axApi={
 overview:()=>request<{cohorts:AXCohort[];voided_baselines:number}>("/ax"),
 context:(id:string)=>request<ContextDetail>(path(id)),
 setContext:(id:string,payload:CaseContextPayload,previous:string|null,reason:string)=>request<ContextVersion>(path(id),send("PUT",{payload,previous,reason})),
 efforts:()=>request<{items:Effort[]}>("/ax/efforts"),
 start:(case_id:string,note:string)=>request<Effort>("/ax/efforts",send("POST",{case_id,note})),
 stop:(id:string,note:string,discard=false)=>request<Effort>(`/ax/efforts/${id}/stop`,send("POST",{note,discard})),
 attest:(id:string,reason:string)=>request(path(id)+"/attest-effort",send("POST",{reason,confirm:true})),
 baselines:()=>request<{items:Baseline[]}>("/ax/baselines"),
 addBaseline:(payload:BaselineInput)=>request<Baseline>("/ax/baselines",send("POST",{payload,confirm:true})),
 voidBaseline:(id:string,reason:string)=>request(`/ax/baselines/${id}/void`,send("POST",{reason,confirm:true})),
 target:(payload:AXTarget)=>request("/ax/targets",send("POST",{payload,confirm:true})),
 feedbackCase:(id:number,title:string,reason:string,owner_id:string|null)=>request<{id:string}>(`/ax/feedback/${id}/case`,send("POST",{title,reason,owner_id})),
 resolveFeedback:(id:number,case_id:string)=>request(`/ax/feedback/${id}/resolve`,send("POST",{case_id,confirm:true})),
}

export const secondsText=(seconds:number|null|undefined)=>seconds==null?"—":`${Math.round(seconds/60*10)/10} min`
