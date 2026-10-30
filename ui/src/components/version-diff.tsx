import { Badge } from "@/components/ui/badge"
import { useT, type MessageKey } from "@/i18n"
type Item = { id?: string; name?: string; concept_name?: string; fields?: string[] }
type Bucket = {
  totals?: { added: number; removed: number; changed?: number }
  added?: Item[]
  removed?: Item[]
  changed?: Item[]
}
export function VersionDiff({ value }: { value: Record<string, unknown> }) {
  const t = useT()
  return (
    <section className="mt-6 rounded-xl border p-5">
      <h2 className="mb-4 font-semibold">
        {t("ops.difference")} · r{String(value.from)} → r{String(value.to)}
      </h2>
      <div className="grid gap-5">
        {[
          ["classes", "ops.concepts"],
          ["axioms", "ops.alignments"],
          ["merges", "ops.same"],
        ].map(([key, label]) => {
          const bucket = value[key] as Bucket | undefined
          if (!bucket) return null
          return (
            <div key={key}>
              <h3 className="mb-2 text-sm font-medium">{t(label as MessageKey)}</h3>
              <div className="mb-2 flex flex-wrap gap-2">
                {(["added", "changed", "removed"] as const).map((kind) => (
                  <Badge variant="outline" key={kind}>
                    {t(`ops.${kind}`)} · {bucket.totals?.[kind] ?? bucket[kind]?.length ?? 0}
                  </Badge>
                ))}
              </div>
              <ul className="grid gap-2">
                {(["added", "changed", "removed"] as const).flatMap((kind) =>
                  (bucket[kind] || [])
                    .filter((i) => i.name || i.concept_name)
                    .map((item, i) => (
                      <li key={`${kind}-${item.id || i}`} className="text-sm">
                        <span className="mr-2 text-muted-foreground">{t(`ops.${kind}`)}</span>
                        {item.name || item.concept_name}
                        {item.fields?.length ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {t("ops.diffFields")}:{" "}
                            {item.fields.map((f) => t(`ops.field.${f}` as MessageKey)).join(", ")}
                          </p>
                        ) : null}
                      </li>
                    )),
                )}
              </ul>
            </div>
          )
        })}
      </div>
    </section>
  )
}
