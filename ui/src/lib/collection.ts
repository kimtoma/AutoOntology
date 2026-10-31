import { request } from "@/lib/api"
export interface Schedule {id:string;source_name:string;worker_id:string;owner_id:string;interval_s:number;mode:"observe"|"prepare";enabled:number;expires_at:number;next_at:number;sequence:number;version:number;policy_version:number;reason:string;cursor:Record<string,unknown>}
export interface CollectionJob {id:string;schedule_id:string;sequence:number;status:string;attempt:number;generation:number;next_at:number;lease_expired:boolean;error_code:string|null;run_id:string|null;input:Record<string,unknown>;result:{mode?:string;candidate_revision?:number;run_id?:string;files?:number;rows?:number;publication_performed?:boolean}}
export interface Notice {id:string;schedule_id:string;job_id:string;kind:string;created_at:number;acknowledged_at:number|null}
export interface CollectionState {schedules:Schedule[];jobs:CollectionJob[];notices:Notice[];workers:{principal_id:string;name:string;kind:string;last_at:number}[]}
export interface CollectionInput {source:string;worker_id:string;interval:number;mode:"observe"|"prepare";reason:string;max_attempts:number;ttl:number}
const send=(body?:unknown):RequestInit=>({method:"POST",headers:{"content-type":"application/json"},...(body?{body:JSON.stringify(body)}:{})})
export const collectionApi={
 overview:()=>request<CollectionState>("/collection"),
 summary:()=>request<{active:number;waiting:number;attention:number;unread:number}>("/collection/summary"),
 workers:()=>request<{items:{id:string;name:string;kind:string}[]}>("/collection/workers"),
 configure:(input:CollectionInput)=>request("/collection",send({...input,confirm:true})),
 change:(path:string,reason:string)=>request(`/collection/${path}`,send({reason,confirm:true})),
 poll:(id:string)=>request(`/collection/${id}/poll`,send()),
 work:()=>request("/collection/work",send()),
 ack:(id:string)=>request(`/collection/notices/${id}/ack`,send()),
}
