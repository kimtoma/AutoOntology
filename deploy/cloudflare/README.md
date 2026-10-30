# Password-protected sample demo through Cloudflare

This deployment keeps the real AutoOntology service on the owner's **Mac mini**.
Cloudflare serves a password form and forwards authenticated requests through a named
Tunnel to a second, loopback-only proxy on that Mac mini. The service remains bound to
`127.0.0.1`. Do not point a public Tunnel directly at port 7800.

```
visitor -> Cloudflare Worker (password + signed cookie)
        -> named Cloudflare Tunnel -> 127.0.0.1:7810 (origin secret)
        -> 127.0.0.1:7800 (AutoOntology) -> local Postgres/model provider
```

The Mac mini, app, database, selected model provider and tunnel must remain running.
See [the Mac mini runbook](MACMINI.md) for the installed ports, isolated workspace,
login services, and start/stop commands on the deployed host.
This is a hosted **sample viewer**, not an independent Cloudflare container deployment.
It exposes ontology search, graph exploration, grounded chat, SQL, SPARQL and existing
exports. Visitors cannot add/delete sources, build, access provider passthrough APIs, or
change settings. The browser does not list or open other visitors' conversations.

## Prepare on the Mac mini

1. Read applicable `AGENTS.md` files and check the repository, dirty state, app ports and
   current sources. Preserve the owner's existing data and settings. Use the already
   working AutoOntology environment; do not install the app on the MacBook Pro.
2. Use a sample-only workspace. If the current workspace contains private/customer data,
   create a separate home and dedicated sample sources without replacing existing ones.
3. Rebuild the UI using `cd ui && pnpm install --frozen-lockfile && pnpm build` if the
   committed bundle is not already updated. Start AutoOntology, ensure its latest
   snapshot and exports are present, and prebuild the playground index locally with
   `POST http://127.0.0.1:7800/api/playground/index`.
4. Write `data/cloudflare-demo/secrets.json` with mode **0600**. Generate independent
   random values for `origin_secret` and `session_secret` (at least 32 random bytes each)
   and a strong shareable `access_password`. Never commit this file or print its contents
   to a build log. The entire `data/` directory is already git-ignored.
5. Start `python3 deploy/cloudflare/origin_proxy.py` on the Mac mini. The default app port
   is 7800 and proxy port is 7810; use `AUTOONTOLOGY_PORT` and `AUTOONTOLOGY_DEMO_PORT`
   if needed. Use a LaunchAgent for the proxy and named tunnel so they restart after login.

## Cloudflare configuration

Use the owner's intended Cloudflare account. Avoid modifying existing tunnels/workers.

- Worker: `autoontology-demo`, `compatibility_date=2026-10-30`, `nodejs_compat`.
- Optional visitor hostname: `autoontology.kimtoma.com`.
- Separate origin hostname: `autoontology-origin.kimtoma.com`.
- Create a **new named tunnel** with that origin hostname pointing to
  `http://127.0.0.1:7810`; use a final `http_status:404` ingress rule.
- Keep the connector token in a 0600 file on the Mac mini and run cloudflared using
  `--token-file`. Do not include it in shell arguments or commit it.
- Deploy `worker.mjs` with the bindings/configuration in `wrangler.jsonc`.
- Set Worker secrets `ORIGIN_SECRET`, `SESSION_SECRET`, `ACCESS_PASSWORD_SHA256`
  (lowercase SHA-256 hex of the shareable access password).
- Set `ORIGIN_URL=https://autoontology-origin.kimtoma.com` as a Worker variable.
- Disable unauthenticated preview URLs. All visitor/API routes go through the Worker.

The Worker adds a meta tag to authenticated HTML. The UI uses it to show the sample-demo
banner and omit unavailable administration controls. API restrictions are enforced on the
server regardless of what the browser renders. Rate limits are per Cloudflare location,
and are not a strict global usage budget.

## Verification before sharing

```
node --test deploy/cloudflare/worker.test.mjs
python3 -m unittest discover -s deploy/cloudflare -p 'test_*.py'
cd ui && pnpm lint && pnpm test && pnpm build
```

Then verify the actual external hostname in a browser: an unsigned visitor sees the
password form and gets 401 on `/api/ontology`; a wrong password is rejected; the correct
password opens the sample ontology; graph nodes and evidence details open; a real chat
question returns a completed answer with a clickable citation; SQL and an export work;
admin writes remain 403; the origin hostname itself is 403 without the origin secret.
Log out and confirm the protected API is private again. Verify the running connector and
app processes are on the Mac mini. Do not report deployment complete from login alone.

## Stop sharing

Disable the Worker's public domain/subdomain or stop the named connector. Keep the local
app private. Rotate the access password hash to change the shared password; rotate
`SESSION_SECRET` as well to expire all signed-in sessions.
