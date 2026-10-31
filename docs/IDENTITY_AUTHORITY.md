# Identity and authority core

P0-03 is in progress. The durable core has HTTP, native CLI and browser
session adapters, domain-selected knowledge/query views and guarded review/publication
effects. Unfiltered operational resources require **whole-workspace scope**.
Complete effect/stream/queue revocation, exact approval lifecycle and
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

- Finish remaining source/workflow/history domain checks and effect boundaries.
  Stages 14-15 project knowledge, originals/view downloads, search/SQL/SPARQL and
  saved results/conversations. The explicit route inventory denies unknown endpoints
  and requires whole-workspace scope for remaining unfiltered operational data.
  Complete native mutation/read parity and effect-boundary checks, including
  source/settings/connection-review writes and resource ownership.
- Administration now has a human UI and native commands for people, roles, password
  reset, disabling/re-enabling people, domains, source classification, preparation
  delegation, credential inspection/revocation and audit. Finish bounded credential
  renewal, remote CLI sessions, owner enrollment/recovery and approval lifecycle
  before normal-workspace activation. Domain-specific business access remains a
  separate gate from selecting a domain in administration.
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

## Domain-selected query and saved-result boundaries

Stage 15 selects the query model before retrieval, provider context, frozen SQL
catalog projection or SPARQL serialization. Frozen publication vectors are reused
only for identical selected element type/text hashes; changed scoped text has a
separate artifact-qualified fragment cache. Full publication models, indexes and
catalog files remain immutable. SPARQL never reuses the full Turtle file for a
restricted view. Query/provider and SQL waits check the selected policy epoch;
scope changes prevent subsequent output or a successful terminal receipt even
when a general query grant remains.

New configured query receipts capture immutable source and resource dependencies.
Current reads require ownership/explicit administration as well as current access
to all recorded knowledge. Broader later permission can read an older narrower
result; unknown historical bindings never become domain-authorized by inference.
Every configured message independently binds its exact stored bytes and knowledge
scope in schema v14, so removing its trace cannot remove the conversation boundary.
The migration preserves old message and receipt bytes/hashes. Old messages require
whole-workspace authority; their scope is not fabricated. Replaying a conversation
requires both query and saved-result read authority. An originating producer lease
binds new messages inside the same write transaction; ordinary callers cannot
attach a known scope to arbitrary result content. Humans can delete owned terminal
results after losing knowledge-read access, subject to current delete permission.

UI navigation permits domain queries and saved-result reads, omits whole-workspace
source/settings requests from the query page, and clears mounted work on policy
drift. These are interface affordances; direct APIs and native operations enforce
the same service boundaries. Remaining activation gates above still apply.

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

## Administration and request attribution

Stage 12 adds `/ui/access` for individually configured workspaces. Every signed-in
principal can inspect/revoke their own sessions. Only a current human administrator
can inspect another person's sessions or administer people, roles, domains, source
classifications and delegation. Unknown/expired/revoked credentials still fail at
the shared service boundary. The management packet is a coherent read transaction
containing public principals, current grants, safe delegation metadata, role/action
catalogs, domains and source bindings; it contains no password or credential hashes.

The interface shows the exact target, selected roles/actions/domains and effect
before applying a change. Pending submissions cannot be repeated. Failure keeps
the confirmation available without claiming success. A password reset invalidates
existing sessions. Re-enabling a disabled human increments the credential epoch;
it permits a new login but never restores old sessions. At least one active human
workspace administrator must remain. Agents cannot be re-enabled after delegation
revocation; create an explicitly reviewed new delegation instead.

Agent credentials appear masked in a transient browser view and can be revealed
for explicit transfer to the agent's private store. They never enter query caches,
browser storage, audit detail or logs. Closing the view removes its secret state;
the browser does not retain a downloadable session file. Native `auth delegate`
continues to write the owner-only credential file directly.

Native administration uses the same operations:

```sh
autoontology auth administration
autoontology auth domains
autoontology auth sessions                      # own sessions
autoontology auth sessions --principal ID       # human administrator
autoontology auth sessions --principal '*'      # all, human administrator
autoontology auth reset-password ID --password-file PRIVATE_FILE
autoontology auth disable ID
autoontology auth enable ID
autoontology auth revoke-session CREDENTIAL_ID
```

The existing `add-person`, `roles`, `domain`, `classify-source`, `delegate`,
`revoke-agent` and `audit` commands remain available. `enable` is a compensating
operation for human disablement, not a rollback of session revocations or audit.

Usage trace creation now rechecks `query.run` inside its write transaction and
records the verified principal, credential, workspace and policy version when
configured. Previously completed legacy receipts remain unchanged; they are not
retroactively attributed. Stage 13 adds the ownership and asynchronous revocation
boundaries below; domain-specific data access remains a separate activation gate.

After explicit user approval of a frozen 2,292-character model payload, actual
individual native CLI -> protected HTTP -> local Ollama retrieval -> Codex
`gpt-6-astra` completed the exact approved request. The first receipt exposed the
missing actor binding; it was preserved. A second call with the same approved
messages verified the corrected actor and the answer's E1 citation. The browser
then recovered that completed answer from its trace without a model call. Actual
copied-data administration verified role changes, password reset, disable/re-enable,
source classification, agent-only preparation, revoked delegation/session behavior
through the native CLI, and the audit chain. Light-mode desktop/mobile captures
and remaining activation gates are recorded in the private stage-12 evidence.

## Saved-request ownership and asynchronous revocation

Schema v13 adds immutable owner/workspace columns to conversations and traces.
Existing verified trace actors populate an ownership index without changing their
receipt JSON or hashes. Unverified historical ownership stays unknown. Historical
conversations are not assigned to an inferred author: a verified answer does not
prove ownership of every earlier message. Normal unconfigured local workspaces
retain their existing behavior; individual enrollment is still disabled by policy.

Configured list/read/continuation/write/delete checks run in the shared stores,
not just route handlers. A person sees their own records by default. Another
person's identifier returns 404 and cannot be continued, appended to or deleted.
Human administrators can explicitly inspect all owners and unknown older records
using the UI history checkbox, `ops traces --all-users` or `ops conversations
--all-users`. Inspection does not allow replying as another conversation's author.
Conversation retention is per owner/workspace; one user's activity cannot prune
another person's history. Running conversations cannot be deleted during a request.

`results.delete` is a human capability for removing one's own completed receipts or
conversations. It does not grant deletion of another person's data; that requires
current human administration. Each explicit configured deletion appends actor,
target and owner metadata to the audit chain in the same transaction. Agent
delegations cannot include this human-only capability. An agent using the native
question commands needs both `query.run` and `results.read` to execute and then
recover the resulting receipt; these are separate explicit capabilities.

Only the originating in-process producer can mutate a configured running trace.
It may record failure/cancellation after revocation, but success requires current
query authority. A trusted reaper may only interrupt a dead producer. The ownership
columns must agree with the existing hashed actor, preventing accidental index
corruption from silently changing access. Finished receipt hashes remain stable.

RAG rechecks before calling the chat provider, before each output chunk and before
successful persistence. The HTTP stream also polls authority while awaiting the
next asynchronous provider or SQL-executor result. Revocation records failure,
cancels the await, closes the provider/SQL worker and emits an authentication or
authority error. A 401 inside an HTTP-200 SSE response locks the UI and clears work
caches. The CLI preserves the request ID and does not attempt another result read
with a just-revoked credential. Structured reads recheck at their result boundary.

This does not complete cancellation of synchronous embedding/catalog/SPARQL worker
threads: they remain bounded by their transport/execution deadlines and cannot
publish a successful result after failed authority checks. Their prompt cleanup,
per-domain historical access, queue/source effects and exact approval lifecycle
remain activation gates. Existing role catalogs also need explicit policy-definition
binding in prepared approval packets, rather than relying only on grant versions.

Actual copied-data UI/native CLI acceptance used two humans, real local Ollama
retrieval and actual PostgreSQL count 40/CSV recovery. Chat output and SQL planning
were controlled local fixtures; no new external model call was made. It checked
foreign-record denial, explicit admin inspection, menu collapse, and revocation
while a controlled provider was waiting. A prior actual embedding cache was reused
only after the current model artifact, model inputs, all 806 element text hashes
and normalized vector fingerprint matched the earlier actual receipt. The initial
fresh-copy attempt had legitimately begun re-embedding legacy, unidentified vectors;
its timeout and subsequent diagnosis are preserved separately. See stage-13 evidence.

## Domain-bound graph, originals and view downloads

Stage 14 adds source-complete read projections for ontology summaries, graph,
concept lists/details and original documents. The native `ontology`, `ops documents`
and `ops original` commands use the same selection. A domain entry gate only allows
the request to select resources; it never grants an unfiltered workspace read.
Other operations retain their whole-workspace gate until their consumers enforce
the corresponding resource selection.

A class may contain definitions, aliases and evidence from several document
merges even when its `documents_source` names only the first. Its complete record
requires every contributing source, including merge/evidence dependencies.
Relations also require their endpoint dependencies. Unknown owners and missing
evidence remain unclassified. The projection does not manufacture a new partially
redacted meaning that was never reviewed. Legacy diagnostics and human notes have
no complete field provenance and are omitted from restricted read models.
Upstream Terms can span databases while legacy snapshots record one structured
owner. Structured components therefore conservatively require every included
structured source; finer cross-database field provenance and independent views
remain work rather than an inferred ownership claim.

Historical source IDs come from captured snapshot/publication bindings, never
today's same-named registry row. Legacy known IDs require every recorded source
classification, including the initial `general` classification assigned at
enrollment. New configured publication manifests pin identified input IDs and
current domain classifications. Reads require both pinned and current domains;
moving a source cannot expose older private history to its new readers. An input
with an unknown older ID is not made known by a later same-named input.
The copied normal r8 remains unmodified and its unrecorded IDs stay unknown.

Model caches hold private full models; returned models are fresh projections.
Concept-index cache keys include the projected model hash. The browser cancels
and removes work queries before mounting a changed principal/credential/workspace/
policy context. Its actual 30-second session poll also invalidates stale graph
and concept views after a classification change. Already downloaded bytes cannot
be revoked by a later policy change.

`GET /api/ontology/export` describes the current authorized view, and
`GET /api/ontology/export/{ttl|jsonld|graphml}` serializes it without reading the
unfiltered archive. Domain readers see these downloads in the sidebar, section
tabs and quick navigation. Existing full archived bundles keep their workspace
gate. The native equivalent is:

```sh
autoontology --credential-file PRIVATE_SESSION ontology
autoontology --credential-file PRIVATE_SESSION ops documents
autoontology --credential-file PRIVATE_SESSION ops original DOC_ID --out NEW_FILE
autoontology --credential-file PRIVATE_SESSION ops export-view --format ttl --out NEW_FILE
```

View downloads do not overwrite files or write into operational exports. The
final file effect verifies the selected model hash, current individual credential,
workspace, action and policy version. Drift or a mutated model aborts before the
atomic destination link. HTTP serialization also rechecks before returning bytes.
RDF comparisons verify semantic equality, not incidental blank-node ordering.

Executed local acceptance uses the copied actual model/originals, real browser
and native commands. A separately seeded r9 binding/operational pointer is a
controlled fixture, explicitly distinguished from historical adoption or approval/
publication acceptance. It checks unknown legacy bindings, authorized graph and
five originals, foreign-original and whole-bundle denial, browser/native RDF
equivalence, current classification changes and browser cache removal. Initial
enrollment's `general` history is explicitly granted in that fixture, never erased
to make a finance-only credential pass. No external model call is made.

Query/search/index/SQL/SPARQL consumers, saved-result knowledge-scope rechecks,
remaining history/source/queue/effect adapters and exact approval/recovery gates
remain in progress. Normal-workspace enrollment and public deployment stay off.
