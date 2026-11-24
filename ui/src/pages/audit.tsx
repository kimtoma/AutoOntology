/* The audit log: `/p/:project/audit` (the project's owners) and `/users/audit` (admins: the
   whole installation). Newest first, filters by action and user, "Load more" pages with
   `before_id`. */

import { useInfiniteQuery } from "@tanstack/react-query"
import { Loader2, ScrollText } from "lucide-react"
import { useState, type FormEvent } from "react"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { NativeSelect } from "@/components/native-select"
import { PageHeader } from "@/components/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { fmtDate, useT } from "@/i18n"
import { AUDIT_ACTIONS, AUDIT_PAGE_SIZE, authApi, detailSummary, useCan, type AuditEntry } from "@/lib/auth"
import { projectName, useProject } from "@/lib/project"
import { UsersTabs } from "@/pages/users"

function EntryRow({ e, showProject }: { e: AuditEntry; showProject: boolean }) {
  const t = useT()
  const detail = detailSummary(e.detail)
  const cols = showProject
    ? "md:grid-cols-[10rem_minmax(0,1.2fr)_9rem_minmax(0,1fr)_6rem_minmax(0,1.4fr)]"
    : "md:grid-cols-[10rem_minmax(0,1.2fr)_9rem_minmax(0,1fr)_minmax(0,1.4fr)]"
  return (
    <li data-audit={e.id} className={`grid gap-1 rounded-lg border p-3 text-sm md:items-baseline md:gap-3 md:rounded-none
                                      md:border-0 md:border-t md:px-1 md:py-2 ${cols}`}>
      <time dateTime={e.at} className="text-xs text-muted-foreground tabular-nums">{fmtDate(e.at)}</time>
      <span className="truncate" title={e.actor}><span className="sr-only">{t("audit.col.actor")}: </span>{e.actor}</span>
      <span><Badge variant="outline" className="font-mono text-[11px]">{e.action}</Badge></span>
      <span className="truncate text-muted-foreground" title={e.target}>
        <span className="sr-only">{t("audit.col.target")}: </span>{e.target || "—"}
      </span>
      {showProject && <span className="truncate font-mono text-xs text-muted-foreground">{e.project ?? "—"}</span>}
      <span className="min-w-0 break-words text-xs text-muted-foreground" title={detail}>{detail || "—"}</span>
    </li>
  )
}

/** The audit table of *project* (null: the whole installation). */
export function AuditLog({ project }: { project: string | null }) {
  const t = useT()
  const [action, setAction] = useState("")
  const [userText, setUserText] = useState("")
  const [user, setUser] = useState("")
  const query = { project, action, user }
  const pages = useInfiniteQuery({
    queryKey: keys.audit(query),
    queryFn: ({ pageParam }) => authApi.audit({ ...query, before_id: pageParam, limit: AUDIT_PAGE_SIZE }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before_id,
  })
  const entries = pages.data?.pages.flatMap((p) => p.entries) ?? []
  const showProject = project === null
  const apply = (e: FormEvent) => { e.preventDefault(); setUser(userText.trim()) }
  const clear = () => { setAction(""); setUser(""); setUserText("") }
  return (
    <div className="grid min-w-0 gap-4">
      <form onSubmit={apply} className="grid gap-3 sm:flex sm:flex-wrap sm:items-end" id="audit-filters">
        <div className="grid gap-2 sm:w-56">
          <Label htmlFor="audit-action">{t("audit.filter.action")}</Label>
          <NativeSelect id="audit-action" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">{t("audit.filter.action.all")}</option>
            {AUDIT_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
          </NativeSelect>
        </div>
        <div className="grid gap-2 sm:w-72">
          <Label htmlFor="audit-user">{t("audit.filter.user")}</Label>
          <Input id="audit-user" type="search" value={userText} autoComplete="off" spellCheck={false} maxLength={320}
                 onChange={(e) => setUserText(e.target.value)} />
        </div>
        <Button type="submit" variant="secondary" id="btn-audit-filter">{t("audit.filter.apply")}</Button>
        {(action || user) && <Button type="button" variant="ghost" onClick={clear}>{t("audit.filter.clear")}</Button>}
      </form>
      <Card className="gap-3 py-4" id="audit-log">
        <CardContent className="px-4">
          {pages.error ? <ErrorState error={pages.error} onRetry={() => pages.refetch()} />
            : pages.isPending ? <Skeleton className="h-40" />
            : !entries.length ? <EmptyState icon={ScrollText} title={t("audit.empty")} />
            : (
              <>
                <div aria-hidden className={`hidden gap-3 px-1 pb-2 text-xs text-muted-foreground md:grid ${showProject
                  ? "md:grid-cols-[10rem_minmax(0,1.2fr)_9rem_minmax(0,1fr)_6rem_minmax(0,1.4fr)]"
                  : "md:grid-cols-[10rem_minmax(0,1.2fr)_9rem_minmax(0,1fr)_minmax(0,1.4fr)]"}`}>
                  <span>{t("audit.col.time")}</span><span>{t("audit.col.actor")}</span><span>{t("audit.col.action")}</span>
                  <span>{t("audit.col.target")}</span>{showProject && <span>{t("audit.col.project")}</span>}
                  <span>{t("audit.col.detail")}</span>
                </div>
                <ul className="grid gap-2 md:gap-0" aria-label={t("audit.title")}>
                  {entries.map((e) => <EntryRow key={e.id} e={e} showProject={showProject} />)}
                </ul>
                <div className="mt-3 flex justify-center">
                  {pages.hasNextPage ? (
                    <Button variant="outline" size="sm" id="btn-audit-more" disabled={pages.isFetchingNextPage}
                            onClick={() => pages.fetchNextPage()}>
                      {pages.isFetchingNextPage && <Loader2 className="animate-spin" aria-hidden />}
                      {t("audit.more")}
                    </Button>
                  ) : <p className="text-xs text-muted-foreground" id="audit-end">{t("audit.end")}</p>}
                </div>
              </>
            )}
        </CardContent>
      </Card>
    </div>
  )
}

export function ProjectAuditPage() {
  const t = useT()
  const scope = useProject()
  const allowed = useCan("audit.read")
  const name = projectName(scope?.project, scope?.slug)
  return (
    <>
      <PageHeader title={t("audit.title")} description={allowed ? t("audit.description.project", { name }) : undefined} />
      {allowed && scope?.slug ? <AuditLog project={scope.slug} /> : <EmptyState icon={ScrollText} title={t("audit.denied")} />}
    </>
  )
}

export function InstallationAuditPage() {
  const t = useT()
  const allowed = useCan("audit.read_all")
  return (
    <>
      <PageHeader title={t("users.title")} description={t("audit.description")} />
      <UsersTabs current="audit" />
      {allowed ? <AuditLog project={null} /> : <EmptyState icon={ScrollText} title={t("users.denied")} />}
    </>
  )
}
