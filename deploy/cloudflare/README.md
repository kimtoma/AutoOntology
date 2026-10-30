# Password-protected AutoOntology through Cloudflare

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
The deployed `ACCESS_MODE=full` workspace exposes every application page and API after
password login: source uploads/removal, build/apply/export, review, conversations,
settings, provider tests, and application restart. Everyone with the shared password
uses the same workspace and permissions. Cross-origin writes remain rejected.

For a restricted sample viewer, explicitly set `ACCESS_MODE=sample` instead. That mode
hides administration pages, allows only sample reads/queries, and keeps chats browser-local.

## Prepare on the Mac mini

1. Read applicable `AGENTS.md` files and check the repository, dirty state, app ports and
   current sources. Preserve the owner's existing data and settings. Use the already
   working AutoOntology environment; do not install the app on the MacBook Pro.
2. Use a separate home, exports directory, and database instance to preserve existing
   local work. Preload samples if wanted; users can add their own sources in full mode.
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
   Run `run_app.py` in an application LaunchAgent with `AUTOONTOLOGY_ENV_FILE` pointing
   to the isolated environment. It uses the native Supervisor, so web settings can
   restart both app processes. Do not also launch a standalone backend agent.
6. Set `AUTOONTOLOGY_UPLOAD_MAX_MB=90` for this Cloudflare route. The Worker streams
   bodies and the origin spools uploads to disk, with a 100,000,000-byte transport cap.
   This fits the current Cloudflare Free/Pro request limit. The local app may use its
   larger default upload limit on routes that do not pass through Cloudflare.

## Cloudflare configuration

Use the owner's intended Cloudflare account. Avoid modifying existing tunnels/workers.

- Worker: `autoontology-demo`, `compatibility_date=2026-10-30`, `nodejs_compat`.
- Optional visitor hostname: `autoontology.kimtoma.com`.
- Separate origin hostname: `autoontology-origin.kimtoma.com`.
- Create a **new named tunnel** with that origin hostname pointing to
  `http://127.0.0.1:7810`; use a final `http_status:404` ingress rule.
- Keep locally managed tunnel credentials in a 0600 JSON file referenced by the
  dedicated config. For remote-managed tunnels, use a 0600 token file with `--token-file`.
  Do not include credentials in shell arguments or commit them.
- Deploy `worker.mjs` with the bindings/configuration in `wrangler.jsonc`.
- Set Worker secrets `ORIGIN_SECRET`, `SESSION_SECRET`, `ACCESS_PASSWORD_SHA256`
  (lowercase SHA-256 hex of the shareable access password).
- Set `ORIGIN_URL=https://autoontology-origin.kimtoma.com` and `ACCESS_MODE=full` as Worker variables.
- Disable unauthenticated preview URLs. All visitor/API routes go through the Worker.

The Worker adds a mode meta tag to authenticated HTML. Full mode shows the complete UI
and a shared-workspace sign-out banner. Sample mode omits administration controls and
enforces its restrictions on the Worker independently of browser rendering. Rate limits are per Cloudflare location,
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
a real upload, build, export, source deletion, settings save and web restart complete;
cross-origin writes remain 403; the origin hostname itself is 403 without the origin secret.
Log out and confirm the protected API is private again. Verify the running connector and
app processes are on the Mac mini. Do not report deployment complete from login alone.

## Stop sharing

Disable the Worker's public domain/subdomain or stop the named connector. Keep the local
app private. Rotate the access password hash to change the shared password; rotate
`SESSION_SECRET` as well to expire all signed-in sessions.
