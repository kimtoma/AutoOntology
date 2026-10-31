import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useT, type MessageKey } from "@/i18n"
import { type MonetaryRule } from "@/lib/rules"
export function RuleFields({ value, onChange }: { value: MonetaryRule; onChange: (value: MonetaryRule) => void }) {
 const t=useT()
 const field=(key:keyof MonetaryRule,next:unknown)=>onChange({...value,[key]:next})
 return <fieldset className="grid gap-4 border-y py-4"><legend className="px-1 text-sm font-medium">{t("quality.ruleFields")}</legend><p className="text-xs leading-6 text-muted-foreground sm:col-span-2">{t("quality.ruleHint")}</p><div className="grid gap-3 sm:grid-cols-2">{(["scope","measure","amount","currency","authority","effective_from","effective_to","priority"] as const).map(key=><label className="grid gap-1.5 text-xs" key={key}>{t(`quality.rule.${key}` as MessageKey)}<Input id={`rule-${key}`} value={value[key]??""} required={!["effective_to","priority"].includes(key)} type={key.startsWith("effective_")?"date":key==="priority"?"number":"text"} maxLength={key==="currency"?3:200} min={key==="priority"?0:undefined} max={key==="priority"?1000:undefined} onChange={event=>field(key,key==="priority"?Number(event.target.value):key==="effective_to"?event.target.value||null:event.target.value)}/></label>)}<label className="grid gap-1.5 text-xs">{t("quality.rule.operator")}<select id="rule-operator" className="rounded border bg-background p-2" value={value.operator} onChange={event=>field("operator",event.target.value)}>{["gt","gte","lt","lte","eq"].map(op=><option key={op} value={op}>{t(`quality.operator.${op}` as MessageKey)}</option>)}</select></label></div><label className="grid gap-1.5 text-xs">{t("quality.rule.exceptions")}<Textarea id="rule-exceptions" value={value.exceptions.join("\n")} onChange={event=>field("exceptions",event.target.value.split("\n").filter(Boolean))}/></label></fieldset>
}

export function RuleSummary({value}:{value:Record<string,unknown>}) {
 const t=useT()
 return <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">{Object.entries(value).map(([key,field])=><div key={key}><dt className="text-muted-foreground">{t(`quality.rule.${key}` as MessageKey)}</dt><dd className="mt-1 break-words">{Array.isArray(field)?field.join(" / "):String(field??"—")}</dd></div>)}</dl>
}
