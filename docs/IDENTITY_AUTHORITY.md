# Identity and authority core

P0-03 is in progress. The durable core now has initial HTTP, native CLI and browser
session adapters and guarded review/publication effects. Unfiltered resources
currently require **whole-workspace scope**. Mixed-domain projection, complete
effect/stream/queue revocation, administration UI, exact approval lifecycle and
local-owner recovery are not complete. There is no anonymous enrollment endpoint
or normal-workspace enrollment command. Do not enroll the normal workspace until
the activation gates below pass. The existing shared access password and
model-provider login are separate mechanisms.

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

- Finish per-resource domain checks/projections across business APIs, historical
  originals/exports, search/SQL results and conversations. The current explicit
  route inventory denies unknown endpoints and requires whole-workspace scope for
  unfiltered data. It must not be described as permitting legitimate mixed-domain
  access. Complete native mutation/read parity and effect-boundary checks, including
  source/settings/connection-review writes and resource ownership.
- Finish principal/domain/role/delegation/session administration UI and the missing
  native administration operations. Initial CLI login/logout, whoami, people,
  provisioning/roles/disable/domain/classification/delegation/revocation/audit exist;
  this is not full lifecycle or remote-session parity. Finish bounded credential
  renewal/recovery and explicit scope selection before enrollment.
- Complete managed ingestion callback compatibility and internal job authorization.
  The model router has a separate purpose-limited key; configured unauthenticated
  ingestion shims are denied. Raw backend access/host-path registration, approved
  source roots and secret-file exclusions still need their final activation audit.
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

## Initial adapter integration

`security/context.py` binds verified execution identity to one database/workspace
without granting an operation. HTTP and native CLI checks use it; `RunManager`
explicitly copies it to workers. Candidate preparation checks at job/step boundaries.
Common review, check/feedback and publication mutations recheck inside an immediate
SQLite transaction and append an actor-attributed effect event atomically. Caller
actor names are ignored when identity is configured. Publication refuses implicit
local authority, unreceipted switches and policy/role/credential drift during
preparation. These guards do not complete the separate consent/approval lifecycle.

The HTTP inventory is checked against actual OpenAPI operations, not a potentially
empty flat router enumeration. Registered path converters are compiled for request
matching; an unknown endpoint fails closed. Explicit approved history remains
readable with history permission; candidate preview needs candidate permission.

Browser sessions use a workspace/port-namespaced, host-only, HttpOnly, SameSite=Strict
cookie. Login fields never enter browser storage. Protected UI mounts after session
verification; a 401 locks it and removes work caches. Logout revokes the credential
before reporting success. Page/navigation capability checks improve the interface;
the service remains the enforcement boundary. While operational state is unknown,
the root shows neutral loading rather than first-install guidance.

`autoontology --credential-file FILE ...` selects a private native session. `auth
login USER [--password-file FILE]` shares the core password/login limits and writes
only safe metadata to stdout. Without a terminal, the password file must be regular,
owner-only and inside a private directory. Passwords are not accepted as arguments
or environment values. Sign out before replacing a saved file; expired/revoked
local sessions can also be removed. Existing commands fail closed without a valid
credential when configured. Explicit credentials never fall back to an unconfigured
workspace; query, stream and result-download requests attach the same validated
bearer credential. Version/help and the identity command family's own explicit
checks are separate from business command authorization.

Configured engine/model clients use an atomically installed owner-only
`security/router-secret`. It only authorizes loopback `GET /v1/models` and `POST
/v1/chat/completions`, never a user/business API or ingestion effect. It cannot be
used as a human/agent credential. Upstream chat profile keys and the managed
playground/provider probe attach it; legacy unconfigured profiles retain their
existing behavior. The key is never placed in URLs, tracked files or logs.

Actual browser acceptance used a private copy of the existing local r8 workspace.
Interactive login/error/progress, role-limited navigation, graph, confirmed logout
and the corrected operational-loading transition were captured in light mode.
Native login/whoami/original listing and a **fixed public pong** through the actual
Codex router passed. Document retrieval stayed on local Ollama; document-based
external chat was not executed after automatic approval review rejected that
transmission. The exact proposed input was frozen for user approval, separately
from these successful checks. Controlled model tests cover authenticated review,
publication and revocation during preparation; they are not real document-model
acceptance. See the private stage-11 report for source, hashes and recovery evidence.
