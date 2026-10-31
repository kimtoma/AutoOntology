import { useT } from "@/i18n"
export interface ModelChecks { ok:boolean; assertions?:{id:string;subject:string;field:string;expected:unknown;actual:unknown;ok:boolean}[]; rules?:{conflicts:{subjects:string[];reason:string}[];unsupported:{subject:string;reason:string}[];invalid:{subject:string;reason:string}[]} }
export function ModelCheckResults({result}:{result?:ModelChecks}){
 const t=useT()
 if(!result)return null
 return <section className="my-3 space-y-3 border-y py-3 text-xs"><h4 className="font-medium">{t("quality.assertions")} · {t(result.ok?"ops.pass":"ops.fail")}</h4>{result.assertions?.map(row=><div key={row.id} className="space-y-1"><p>{t(row.ok?"ops.pass":"ops.fail")} · {row.subject} · {row.field}</p><p className="text-muted-foreground">{t("quality.expected")} · {String(row.expected??"—")} / {t("quality.actual")} · {String(row.actual??"—")}</p></div>)}{result.rules?.conflicts.map((row,index)=><p key={`conflict-${index}`} className="text-destructive">{t("quality.conflict")} · {row.subjects.join(" / ")}</p>)}{[...(result.rules?.unsupported??[]),...(result.rules?.invalid??[])].map((row,index)=><p key={`invalid-${index}`} className="text-destructive">{row.subject} · {row.reason}</p>)}</section>
}
