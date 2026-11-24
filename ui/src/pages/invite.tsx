/* `/invite#token=<t>&purpose=invite|reset`: the link of an invitation or a password reset.
   The token is read from the fragment once (it never reaches a server log or a Referer) and
   the fragment is dropped from the history right away; choosing a password signs in. */

import { useMutation } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import { Link, Navigate, useLocation, useNavigate } from "react-router"
import { toast } from "sonner"

import { useSignedIn } from "@/hooks/use-auth"
import { NewPasswordFields } from "@/components/password-field"
import { Button } from "@/components/ui/button"
import { CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useT } from "@/i18n"
import { authApi, authErrorMessage, newPasswordOk, readInviteHash, useMe } from "@/lib/auth"
import { AuthLayout, FormError } from "@/pages/login"

export default function InvitePage() {
  const t = useT()
  const location = useLocation()
  const navigate = useNavigate()
  const signedIn = useSignedIn()
  const me = useMe()
  // read once: the effect below removes the fragment
  const [link] = useState(() => readInviteHash(location.hash))
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [tried, setTried] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    if (location.hash) navigate({ pathname: location.pathname, search: location.search }, { replace: true })
  }, [location.hash, location.pathname, location.search, navigate])

  const accept = useMutation({
    mutationFn: () => authApi.acceptInvite(link!.token, password),
    onSuccess: (me) => {
      signedIn(me)
      toast.success(t("auth.invite.done"))
      navigate("/", { replace: true })
    },
    onError: (err) => setProblem(authErrorMessage(err)),
  })

  const reset = link?.purpose === "reset"
  if (me.auth !== "accounts") return <Navigate to="/" replace />
  if (!link) {
    return (
      <AuthLayout id="invite-page">
        <CardHeader className="px-6">
          <CardTitle className="text-xl"><h1>{t("auth.invite.title")}</h1></CardTitle>
          <CardDescription id="invite-no-token">{t("auth.invite.noToken")}</CardDescription>
        </CardHeader>
        <CardContent className="px-6">
          <Button asChild variant="outline" className="w-full"><Link to="/login">{t("auth.invite.toLogin")}</Link></Button>
        </CardContent>
      </AuthLayout>
    )
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (!newPasswordOk(password, confirm)) return
    setProblem(null)
    accept.mutate()
  }
  return (
    <AuthLayout id="invite-page">
      <CardHeader className="px-6">
        <CardTitle className="text-xl"><h1>{t(reset ? "auth.invite.title.reset" : "auth.invite.title")}</h1></CardTitle>
        <CardDescription>{t(reset ? "auth.invite.description.reset" : "auth.invite.description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-6">
        <form onSubmit={submit} className="grid gap-4" noValidate id="invite-form" method="post">
          {/* lets a password manager store the new password with the right account name */}
          <input type="text" name="username" autoComplete="username" hidden readOnly value="" />
          <NewPasswordFields idPrefix="invite" value={password} confirm={confirm} onValue={setPassword}
                             onConfirm={setConfirm} showErrors={tried}
                             labels={{ password: "auth.invite.password", confirm: "auth.invite.confirm" }} />
          <FormError id="invite-error" message={problem} />
          <Button type="submit" disabled={accept.isPending} id="btn-accept-invite">
            {accept.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {t("auth.invite.submit")}
          </Button>
        </form>
      </CardContent>
    </AuthLayout>
  )
}
