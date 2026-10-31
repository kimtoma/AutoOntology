import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LangToggle } from "@/components/lang-toggle"
import { useT } from "@/i18n"
import { ApiError } from "@/lib/api"
import { AUTHORITY_CHANGED, IDENTITY_REQUIRED } from "@/lib/auth-events"
import { identityApi, IdentityContext, useCanUse, useIdentity } from "@/lib/identity"

const statusKey = ["identity", "status"] as const
const meKey = ["identity", "me"] as const

export function IdentityGate({ children }: { children: ReactNode }) {
  const t = useT()
  const qc = useQueryClient()
  const [locked, setLocked] = useState(false)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const status = useQuery({ queryKey: statusKey, queryFn: ({ signal }) => identityApi.status(signal), retry: false })
  const configured = status.data?.configured === true
  const me = useQuery({ queryKey: meKey, queryFn: ({ signal }) => identityApi.me(signal), enabled: configured && !locked, retry: false, refetchInterval: 30_000 })

  const clearWork = () => {
    qc.removeQueries({ predicate: query => query.queryKey[0] !== "identity" })
  }
  useEffect(() => {
    const lock = () => {
      if (!configured) return
      setLocked(true)
      void qc.cancelQueries()
      qc.removeQueries({ predicate: query => query.queryKey[0] !== "identity" })
      qc.removeQueries({ queryKey: meKey, exact: true })
    }
    const refresh = () => { if (configured) void qc.invalidateQueries({ queryKey: meKey, exact: true }) }
    window.addEventListener(IDENTITY_REQUIRED, lock)
    window.addEventListener(AUTHORITY_CHANGED, refresh)
    return () => {
      window.removeEventListener(IDENTITY_REQUIRED, lock)
      window.removeEventListener(AUTHORITY_CHANGED, refresh)
    }
  }, [configured, qc])

  const login = useMutation({
    mutationFn: () => identityApi.login(username, password),
    onSettled: () => setPassword(""),
    onSuccess: async () => {
      await qc.cancelQueries()
      clearWork()
      qc.removeQueries({ queryKey: meKey, exact: true })
      setLocked(false)
      await qc.invalidateQueries({ queryKey: meKey, exact: true })
    },
  })
  const logout = useMutation({
    mutationFn: identityApi.logout,
    onSuccess: async () => {
      setLocked(true)
      await qc.cancelQueries()
      clearWork()
      qc.removeQueries({ queryKey: meKey, exact: true })
    },
  })

  if (status.isPending || (configured && !locked && me.isPending)) {
    return <main className="mx-auto max-w-sm px-6 py-24"><p className="text-sm text-muted-foreground" role="status">{t("identity.checking")}</p></main>
  }
  if (status.isError || (configured && !locked && me.isError && !(me.error instanceof ApiError && me.error.status === 401))) {
    return <main className="mx-auto max-w-md px-6 py-24"><p className="mb-6 text-sm">AutoOntology / Ontology Ops</p><h1 className="text-xl font-medium" role="alert">{t("identity.unavailable")}</h1><p className="my-4 text-sm text-muted-foreground">{t("identity.remedy")}</p><Button variant="outline" onClick={() => { void status.refetch(); void me.refetch() }}>{t("identity.retry")}</Button></main>
  }
  if (!configured) return children
  if (locked || !me.data || me.isError) {
    const submit = (event: FormEvent) => { event.preventDefault(); login.mutate() }
    const error = login.error instanceof ApiError
      ? login.error.status === 429 ? t("identity.cooldown", { seconds: Number(login.error.body?.retry_after) || 60 })
        : login.error.status === 401 ? t("identity.invalid") : t("identity.loginFailed")
      : login.error ? t("identity.loginFailed") : null
    return <main className="mx-auto min-h-svh w-full max-w-md px-7 py-16 sm:py-24">
      <div className="mb-14 flex items-center justify-between border-b border-border pb-5"><strong className="text-sm font-medium">AutoOntology</strong><LangToggle /></div>
      <p className="mb-3 text-xs text-muted-foreground">Ontology Ops</p>
      <h1 className="text-2xl font-medium tracking-tight">{t("identity.title")}</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{t("identity.hint")}</p>
      <form onSubmit={submit} className="mt-8 space-y-5" aria-label={t("identity.title")}>
        <div className="space-y-2"><Label htmlFor="identity-username">{t("identity.username")}</Label><Input id="identity-username" name="username" autoComplete="username" autoCapitalize="none" required maxLength={64} value={username} onChange={event => setUsername(event.target.value)} disabled={login.isPending} /></div>
        <div className="space-y-2"><Label htmlFor="identity-password">{t("identity.password")}</Label><Input id="identity-password" name="password" type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} disabled={login.isPending} /></div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button type="submit" className="w-full" disabled={login.isPending}>{t(login.isPending ? "identity.signingIn" : "identity.signIn")}</Button>
        {login.isPending && <p role="status" className="text-xs text-muted-foreground">{t("identity.signingIn")}</p>}
      </form>
    </main>
  }
  return <IdentityContext.Provider value={{ session: me.data, signOut: () => logout.mutate(), signingOut: logout.isPending, logoutFailed: logout.isError }}>{children}</IdentityContext.Provider>
}

export function IdentityControls() {
  const identity = useIdentity()
  const t = useT()
  if (!identity) return null
  return <div className="flex max-w-xs items-center gap-3 text-xs"><span className="max-w-28 truncate text-muted-foreground" title={t("identity.signedIn", { name: identity.session.principal.name })}>{identity.session.principal.name}</span><button type="button" className="whitespace-nowrap text-muted-foreground underline-offset-4 hover:underline disabled:opacity-50" disabled={identity.signingOut} onClick={identity.signOut}>{t(identity.signingOut ? "identity.signingOut" : "identity.signOut")}</button>{identity.logoutFailed && <span role="alert" className="text-destructive">{t("identity.logoutFailed")}</span>}</div>
}

export function CanUse({ capability, children, fallback = null }: { capability: string; children: ReactNode; fallback?: ReactNode }) {
  return useCanUse(capability) ? children : fallback
}

export function AccessBoundary({ capability, children }: { capability: string; children: ReactNode }) {
  const allowed = useCanUse(capability)
  const t = useT()
  if (allowed) return children
  return <section className="m-6 max-w-xl border-t border-border py-8"><h1 className="text-xl font-medium">{t("identity.denied")}</h1><p className="mt-3 text-sm text-muted-foreground">{t("identity.deniedHint")}</p></section>
}
