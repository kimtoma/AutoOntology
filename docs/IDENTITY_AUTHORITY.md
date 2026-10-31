# Identity and authority core

This is the P0-03 foundation, not complete application authorization. The core and
credential-file codec are implemented and tested. They are **not yet wired** to
HTTP, native business commands, the UI, background jobs or publication confirmation.
There is deliberately no new enrollment command or anonymous enrollment endpoint.
Do not enroll the normal workspace until the integration gates below pass. The
existing shared access password and model-provider login are separate mechanisms.

## Durable policy

Schema v12 adds a single organization/workspace identity, human and agent principals,
domains, role grants, hashed credentials, expiring delegations, versioned source-domain
classifications, login limits and append-only identity events. The migration adds
tables without rewriting existing application rows. Migration statements are parsed
with SQLite's complete-statement predicate so trigger bodies remain atomic.

`Identity.bootstrap` is a trusted operating-system-owner enrollment operation. It
creates one initial human administrator and classifies existing sources as General.
Concurrent enrollment is serialized; a second enrollment fails. Later provisioning,
role changes, password resets, source classification and delegation require a current
human workspace administrator. At least one active all-domain human administrator
must remain, including during concurrent removal attempts.

`Identity.authenticate` verifies a high-entropy credential. The returned `Actor`
does not grant authority by itself: `Identity.require` reloads principal kind,
credential linkage, status, epoch, expiry, revocation and current policy for every
operation. Caller-supplied actor names, kinds and known identifiers grant nothing.
Unknown actions, workspaces, scopes and unclassified sources fail closed.

The explicit capability/role catalog is in `autoontology/security/policy.py`.
Viewer/editor/reviewer/publisher/admin roles never imply an undefined capability.
Agent delegation names actions, domains, accountable human owner and expiry. The
owner's **current** roles and active status bound existing agent credentials.
Review decisions, publication, restoration, source/settings changes and identity
administration remain human-only. Delegating preparation cannot grant confirmation.

Until classifications are pinned per publication, source reads conservatively
require every historical domain assigned to that source. Moving a source cannot
make old private evidence readable to its new audience. This can deny otherwise
legitimate reads; it is not a substitute for per-publication domain bindings.

## Operation boundaries

Adapters must derive the action and resource domains from server-owned resource
records, not from a requested role or model output. Check before expensive work and
again at each effect boundary. For an atomic SQLite effect, pass its active write
transaction as `conn` to `require`. An `expected_policy` from a prepared packet
detects policy drift, but is not itself human consent or a publication content hash.
Long-running reads and jobs also need safe revocation/cancellation boundaries.

Identity audit events contain controlled metadata, authenticated actor/credential
identifiers and a consecutive SHA-256 chain. SQLite triggers reject update/delete.
This detects ordinary corruption/modification; it does not protect the database
from the operating-system owner who can edit files or remove triggers. Never give
an untrusted agent that host account's filesystem or database authority.

## Passwords and CLI credentials

Passwords use per-password random salts and scrypt N=131072, r=8, p=1. At most two
derivations run concurrently. Passwords require 15 characters and at most 1024
UTF-8 bytes; malformed types/encodings fail safely. Human sessions expire within
12 hours; agent delegations expire within seven days. A password reset increments
the principal epoch and invalidates every previous session. Revocation is checked
against stored policy rather than a signed token's old claims.

Account and peer failed-login budgets are stored as hashes; five account failures
or thirty peer failures within the window trigger a 60-second cooldown. Successful
login clears only its account counter, so a valid account cannot reset a peer's
brute-force budget. Future HTTP adapters must derive peer identity from a trusted
transport/proxy configuration, never a client-provided JSON field or arbitrary
forwarded header. Logs/audit/public representations omit passwords and raw tokens.

`security/credential_file.py` is the shared CLI storage codec, not an exposed CLI
command. It writes exclusively through a temporary file as mode 0600, checks an
owner-only directory using a directory descriptor, rejects symlinks and non-regular
files, and bounds reads. Existing files are not overwritten. Each credential is
bound to a workspace ID and canonical HTTPS or literal-loopback HTTP origin;
mismatches and expiry fail before a bearer secret is returned. Adapters must not
follow redirects with credentials. `Secret` and credential public representations
are redacted; explicit `.reveal()` belongs only at a storage/transport boundary.

## Required integration and activation gates

- Enforce on every business API, historical original/export, search/SQL result,
  conversation, native command, job and publication switch; unknown routes/actions
  deny access when configured. A remote command cannot fall back to host privileges.
- Add native principal/domain/role/delegation/session administration and login/logout
  with safe password input, machine-readable errors and no token stdout. Provide the
  equivalent human UI and capture its actual light-mode login/control/recovery flow.
- Separate upstream internal calls from user credentials. Model/provider and ingest
  shims must not create a route around principal permissions.
- Bind consequential human confirmation to exact content, base publication,
  validation, policy, executor, allowed effect and expiry, with one idempotent receipt.
  Revalidate before execution; an agent's credential cannot approve its own packet.
- Pin per-publication domains and filter mixed-source graph/search/history/downloads
  consistently. Do not claim global organization isolation from this core alone.
- Add explicit local-owner recovery and safe configuration-loss handling. An offline
  full backup can contain sessions revoked after that backup: rotate/invalidate them
  before exposing a recovered service. Knowledge-only managed restoration must not
  roll back current identity/roles/revocations or overwrite their audit history.
- Recheck the same permission, stale-context, expiry, revocation and retry scenarios
  through actual UI and CLI against an isolated service before normal enrollment.

The full P0-P3 lifecycle, semantic CLI parity and Enterprise AX measurement remain
in progress. No end-to-end time savings or completed authenticated product is
claimed by this foundation. See `KNOWLEDGE_OS_IMPLEMENTATION.md` and the private
stage evidence/recovery index for executed results.
