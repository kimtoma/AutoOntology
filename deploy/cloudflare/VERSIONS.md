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
