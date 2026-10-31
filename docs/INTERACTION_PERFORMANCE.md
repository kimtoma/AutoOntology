# Time to useful data

UI/CLI latency is part of organizational time cost. Measure the first useful data, not just the appearance of a spinner. Independent regions render as soon as their data arrives. An unavailable operations or ontology response does not prevent case summaries, activity or source responses from rendering. Missing counts remain unknown; loading is never displayed as zero.

The client retains its existing authorized query cache during background refreshes. A failed refresh shows an error while retaining the last successful data. Workspace, principal, credential or policy changes still cancel/remove business queries; logout and revocation still lock the workspace. This change adds no persistent browser data cache or authorization shortcut. Whole-page reloads still verify identity.

The operations home prioritizes knowledge changes and review work. Detailed time cohorts load only when their section is expanded. In native CLI, metadata and command discovery no longer initialize the ingestion HTTP/schema/DuckDB dependency chain. Actual file discovery and semantic acquisition load their dependencies when called; command outputs and authority checks remain shared.

## Local measurement protocol

The private before/after experiment uses commit `08d21da` as the immutable before code and the same copied local workspace data, with additive schema migration after the change. Each side uses three cold-browser-cache Chromium trials, then three trials with a controlled two-second delay on `/api/ops`. Native timings start a fresh Python process five times per command. A single warm SPA return is reported separately. No CPU/network throttling is applied; server/OS caches and incidental load are not controlled. These are small local laboratory samples, not production Core Web Vitals, real-user INP, remote-service results or measured organizational savings.

| Observed interaction | Before | After |
| --- | --- | --- |
| First case data, median of three | 269.6 ms | 182.1 ms |
| First case data with synthetic 2 s operations delay, median of three | 2150.4 ms | 133.6 ms |
| Warm SPA return, one sample | 55.1 ms | 56.0 ms |
| Native case summary startup, median of five | 193.0 ms | 79.4 ms |
| Native source list startup, median of five | 190.5 ms | 80.5 ms |

Intermediate after batches measured 186.0 ms and 224.3 ms for first case data; the final bundle measured 182.1 ms. All batches are retained, and the last frozen implementation is reported. The single warm-return observation shows no demonstrated gain (55.1 ms versus 56.0 ms); do not claim that interaction improved.

The delayed case measures independence from the slow request; it does not imply that adding latency improves a page. Browser captures show partial rendering before that response, completed data, an explicitly simulated 503 refresh error with retained authorized data, and return navigation. Regression checks also retain policy-change cache disposal and session-revocation behavior.

Continue measuring representative large workspaces, other pages, actual remote networks and the full knowledge/decision loop before claiming organization-wide performance.
