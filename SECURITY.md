# Security policy

## Scope

AutoOntology is a **local, single-user tool**. The service (port 7800) and the engine
backend (port 3101) bind to `127.0.0.1` and have **no authentication**. The service rejects
requests with a non-local `Host` header or a cross-site `Origin`, which guards against DNS
rebinding and cross-site requests from a browser. Do not expose these ports to a network,
and do not put them behind a reverse proxy or tunnel. Doing so is outside the supported
configuration. The one exception is opt-in: tailnet hostnames listed in
`AUTOONTOLOGY_TAILSCALE_HOSTS` (forwarded by `tailscale serve` on the same machine) are
accepted only with Tailscale's `Tailscale-User-Login` identity header, optionally limited to
`AUTOONTOLOGY_TAILSCALE_LOGINS`, and never for Funnel traffic (see the README section
"Remote access over Tailscale"). Public exposure with `tailscale funnel` is not supported.

Treat ingested documents as untrusted input. AutoOntology quotes document text inside
delimited prompt blocks, validates model output fail-closed, and escapes Markdown and HTML
in the OKF export. Anything that renders exported files as HTML must still sanitize them.

## Reporting a vulnerability

Please report vulnerabilities through
[GitHub issues](https://github.com/kimtoma/AutoOntology/issues). If the report includes
details that should not be public yet, open an issue that only asks for a private contact
and leave the details out; a maintainer will follow up. Never include API keys or private
data in a report.

Fixes go to the `main` branch; there are no maintained release branches yet.
