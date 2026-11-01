# Independent local owner pin

This document records the host-marker and existing-identity adoption portion of the
[owner recovery gate](OWNER_RECOVERY_GATE.md). It does not complete first owner
enrollment, password recovery, restored-home rebinding or bounded autonomy.
Normal-workspace identity remains unconfigured until an explicitly authorized
local ceremony; the public and frozen review applications are separate.

## Trust and storage

The trusted operating-system account may pin an already enrolled workspace after
proving an active human administrator password and a current whole-workspace
administrator grant. An application session, agent delegation or loopback HTTP
request is not host-owner proof. There is no new anonymous HTTP endpoint.

The registry is under the OS account's passwd home at
`.local/share/autoontology/owner-registry`. Neither a request, environment `HOME`
override nor a workspace setting selects its location. Each private entry is
keyed by the canonical database path and binds organization/workspace IDs, the
operation and human IDs, phase and an immutable audit-event hash. Passwords,
bearer credentials and password hashes are not stored in the pin.

Directories are owner-only, entries and lock files are owner-only regular files,
and symlinked paths, hard-linked entries, unsafe permissions, oversize/invalid
payloads and database mismatches are refused. Registry reads do not create files.
A process-local mutex and OS file lock serialize the complete local ceremony.
Atomic replacement and file/directory fsync provide its durability boundaries.
The OS owner can deliberately remove or change both registry and database; this
boundary does not claim protection against that owner.

## Supported native ceremony

Use the selected workspace's ordinary environment/profile. Read the password
from a private file in a private directory, or omit `--password-file` for a hidden
terminal prompt. Do not put the password in an argument or environment variable.

```bash
uv run autoontology auth owner-adopt owner --confirm-local-owner --password-file "$PRIVATE_PASSWORD_FILE"
```

The command validates the existing identity snapshot, declared identity foreign
keys and the full identity audit chain, then rechecks the human and current grant
inside the write transaction. It preserves workspace/principal IDs and knowledge.
It returns stable public JSON metadata and does not issue or print a credential.
Use the existing `auth login` command for a separate session.

The command refuses an explicitly selected application credential. In-process
execution already bound to a human/agent application session is also refused.
The password proof and the trusted local invocation are independent requirements.

## Recoverable adoption operation

1. Reserve a pending host pin with one operation ID while holding the local gate.
2. Commit one attributable `identity.owner_pinned` audit event in SQLite.
3. Finalize the pin with that exact event hash.

Ordinary reads and effects reject a pending pin. After an interruption, preserve
its files and resume explicitly with the same verified human administrator:

```bash
uv run autoontology auth owner-adopt owner --confirm-local-owner --resume --password-file "$PRIVATE_PASSWORD_FILE"
```

Resume reuses the operation and committed event rather than creating another
owner, workspace or duplicate adoption event. A completed matching operation is
idempotent. Corrupt/incomplete metadata cannot be repaired by adoption.

A fully missing database, an earlier unpinned database or a different workspace
alongside an existing pin fails closed. Previously created Store instances also
refuse to recreate a missing pinned database. If the pin is missing but the
SQLite audit still proves prior pinning, the workspace also requires recovery.
Pre-marker identities remain readable under their existing policy until explicitly
adopted; unadopted historical databases have no independent loss protection.

## Checkpoint and restore boundary

New private checkpoints preserve an active entry as `payload/host-owner.json` and
validate its exact SQLite/audit pairing before staging. A mismatched pre-pin
SQLite payload is rejected even when individual payload hashes match.
The original immutable checkpoint must remain unchanged after a restore drill.

Staging into a new directory copies the entry as evidence; it does not install it
into the host registry or grant the new canonical home authority. Serving a copied
pinned database without a supported local rebind is intentionally denied. The
complete explicit rebind/credential-recovery ceremony remains implementation work.
Do not manually copy a marker into the live registry as a documented workaround.

The actual CLI acceptance uses a private copy of existing SQLite knowledge,
generated private fixture credentials, real native adoption/login/status/whoami,
controlled database loss and an explicitly labeled same-home SQLite restoration.
That restoration is test-fixture code, not an exposed product recovery command.
No model or PostgreSQL writes are involved in this acceptance.

## Evidence and remaining work

The private stage-27 record contains exact regression results, before/after
knowledge hashes, native CLI evidence, checkpoint and restore results and failure
history. No UI source/layout change is made in this stage; no new UI capture is
presented as evidence. Existing recovery UI handles the shared denial code.

Remaining: atomic initial enrollment before any identity commit, supported local
first-owner/session setup, explicit credential/password recovery with revocation
and pending-packet invalidation, canonical-home rebind, pending-enrollment backup
semantics, actionable UI/native recovery, and the full owner gate acceptance.
The wider authority/effect journal, consumer contracts and broader development scope
remain governed by [the public implementation checkpoint](PUBLIC_CHECKPOINT.md).

## Subsequent first-owner unit

[Journaled native first enrollment](OWNER_ENROLLMENT.md) now reserves the host pin
before the first identity transaction and supports explicit resume. Its checkpoint
contract covers interrupted first-enrollment pairs. The remaining scope listed
above is the preserved stage-27 boundary; password recovery, new-home rebind and
full owner-gate/UI acceptance are still pending.


## Subsequent credential recovery unit

Stage 29 adds [explicit local credential recovery](OWNER_CREDENTIAL_RECOVERY.md)
for a complete pinned workspace, with current-admin verification, workspace-wide
session/delegation revocation, policy-bound review invalidation and resumable
receipts. It does not reconstruct metadata or rebind a restored canonical home.
Historical remaining-scope notes above describe their original stage boundaries;
the full owner/effect gate and Knowledge OS roadmap are still incomplete.
