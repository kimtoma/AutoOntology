import { useT } from "@/i18n"

export function ScopeNotice({ access }: { access?: { restricted: boolean; historical_scope_missing?: boolean } }) {
  const t = useT()
  if (!access?.restricted) return null
  return <div role="status" className="mb-5 border-l-2 border-border py-1 pl-3 text-xs leading-6 text-muted-foreground"><strong className="block font-medium text-foreground">{t("identity.scope.title")}</strong>{t("identity.scope.hint")}{access.historical_scope_missing && <p>{t("identity.scope.legacy")}</p>}</div>
}
