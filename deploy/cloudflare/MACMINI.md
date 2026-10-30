# Mac mini full workspace deployment

Visitor URL: `https://autoontology.kimtoma.com`.

This host runs a separate `public-demo` workspace initially populated with the
quickstart sample. It now accepts uploads and exposes the complete application. Existing `data/`, root
`exports/`, `.env`, the Postgres instance on 5432, and the `draftboard` tunnel are
preserved. Historical snapshots and conversations were excluded from the demo copy.

| Component | Loopback port |
| --- | --- |
| AutoOntology | 7820 |
| Authenticated origin proxy | 7821 |
| Upstream backend | 3121 |
| Dedicated Postgres 17 + pgvector | 55432 |
| Existing Ollama embeddings (`qwen3-embedding:4b`) | 11434 |

Chat uses `AUTOONTOLOGY_CHAT_PROVIDER=codex`, model `gpt-6-astra`, and the owner's
existing ChatGPT CLI login. Model requests run on the Mac mini and use the Codex
subscription service. Embeddings use the existing local Ollama configuration.
The embedding model was selected by the [Korean retrieval evaluation](EMBEDDING_EVALUATION.md).
The 891 live vectors were re-embedded at 2560 dimensions, with SQLite/Postgres backups
retained under the ignored `data/cloudflare-demo/embedding-evaluation/` directory.
The primary local workspace keeps its previous embedding configuration.
Sources use the isolated Postgres application credential, so builds can manage their
source databases. The former sample-only read account remains unused.
The Worker variable `ACCESS_MODE=full` enables all UI pages and APIs behind the password.
The backend entry point installs a pinned compatibility fix for single-table `COUNT(*)`
queries in both the parent and spawned SQL workers; vendor files remain unchanged.

The Worker `autoontology-demo` runs in the `kimtoma` account. Preview and workers.dev
URLs are disabled. A separate locally managed tunnel
`6344dc9d-37ab-480d-8678-001238c42d7d` forwards only
`autoontology-origin.kimtoma.com` to the origin proxy. Its 0600 credentials JSON is
used directly, without a credential in process arguments. The origin rejects
requests without the independent Worker secret.

Four LaunchAgents under `~/Library/LaunchAgents/com.kimtoma.autoontology-demo.*.plist`
start Postgres, an application guardian (`service`), the proxy, and the tunnel after login.
The guardian runs `run_app.py`, which manages both service and backend with the native
Supervisor and cooperates with web settings restarts. The old standalone backend
LaunchAgent is preserved under the ignored runtime directory and is no longer loaded.
Postgres explicitly sets `LC_ALL=C`. Ollama already has its own login service.

From this checkout:

```bash
bash deploy/cloudflare/democtl.sh status
bash deploy/cloudflare/democtl.sh stop
bash deploy/cloudflare/democtl.sh start
```

`stop` unloads only these four workspace agents for the current login session. The
password screen remains on Cloudflare; authenticated app requests cannot reach the
stopped origin. Agents start again at the next login. The existing Ollama and other
services remain running.

Host-only files live under the ignored `data/cloudflare-demo/` directory:

- `demo.env`: isolated paths, ports, provider selection and database credential (0600).
- `secrets.json`: shareable password and independent origin/session/reader secrets (0600).
- `tunnel.yml`, `tunnel-credentials.json`: dedicated tunnel configuration and credential.
- `workspace/`, `exports/`, `postgres/`: the demo's independent state and data.
- `logs/`: LaunchAgent logs; `workspace/logs/`: supervised app/backend/job logs. Never enable debug header/body logging.
- `full-verification.json`, screenshots, and `verify-full*.py`: external full-access verification.
- `workspace/kordoc`: link to the existing document engine and OCR models.

Uploads through the public route accept up to 90 MB per request. The full workspace
shares sources, results, conversations, and settings among everyone using the password.

Do not publish these runtime files. To rotate the sharing password, update its
SHA-256 Worker secret and rotate `SESSION_SECRET` to expire existing cookies; update
the protected local record too. Send secrets to Wrangler over stdin with sanitized
logging, never through shell arguments.
