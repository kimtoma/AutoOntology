# Restored-home authority transfer

Stage 34 extends [inactive staging](RESTORE_STAGING.md) with the first local
transfer from an enrolled version-1/2 source to its version-3 staged runtime.
It retires the original authority before a restored administrator can sign in.
Stored published knowledge, evidence, originals, exports and history remain
readable. **Physical catalog/source review is still pending**: questions,
preparation, publication, source changes and collection execution are held.
This is not a completed Complete recovery gate or a new business approval.
The subsequent [reviewed-read stage](RESTORED_READ_REVIEW.md) adds exact physical
target confirmation and actual questions/SQL while retaining the remaining holds.

## Local owner workflow

Stop the original app, backend, collection workers and its PostgreSQL cleanly
using that runtime's own controls. This command never stops them for you.
Prepare an owner-only password file (mode 0600) containing a new password of at
least 15 characters, or enter it at the secure terminal prompt. Do not put the
password in shell arguments, an environment variable or a tracked file.

```bash
python -m autoontology auth restore-activate /private/new-runtime owner \
  --workspace-id <existing-workspace-id> --password-file /private/new-password \
  --confirm-local-owner --confirm-source-offline --revoke-all-sessions
python -m autoontology auth restore-status /private/new-runtime --confirm-local-owner
# After interruption repeat the activation with the same destination/user and --resume.
python /private/new-runtime/control.py start
python /private/new-runtime/control.py status
python /private/new-runtime/control.py stop
```

All three confirmation flags are required. The selected username must already
be an active whole-workspace human administrator in the verified backup. The
operation creates no replacement principal and accepts no application credential
as local-owner proof. Neither HTTP nor the browser accepts a host backup path.
The normal unenrolled preview is not enrolled by this workflow.

The offline attestation is supported by local checks: actual source profile/home,
three idle source and destination ports, known runtime/lease PIDs, a stopped PG17
cluster and a nonblocking SQLite writer lock. Missing/corrupt source metadata is
not used to reconstruct identity; the independent source pin and complete paired
backup remain required. These checks are not a claim to detect every external
process. Resolve a busy/unknown source before confirming it offline.

## Durable authority transition

A private per-workspace host journal is independent of both replaceable SQLite
files. It binds the exact source/staged pins, destination, operation, manifest,
owner, policy, OS UID and runtime configuration hashes.

| Durable phase | Original | Restored home |
| --- | --- | --- |
| Prepared | Exact prior source binding only | Inactive |
| Pending | Fenced | Inactive |
| Active | Retired | Active historical-read/identity scope |

Version-4 source/destination pins prevent older supported readers from treating
the new protocol as legacy authority. Store connections recheck the external
fence before commit; transactions admitted before a fence cannot commit through
it. A prepared transfer also prevents competing password recovery from rewriting
the reserved source pin. Unknown changes are preserved and fail closed.

The destination transaction revokes every copied session/delegation, disables
other active principals without deleting history, increments epochs, sets the
chosen administrator's new password and advances the backup policy. Host file
roots are revoked; schedules are paused; queued/running jobs, workers and source
leases are stopped in metadata. Historical completed/failed runs and their steps
are preserved. The recorded policy is not a claim to reconstruct a lost source's
latest policy high-water mark.

The attributed `identity.workspace_restored` receipt identifies the local OS owner,
not a fabricated logged-in business approver. Existing approval history is kept;
it is not renewed. Runtime files are installed and verified before the master
journal grants the destination. A committed interruption resumes the same event
and requires the committed password; it cannot silently mint another owner.

## Runtime and subsequent credential recovery

The generated controller imports the currently trusted local application, never
the archived controller/source. It validates all pinned runtime files, scrubs
conflicting runtime/provider shell settings, verifies the installed upstream
revision against the gitlink, and uses only this runtime's loopback ports and PG
folder. Startup failure stops a PG cluster started by that attempt. Stop/status
can inspect the exact child processes without opening unreadable SQLite metadata.
The current Mac adapter uses fixed Homebrew PostgreSQL 17 paths.

Sign in with the new individual account. `/api/identity/me` returns readable
capabilities while omitting held operations. The light UI explains the connection
review hold, links to access/history, labels the local-owner transfer audit event,
and keeps stored knowledge and downloads available. A valid question request
returns `409 restored_catalog_review_required` before any model call.

Later same-home `auth owner-recover` uses the existing explicit local-owner and
workspace-wide revocation ceremony while preserving the version-4 transfer root.
It invalidates old passwords/sessions and keeps the catalog hold. Do not downgrade
the pin or edit the host journal to make it ready. No supported catalog-release
operation is supplied in this stage.

## Acceptance and remaining work

Automated transfer tests cover seven interruption boundaries, source fencing,
old credentials, database rollback, invalid stages, competing owner recovery,
historical rows, worker pause, capability discovery and later password recovery.
Actual native acceptance on a separate full Korean sample uses process exit 74
after the destination commit, resumes in a new process, starts the real PG/app/
backend and serves authenticated published r8. A subsequent native password
recovery invalidates an established HTTP session. No model call is needed.

Actual light-mode browser acceptance covers login, populated operations, 3D graph,
original evidence, original-file and published GraphML downloads, audit and held
questions, including 390px layout. Screenshots and hashes are private in the
stage record. Native tests, browser checks and the normal-data checkpoint drill
are distinct evidence; none replaces the other.

Next close physical SQL catalog/source-locator binding and a reviewed new-home
query path, then finish the first Hanbit Definition/KnowledgeContract consumer
slice. Repeated transfers from version-4 homes, legacy backup compatibility and
pending-recovery supersession remain open. Do not report restored answers/SQL or
broader development completion from historical reads alone. Real receiving service, approver,
baseline and business outcomes are still unspecified; the user explicitly chose
the fictional sample for continued engineering validation. No public deployment
or independent UI/UX planning-menu migration is part of this stage.
