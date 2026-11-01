# Side-by-side version gateway

The password-protected Worker serves three fixed visitor hosts:

| Host | Behavior after password login |
| --- | --- |
| `autoontology.kimtoma.com` | `/` and `/__versions` show the comparison/chooser; existing `/ui/*` and API links retain the classic app |
| `autoontology-classic.kimtoma.com` | Existing application and existing workspace |
| `autoontology-next.kimtoma.com` | New application and a separately prepared workspace |

The chooser opens each version in its own tab. Cookies remain Secure, HttpOnly,
host-only and audience-bound. A cookie copied from another version is refused.
Each host therefore needs one password login; the shareable password is unchanged.
The page explicitly explains that data/settings do not synchronize between versions.
An injected compact version indicator links back to the comparison page.

## Configuration and deployment order

Worker vars `MAIN_APP_HOST`, `CLASSIC_APP_HOST`, `NEXT_APP_HOST` map only those
configured hosts to fixed `classic` or `next` version labels. Unknown hosts fail
closed. The three custom-domain routes are declared in `wrangler.jsonc`.
The existing Worker origin URL and all three secrets stay unchanged. Do not put
secrets in config, command arguments or logs.

Prepare and validate the new isolated app before publishing its link. Configure
the updated loopback proxy with existing `AUTOONTOLOGY_PORT=7820` and
`AUTOONTOLOGY_NEXT_PORT=7870`. These are local environment values, not visitor
parameters. The same named tunnel still points to the authenticated proxy on7821.
Only requests with the origin secret may select the exact `classic`/`next` label;
missing version means classic for compatibility with the older Worker. Duplicate,
unknown or unavailable selections are refused. The proxy forwards neither the
version selector nor browser authentication/identity headers to either app.

Use the new Worker/proxy source from this checkout when updating the deployment.
Do not change the original app source/workspace to enable selection. Keep startup
and rollback configuration in protected host-only runtime records. Rollback may
restore the older Worker; absent-version proxy requests continue to use classic.

The proxy rewrites redirects to the selected loopback application as relative
paths. The Worker only accepts redirects within its configured tunnel origin and
returns visitor-relative locations; arbitrary external redirects are rejected.

## Verification

```bash
node --test deploy/cloudflare/worker.test.mjs
python3 -m unittest discover -s deploy/cloudflare -p 'test_*.py'
```

Then verify all three real hosts: private APIs before login, password/login,
chooser links, separate version labels, both graph/evidence/question/SQL/download
paths, denied cross-origin writes, private tunnel origin, and data isolation.
Capture actual light-mode chooser and each application flow. Tests and configuration
alone are not proof that domains, certificates, runtime or external features work.

The shared gateway password is separate from the app's individual identity and
approval system. The gateway retains its existing browser-cookie stripping.
Do not enroll a gateway-shared app and assume individual sessions will pass
through this boundary without a separately designed and tested session contract.

## Installed Mac mini controls

From this checkout:

```bash
bash deploy/cloudflare/versionsctl.sh status
bash deploy/cloudflare/versionsctl.sh start
bash deploy/cloudflare/versionsctl.sh stop
bash deploy/cloudflare/versionsctl.sh stop next
bash deploy/cloudflare/versionsctl.sh start next
```

The optional target is `all`, `classic` or `next`. Stopping one application leaves
the shared proxy/tunnel and the other application running. Starting one also
starts the shared gateway if needed. Stopping all unloads only these six agents;
Ollama and unrelated local/review services are preserved. RunAtLoad/KeepAlive agents
start again after the Mac mini user's next login. The root Worker login/chooser is
hosted separately and can remain visible when local application services stop.

The next application uses service7870, backend3171 and PostgreSQL55440. Classic
retains7820/3121/55432; the shared proxy remains7821. Both use Codex for chat and
the existing Ollama Qwen3embedding4B model. Their mutable workspace, exports,
database clusters, logs and runtime settings are separate. The next fixture keeps
the immutable public quickstart structured locator for its legacy mapping; build
outputs and SQL operate only on its own cluster. No original workspace is retired.

## Actual external acceptance — 2026-11-01

Both custom-domain applications were verified over real HTTPS in Chromium after
deployment. The root comparison page is password protected. Both application
hosts reject unsigned APIs, foreign-audience cookies and cross-origin writes;
the direct tunnel origin rejects requests without its separate gateway secret.
Visitor-supplied routing headers cannot change the selected application.

| Scenario | Classic0.2.0 | Ontology Ops0.3.0a1 |
| --- | --- | --- |
| Primary home | Setup/build progress | Operations, incoming changes and review queue |
| Navigation | Existing build/exploration menu | Operations, review, sources, ontology, use and history |
| Actual graph and cited Korean sample answer | Passed | Passed |
| Source evidence | Quoted evidence | Quoted evidence plus content-addressed original download |
| Actual customer-count SQL and CSV | Passed:40 | Passed:40 |
| Ontology Turtle download | Passed | Passed |
| Saved SQL receipt and identical CSV replay | Earlier interface | Verified without another model call |
| Frozen fictional-data comparison and JSON download | Not present | Verified; not a measured business baseline |

Acceptance used one completed Codex chat and one SQL request per version, four
model requests total. It also verified the next app's390px layout, a temporary
CSV upload visible only there, cleanup, same-value model-setting request and web
restart while classic remained available. All155business rows agree across the
separate clusters. Existing source/checkpoint bytes were preserved.

Twenty-one actual light-mode flow captures, receipts, download hashes, preparation
failures and restart evidence remain in the owner's ignored private runtime.
They are not distributed with this source. Timing observations from individual
calls are not a comparative model benchmark or proof of organizational savings.
The wider Knowledge OS development remains paused and incomplete.
