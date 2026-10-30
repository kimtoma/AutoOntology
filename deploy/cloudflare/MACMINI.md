# Mac mini sample deployment

Visitor URL: `https://autoontology.kimtoma.com`.

This host runs a separate `public-demo` workspace containing only the quickstart
sample, rebuilt from the existing extracted sample results. Existing `data/`, root
`exports/`, `.env`, the Postgres instance on 5432, and the `draftboard` tunnel are
preserved. Historical snapshots and conversations were excluded from the demo copy.

| Component | Loopback port |
| --- | --- |
| AutoOntology | 7820 |
| Authenticated origin proxy | 7821 |
| Upstream backend | 3121 |
| Dedicated Postgres 17 + pgvector | 55432 |
| Existing Ollama embeddings (`bge-m3`) | 11434 |

Chat uses `AUTOONTOLOGY_CHAT_PROVIDER=codex`, model `gpt-6-astra`, and the owner's
existing ChatGPT CLI login. Model requests run on the Mac mini and use the Codex
subscription service. Embeddings use the existing local Ollama configuration.
SQL source connections use `autoontology_demo_reader`, with SELECT privileges only.
The catalog uses its own local application credential.

The Worker `autoontology-demo` runs in the `kimtoma` account. Preview and workers.dev
URLs are disabled. A separate locally managed tunnel
`6344dc9d-37ab-480d-8678-001238c42d7d` forwards only
`autoontology-origin.kimtoma.com` to the origin proxy. Its 0600 credentials JSON is
used directly, without a credential in process arguments. The origin rejects
requests without the independent Worker secret.

Five LaunchAgents under `~/Library/LaunchAgents/com.kimtoma.autoontology-demo.*.plist`
start Postgres, the service, backend, proxy, and tunnel after login and restart them
if they exit. Postgres explicitly sets `LC_ALL=C`. Ollama already has its own login
service.

From this checkout:

```bash
bash deploy/cloudflare/democtl.sh status
bash deploy/cloudflare/democtl.sh stop
bash deploy/cloudflare/democtl.sh start
```

`stop` unloads only these five demo agents for the current login session. The
password screen remains on Cloudflare; authenticated app requests cannot reach the
stopped origin. Agents start again at the next login. The existing Ollama and other
services remain running.

Host-only files live under the ignored `data/cloudflare-demo/` directory:

- `demo.env`: isolated paths, ports, provider selection and database credential (0600).
- `secrets.json`: shareable password and independent origin/session/reader secrets (0600).
- `tunnel.yml`, `tunnel-credentials.json`: dedicated tunnel configuration and credential.
- `workspace/`, `exports/`, `postgres/`: the demo's independent state and data.
- `logs/`: component logs. Never enable debug header/body logging.
- `browser-verification.json`, screenshots, and `verify-browser.py`: external verification.

Do not publish these runtime files. To rotate the sharing password, update its
SHA-256 Worker secret and rotate `SESSION_SECRET` to expire existing cookies; update
the protected local record too. Send secrets to Wrangler over stdin with sanitized
logging, never through shell arguments.
