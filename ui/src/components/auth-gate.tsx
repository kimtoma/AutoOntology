/* The sign-in gate: `GET /api/auth/me` once at startup. Accounts off (or an older service
   without the route): the app as before. Accounts on and signed out: every page but the
   login and invitation pages goes to `/login?next=<the page>`. Any later 401
   `login_required` (the session ended) does the same; 403 `forbidden` shows a toast naming
   the missing permission (the UI hides what the role cannot do, the server decides). */

import { useQueryClient } from "@tanstack/react-query"
import { useEffect, type ReactNode } from "react"
import { Navigate, Outlet, useLocation } from "react-router"
import { toast } from "sonner"

import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { signedOut, useMeQuery } from "@/hooks/use-auth"
import { t } from "@/i18n"
import { isLoginRequired, setAuthErrorHandler, type ApiError } from "@/lib/api"
import { AuthContext, OFF_ME, useMe, type Me } from "@/lib/auth"

function AuthErrors() {
  const qc = useQueryClient()
  useEffect(() => {
    const onError = (err: ApiError) => {
      const permission = String(err.body?.permission ?? "")
      if (isLoginRequired(err)) {
        const me = qc.getQueryData<Me>(keys.me)
        if (me?.auth === "accounts") {
          if (me.authenticated) qc.setQueryData(keys.me, signedOut(me))
        } else {
          void qc.invalidateQueries({ queryKey: keys.me })  // accounts were switched on meanwhile
        }
        return
      }
      toast.error(t("auth.forbidden", { permission }), { id: `forbidden:${permission}` })
    }
    setAuthErrorHandler(onError)
    return () => setAuthErrorHandler(null)
  }, [qc])
  return null
}

export function AuthGate({ children }: { children: ReactNode }) {
  const me = useMeQuery()
  if (me.isPending) {
    return <Skeleton className="m-6 h-[60svh]" id="auth-loading" aria-label={t("auth.loading")} />
  }
  // unreachable or an unexpected answer: the pages explain an unreachable service as before
  const value = me.data ?? OFF_ME
  return (
    <AuthContext.Provider value={value}>
      <AuthErrors />
      {children}
    </AuthContext.Provider>
  )
}

/** Pages that need a signed-in user (every page but login and invitation). */
export function RequireAuth() {
  const me = useMe()
  const { pathname, search, hash } = useLocation()
  if (me.auth === "accounts" && !me.authenticated) {
    const here = pathname + search + hash
    const to = here === "/" ? "/login" : `/login?next=${encodeURIComponent(here)}`
    return <Navigate to={to} replace />
  }
  return <Outlet />
}
