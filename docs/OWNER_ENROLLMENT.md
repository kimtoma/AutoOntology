# Recoverable first-owner enrollment

The native `auth enroll` ceremony reserves a host-owned workspace identity before
committing any identity rows. It completes the first-enrollment unit of the
[owner recovery gate](OWNER_RECOVERY_GATE.md), alongside the existing
[legacy adoption ceremony](HOST_OWNER_PIN.md). Password recovery, restored-home
rebinding and actionable UI handoff remain unfinished. This is not a full owner
recovery release or permission to expose application ports.

## Local operation

Use the selected workspace's existing profile, under the trusted OS account.
Choose a human username, display name and workspace name. The individual account
password must have at least 15 characters; it is independent of a gateway's shared
password. Read it from an owner-only file in a private directory, or omit the file
option for a hidden terminal prompt with confirmation. Never use an argument or
environment variable for the password itself.

```bash
uv run autoontology auth enroll owner --workspace "Knowledge OS" --name "Owner" --confirm-local-owner --password-file "$PRIVATE_PASSWORD_FILE"
uv run autoontology auth whoami
```

The explicit confirmation represents the trusted local OS invocation. An
application bearer credential, an agent context, a browser request or a loopback
address does not authorize enrollment. No anonymous HTTP enrollment route exists.
The command does not change model selection, call a model or rebuild knowledge.

Existing complete pre-marker identities must use `auth owner-adopt`, followed by
ordinary login. Enrollment never replaces an existing principal or repairs
incomplete identity metadata. The original low-level `Identity.bootstrap` remains
an internal, host-trusted legacy/test setup helper; no production adapter calls it.

## Durable operation and resumption

1. Under the shared process/OS lock, verify an untouched identity snapshot.
2. Reserve one private pin containing operation, workspace, organization, principal
   and default-domain IDs, plus the public enrollment names. No password, password
   hash or bearer credential is stored in that pin.
3. In one SQLite transaction, preserve all existing knowledge, seed those exact
   IDs and the current owner grant, bind existing sources to the default domain,
   and append linked bootstrap and owner-pin audit events.
4. Commit SQLite, then atomically finalize the host pin with the exact event hash.
5. Only after activation, issue and save a session using the existing owner-only,
   exclusive credential-file contract. Return public JSON metadata and the file
   path, never its bearer value.

Pending pins deny normal status, login, reads and effects. Preserve the database
and pin after interruption, and repeat the original inputs with explicit resume:

```bash
uv run autoontology auth enroll owner --workspace "Knowledge OS" --name "Owner" --confirm-local-owner --resume --password-file "$PRIVATE_PASSWORD_FILE"
```

After a committed identity exists, resume must prove its current password and
current human/admin status; it verifies the identity foreign keys and complete
audit chain. It reuses the same operation and IDs. Before SQLite ever committed,
no password verifier exists: the trusted local resume selects the initial
password while retaining the exact reserved public identity. This is not password
recovery for an active account. A missing database alongside any pin is refused;
resume never recreates it.

Concurrent commands serialize and cannot create a second owner. Repeating a
completed matching enrollment verifies it and reuses a valid saved owner session.
It never replaces a different, expired, malformed or unsafe credential file.
Use the supported logout/login flow to select a session rather than overwriting
files manually.

A session-save error revokes the just-issued credential while leaving the
activated owner intact. Correct the storage problem and log in or repeat the
matching active ceremony. Arbitrary process termination between session issuance
and file persistence is not claimed to be a journaled session recovery feature.
That wider credential/effect recovery remains part of the owner gate. Native
owner commands mask SQLite/OS error details, and CLI exception rendering omits
local variables containing hidden input.

## Backup and restore contract

The private checkpoint verifies active pin/DB pairs and interrupted first-owner
pairs both before and after SQLite commit. A pending backup remains pending;
verification/staging neither installs it in the host registry nor enables normal
access. A committed pending pair must contain the reserved IDs and both audit
events. Surviving partial rows, a pin without its database, or a pinned database
without its pin are rejected even when individual payload hashes match.

Immutable backup reads do not create WAL sidecars. Staging always uses a new
folder; its pin is evidence for a future explicit rebind, not authority for that
new canonical database path. Incomplete legacy-adoption checkpoints are still
refused; only completed adoption is supported in that backup path. Original
checkpoints remain unchanged after verification and recovery drills.

## Acceptance and limits

Tests cover every named interruption boundary with exceptions and actual process
exit, actual competing processes, invalid input/password files, app/remote
rejection, stale/missing/rolled-back metadata, stable database-error receipts,
credential save failure, and interrupted checkpoint verification/staging.

Native acceptance uses three private copies of existing Korean knowledge: fresh
enrollment, precommit interruption and postcommit interruption. Real CLI resume,
status, session reuse, whoami, logout and login preserve all 65 nonidentity tables.
Private evidence records source, tests, failure history, preservation hashes and
the local code/data checkpoint. No UI source/layout changes or new UI captures
occur in this unit. Normal-workspace identity stays unconfigured; the frozen
review and public application are not upgraded.

One repeated focused concurrency run hit `ENOENT` while creating a host lock file.
It failed closed and did not create a replacement owner. A separate diagnostic
with 48 native processes did not reproduce it; the cause is not established.
The failure and raw diagnostics remain in private evidence and are an unresolved
release-readiness item, not proof that a retry fixed the cause. Preserve the pin
rather than deleting it to bypass a failed ceremony.

Remaining owner gate: credential/password recovery with revocation, policy and
packet invalidation; explicit same/new-home recovery and rebind; actionable UI
handoff and light-mode acceptance; and the remaining broad authority/effect audit.
The [the public implementation checkpoint](PUBLIC_CHECKPOINT.md) remains the full Knowledge OS scope.


## Subsequent credential recovery unit

Stage 29 adds [explicit local credential recovery](OWNER_CREDENTIAL_RECOVERY.md)
for a complete pinned workspace, with current-admin verification, workspace-wide
session/delegation revocation, policy-bound review invalidation and resumable
receipts. It does not reconstruct metadata or rebind a restored canonical home.
Historical remaining-scope notes above describe their original stage boundaries;
the full owner/effect gate and Knowledge OS roadmap are still incomplete.

The subsequent credential-recovery stage isolated the concurrent lock-creation
ENOENT on this Mac and switched the shared writer to exclusive creation followed
by a noncreating reopen. See the current [recovery evidence and boundary](OWNER_CREDENTIAL_RECOVERY.md);
the older unknown-cause paragraph above is the preserved stage-28 observation.
