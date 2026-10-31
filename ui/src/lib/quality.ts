import type { ModelChecks } from "@/components/model-check-results"
import { request } from "@/lib/api"
export interface EvaluationQuestion { id: string; question: string; expected: string; mode: string; forbidden: string[]; minimum_citations: number }
export interface EvaluationAssertion { id: string; collection: string; subject: string; field: string; expected: string | number | null }
export interface EvaluationPayload { title: string; description: string; questions: EvaluationQuestion[]; assertions: EvaluationAssertion[] }
export interface EvaluationVersion { id: string; suite_key: string; version: number; previous_id: string | null; sha256: string; actor: string; created_at: string; payload: EvaluationPayload }
const send=(value:unknown):RequestInit=>({method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(value)})
export const qualityApi={
 list:()=>request<{active:EvaluationVersion[];versions:EvaluationVersion[]}>("/quality"),
 prepare:(key:string,previous:string|null,payload:EvaluationPayload)=>request<EvaluationVersion>("/quality/versions",send({key,previous,payload})),
 activate:(item:EvaluationVersion)=>request<EvaluationVersion>(`/quality/versions/${item.id}/activate`,send({sha256:item.sha256,confirm:true})),
 deactivate:(item:EvaluationVersion)=>request<EvaluationVersion>(`/quality/versions/${item.id}/deactivate`,send({sha256:item.sha256,confirm:true})),
 runs:()=>request<{items:{id:string;revision:number;created_at:string;result:{ok:boolean;questions:{id:string;question:string;answer:string;ok:boolean}[];model_checks?:ModelChecks}}[]}>("/quality/runs"),
 rules:()=>request<{revision:number|null;items:{id:string;label:string;rule:Record<string,unknown>}[];checks:{ok:boolean;typed_rules:number;unstructured_legacy_rules:number;conflicts:{subjects:string[];reason:string}[];unsupported:{subject:string;reason:string}[]}|null}>("/quality/rules"),
}
