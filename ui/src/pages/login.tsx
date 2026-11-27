/* `/login` (accounts mode): email + password, an emailed sign-in link when SMTP is set up,
   the notice of a bad link (`?error=link`), and back to `?next=` afterwards. With accounts
   off, or already signed in, it goes on to the app. */

import { useMutation } from "@tanstack/react-query"
import { Loader2, Mail, MailCheck } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react"
import { Navigate, useNavigate, useSearchParams } from "react-router"

import { useSignedIn } from "@/hooks/use-auth"
import { LangToggle } from "@/components/lang-toggle"
import { PasswordInput } from "@/components/password-field"
import { ThemeToggle } from "@/components/theme"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useT } from "@/i18n"
import { authApi, authErrorMessage, safeNext, useMe } from "@/lib/auth"

/** The frame of the sign-in pages: no sidebar, the language and theme switches. */
export function AuthLayout({ children, id }: { children: ReactNode; id: string }) {
  return (
    <div className="flex min-h-svh flex-col bg-muted/30">
      <header className="flex items-center justify-between px-4 py-3">
        <span className="flex items-center gap-2 font-semibold">
          <span aria-hidden className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">◇</span>
          Ontology
        </span>
        <span className="flex items-center gap-1"><LangToggle /><ThemeToggle /></span>
      </header>
      <main id={id} className="flex flex-1 items-start justify-center px-4 pt-[8svh] pb-10">
        <Card className="w-full max-w-sm gap-5 py-6">{children}</Card>
      </main>
    </div>
  )
}

export function FormError({ id, message }: { id: string; message: string | null }) {
  if (!message) return null
  return (
    <p id={id} role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
      {message}
    </p>
  )
}

export default function LoginPage() {
  const t = useT()
  const me = useMe()
  const navigate = useNavigate()
  const signedIn = useSignedIn()
  const [params] = useSearchParams()
  const next = safeNext(params.get("next"))
  const linkError = params.get("error") === "link"
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [problem, setProblem] = useState<string | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  const login = useMutation({
    mutationFn: () => authApi.login(email.trim(), password),
    onSuccess: (fresh) => {
      signedIn(fresh)
      navigate(next, { replace: true })
    },
    onError: (err) => {
      setProblem(authErrorMessage(err))
      setPassword("")
      passwordRef.current?.focus()
    },
  })
  const magic = useMutation({
    mutationFn: (to: string) => authApi.magicLink(to),
    // the answer never tells whether the address has an account: the same notice either way
    onSettled: (_d, _e, to) => setSentTo(to),
  })

  useEffect(() => { if (!sentTo) emailRef.current?.focus() }, [sentTo])

  if (me.auth !== "accounts" || me.authenticated) return <Navigate to={next} replace />

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) {
      setProblem(t("auth.login.required"))
      ;(email.trim() ? passwordRef : emailRef).current?.focus()
      return
    }
    setProblem(null)
    login.mutate()
  }
  const askLink = () => {
    if (!email.trim() || !emailRef.current?.checkValidity()) {
      setProblem(t("auth.login.magic.hint"))
      emailRef.current?.focus()
      return
    }
    setProblem(null)
    magic.mutate(email.trim())
  }

  if (sentTo) {
    return (
      <AuthLayout id="login-page">
        <CardHeader className="px-6">
          <MailCheck className="mb-2 size-8 text-primary" aria-hidden />
          <CardTitle className="text-xl" id="magic-sent" tabIndex={-1} ref={(el) => el?.focus()}>{t("auth.login.magic.sent.title")}</CardTitle>
          <CardDescription>{t("auth.login.magic.sent", { email: sentTo })}</CardDescription>
        </CardHeader>
        <CardContent className="px-6">
          <Button variant="outline" className="w-full" onClick={() => { setSentTo(null); magic.reset() }} id="btn-magic-back">
            {t("auth.login.magic.again")}
          </Button>
        </CardContent>
      </AuthLayout>
    )
  }

  const busy = login.isPending || magic.isPending
  return (
    <AuthLayout id="login-page">
      <CardHeader className="px-6">
        <CardTitle className="text-xl"><h1>{t("auth.login.title")}</h1></CardTitle>
        <CardDescription>{t("auth.login.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 px-6">
        {linkError && (
          <p role="status" id="login-link-error" className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
            {t("auth.login.linkError")}
          </p>
        )}
        <form onSubmit={submit} className="grid gap-4" noValidate aria-describedby={problem ? "login-error" : undefined}
              id="login-form" method="post">
          <div className="grid gap-2">
            <Label htmlFor="login-email">{t("auth.login.email")}</Label>
            <Input ref={emailRef} id="login-email" name="email" type="email" inputMode="email" autoComplete="username"
                   autoCapitalize="off" spellCheck={false} required value={email} maxLength={320}
                   onChange={(e) => setEmail(e.target.value)} />
          </div>
          <PasswordInput ref={passwordRef} id="login-password" name="password" label={t("auth.login.password")}
                         autoComplete="current-password" required value={password} maxLength={1024}
                         onChange={(e) => setPassword(e.target.value)} />
          <FormError id="login-error" message={problem} />
          <Button type="submit" disabled={busy} id="btn-login">
            {login.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {login.isPending ? t("auth.login.submitting") : t("auth.login.submit")}
          </Button>
        </form>
        {me.magic_link_enabled && (
          <>
            <div className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden>
              <span className="h-px flex-1 bg-border" />{t("auth.login.or")}<span className="h-px flex-1 bg-border" />
            </div>
            <Button type="button" variant="outline" disabled={busy} onClick={askLink} id="btn-magic">
              {magic.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Mail aria-hidden />}
              {t("auth.login.magic")}
            </Button>
          </>
        )}
        <p className="text-center text-xs text-muted-foreground">{t("auth.login.noAccount")}</p>
      </CardContent>
    </AuthLayout>
  )
}
