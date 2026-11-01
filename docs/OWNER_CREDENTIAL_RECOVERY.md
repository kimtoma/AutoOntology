# Local owner credential recovery

This ceremony recovers an existing administrator's password in a complete,
independently pinned workspace. It preserves the workspace, organization, people,
grants, knowledge and audit history. It does not rebuild missing metadata,
reenroll a replacement owner, rebind a restored directory or expose a remote
password-reset endpoint. Those distinctions are required by the
[owner recovery gate](OWNER_RECOVERY_GATE.md).

## Diagnose and recover locally

Run under the trusted OS account with the selected workspace's normal profile.
The diagnostic command does not create or migrate a store. Its public IDs and
phase are diagnostics, never an application credential or a grant of access.

```bash
uv run autoontology auth owner-status --confirm-local-owner
```

Choose the reported workspace ID and an existing active human administrator's
username. Choose a non-secret, stable request label so retries identify the same
operation. Read the new password from a private file in an owner-only directory,
or omit that option for an interactive hidden prompt with confirmation. The
minimum is 15 characters. Passwords are not arguments or environment variables.

```bash
uv run autoontology auth owner-recover owner --workspace-id "$WORKSPACE_ID" --request-id owner-reset-1 --confirm-local-owner --revoke-all-sessions --password-file "$PRIVATE_PASSWORD_FILE"
```

Both confirmations are required. This is workspace-wide credential recovery:
all nonrevoked stored credentials are revoked, all agent delegations are revoked,
and every principal's epoch advances. Other humans retain their passwords and
grants but must log in again. Agents need a new explicit delegation. No viewer is
promoted, disabled account enabled, person invented or last administrator removed.
The OS account is the recovery authority; knowing a shared gateway password,
having an application session, or being on loopback is not that proof.

An application-bound human/agent context and an explicitly selected CLI credential
are refused. There is no anonymous or authenticated HTTP owner-recovery route.
The private registry is the same OS-owned path used for
[first enrollment](OWNER_ENROLLMENT.md) and [legacy adoption](HOST_OWNER_PIN.md).
The ceremony cannot reset an unpinned legacy identity whose password is already
lost. Preserve it for verified backup/adoption recovery; do not delete history.

## After recovery

The command returns only public receipt metadata, with the same workspace and
principal IDs, operation hash, new policy version and revocation counts. It does
not print a bearer or replace a saved CLI credential file. Clear the old local
session through the existing logout command, then log in with the new password:

```bash
uv run autoontology auth logout
uv run autoontology auth login owner --password-file "$PRIVATE_PASSWORD_FILE"
uv run autoontology auth whoami
uv run autoontology source list
```

A browser's old cookie is also invalid. On the next authority verification it
must return to login; sign in with the recovered password. A previous password
cannot log in. The actual private browser acceptance reloads the page to trigger
that verification; it does not claim instant cancellation of an already delivered
response or of all running external work.

## Journal and exact resumption

The original enrollment pin remains immutable in meaning. Recovery installs host
marker protocol 2; protocol-1-only readers from earlier owner stages reject it
rather than silently ignore a pending recovery. Current code still reads genuine
protocol-1 enrollment/adoption pins, but rejects a recovery journal mislabeled
as protocol 1. Restore the verified code/database/pin set through the current
verifier; pre-marker software does not participate in this protocol.

A nested recovery journal records one target principal, operation hash, prior receipt hash, prior
policy/epoch and OS UID. It stores no password, password hash or bearer. Under
the host lock and one SQLite transaction the ceremony:

1. Verifies the original pin/DB pair, declared identity foreign keys, complete
   identity audit chain and current active human administrator grant.
2. Reserves a pending recovery. Ordinary reads/effects then fail closed.
3. Changes only that human's password, revokes credentials/delegations, advances
   principal epochs and the policy version, and appends one recovery audit event.
4. Commits SQLite, then durably finalizes the host journal with that exact event.

The actor in the receipt is `local-os-owner:<uid>`, not a password-authenticated
application human. The existing recovered principal is recorded separately. This
avoids attributing forgotten-password recovery to a human session that was absent.

After interruption, inspect `owner-status`. A verified pending entry reports
whether the DB committed. Resume the exact current operation, even if the original
non-secret request label is unavailable:

```bash
uv run autoontology auth owner-recover owner --workspace-id "$WORKSPACE_ID" --confirm-local-owner --revoke-all-sessions --resume --password-file "$PRIVATE_PASSWORD_FILE"
```

An explicit original `--request-id` may also be supplied. A mismatching request,
workspace or target is refused. Resume never starts a new recovery. Before commit,
the OS owner may choose the new password again; after commit, resume must prove the
committed new password. A repeated completed request returns its original receipt
and preserves sessions issued after it. A superseded old request cannot rotate
credentials again. A deliberate later recovery requires a new request label.

A corrupt database, missing identity rows, missing pin or pre-recovery rollback
requires verified complete restore/rebind, not this password reset. If a pending
committed recovery's new password and private password file are both lost,
preserve the pair for explicit local recovery; superseding that pending operation
is not implemented. Do not bypass it by deleting the registry or database.

## Review and effect fence

The policy version changes in the same transaction as the password and revocations.
Existing immutable case packets are retained, but prior-policy confirmation is
refused even after the same person logs in again. Prepare a fresh packet and
inspect its exact current content. Counts cover unconfirmed, unexpired packets
bound to the previous policy; some may already be stale for another reason.
Existing prepared authority bindings also require their current policy and actor.
This unit does not claim to journal or cancel every cross-resource side effect.

Normal authority reads validate the latest recovery event's pinned hash. Schema
20 adds only an index on `identity_events.sha256`; active receipt lookups use that
hash rather than scanning JSON operation fields on every UI/API request. Tests
verify the actual query plan uses the index; no latency or business-savings figure
is inferred from it. Historical migrations are unchanged.

## Backup, acceptance and limits

Checkpoints preserve pending recovery before and after DB commit, validate the
original pin and previous/current receipts, and never install authority at a new
canonical home. A paired backup's immutable reads cannot change its own hashes.
Explicit same/new-home restore and rebind remain the next implementation unit.

Acceptance includes both adopted and newly enrolled identities, real process exits
at four boundaries, concurrent native processes, invalid and superseded resume,
old human/agent credential rejection, and an exact case packet that becomes stale
then succeeds only after current human re-preparation. Native acceptance uses three
private copies of existing Korean data and checks logout/login/source access.
Four inspected light-mode captures exercise the built UI and actual native CLI;
no UI source/layout or independent menu-design change is made. Their upstream
backend is absent, so source-status warnings are labeled fixture limitations.

Failed test assumptions and controlled regressions are retained in private stage
records. An initial full Python run passed, but real native source listing exposed
a tuple-based read-only identity probe that the Row-based service tests missed.
Receipt, principal and audit readers now normalize both connection row shapes;
regression tests exercise the independent probe, native protected read and plain
SQLite checkpoint validation. Only the stronger final run and fresh native/UI
acceptance are completion evidence. Two synthetic fixture credentials appeared in a failed assertion's
header rendering; both were revoked, failed logs redacted and the assertion changed
to report only the response status. No real account credential was involved. The
previous intermittent host lock `ENOENT` was isolated on this macOS host:
concurrent nonexclusive `O_CREAT|O_NOFOLLOW` opens failed 4,996 of 10,000 times
while the directory inode stayed unchanged and the file already existed. Exclusive
creation followed by a noncreating existing-file open had zero errors in that
comparison. The actual corrected helper also passed 10,000 contended opens, with
link/permission checks preserved. No missing or unsafe file is recreated as a
reopen fallback; unexpected filesystem failures still deny access.

Normal identity remains off. Public and frozen review services are not upgraded.
The full [the public implementation checkpoint](PUBLIC_CHECKPOINT.md), owner UI
handoff, restored-home rebind and broad authority/effect audit remain incomplete.
