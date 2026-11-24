/* Sign-in state hooks: the `/api/auth/me` query and what a sign-in or sign-out does to the
   query cache (nothing of the previous user stays). */

import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useCallback } from "react"

import { keys } from "@/hooks/queries"
import { authApi, OFF_ME, type Me } from "@/lib/auth"

/** `GET /api/auth/me` (no retry: a 404 means accounts off, an error falls back to off too). */
export const useMeQuery = () =>
  useQuery({ queryKey: keys.me, queryFn: authApi.me, retry: false, staleTime: Infinity, gcTime: Infinity })

/** Signed out: the same state without a user. */
export const signedOut = (me: Me): Me => ({
  ...me, authenticated: false, actor: null, via: null, user: null, is_admin: false, memberships: [],
  permissions: {}, installation_permissions: [], session_expires_at: null,
})

/** Forget what the previous user saw (every query but the sign-in state). */
export function dropUserData(qc: QueryClient) {
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" })
}

/** After a sign-in (password, link, invitation): the new state, nothing of the previous one. */
export function useSignedIn() {
  const qc = useQueryClient()
  return useCallback((me: Me) => {
    dropUserData(qc)
    qc.setQueryData(keys.me, me)
  }, [qc])
}

/** After signing out (or a session that ended). */
export function useSignedOut() {
  const qc = useQueryClient()
  return useCallback(() => {
    const me = qc.getQueryData<Me>(keys.me)
    dropUserData(qc)
    qc.setQueryData(keys.me, me ? signedOut(me) : signedOut({ ...OFF_ME, auth: "accounts" }))
  }, [qc])
}

