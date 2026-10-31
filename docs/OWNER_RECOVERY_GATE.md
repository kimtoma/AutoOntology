# Owner enrollment and recovery gate

Audit reference: application `56af1c0`. This is a confirmed incomplete requirement,
not a released recovery implementation. The 20:00 review copy stays frozen.

## Observed behavior

Controlled stores use synthetic names/passwords, with no runtime, network, model
or PostgreSQL operations. A disposable direct SQLite connection with foreign keys
disabled represents a partial restore; this is not an application-authorized
operation or a remote attack path.

| Missing metadata rows | Current probe and unauthenticated settings gate | New enrollment |
| --- | --- | --- |
| Workspace state only | Correctly denies with `identity_configuration_missing` | Correctly denies |
| Workspace state and principals | Incorrectly reports unconfigured and grants legacy local settings authority, despite surviving credentials/grants/audit | Fails later on the existing default domain's unique constraint |
| Workspace state, principals and domains | Incorrectly grants legacy local settings authority despite surviving history | A replacement workspace owner can be enrolled |

The first attempt to delete every identity table stopped at the existing
append-only audit trigger. That protection is effective. The narrower successful
reproductions preserve that audit and demonstrate that the unconfigured checks do
not consult all surviving managed-workspace evidence. They do not prove deletion
through the API or resistance against a malicious operating-system account.

`security.internal.identity_enabled`, `security.context.current_actor`,
`Identity.status` and `Identity.bootstrap` each distinguish enrollment using state
and/or principals. There is no supported CLI bootstrap/recovery command, and
`web.identity_api` correctly exposes no anonymous enrollment. Current review and
configured acceptance setup therefore call the host-owned Python service directly.
The application currently reports that local recovery is required without
providing the complete product recovery workflow.

## Required implementation unit

1. Share one enrollment-state decision across CLI, HTTP, workers and effects.
   Any surviving managed metadata must prevent a legacy-authority fallback.
   Read-only status must never create or migrate a store.
2. Persist a private host-owned enrollment marker independently of the database.
   Bind the workspace identity; an older pre-enrollment database or a database
   missing alongside its surviving marker must fail closed. Do not claim this
   protects against an owner who deliberately removes both.
3. Model first enrollment as an idempotent recoverable operation: reserve marker,
   commit identity, finalize marker. Crash or competing enrollment at any boundary
   cannot create a second owner or silently re-enable legacy authority.
4. Provide a named local CLI enrollment command using a private password file or
   an interactive hidden prompt. Neither raw passwords nor credentials belong in
   arguments, terminal output, logs, tracked files or operation receipts. Return
   stable machine-readable public metadata and save a session using existing
   owner-only credential-file checks.
5. Keep remote anonymous enrollment closed. Give the UI actionable enrollment and
   recovery status; any browser handoff must require a local one-use, expiring
   proof and bind the exact workspace/operation. Never treat loopback HTTP alone
   as operating-system-owner proof.
6. Distinguish credential/password recovery from corrupt identity metadata.
   Recover the same verified workspace using an explicit local ceremony or a
   verified complete backup; never invent principals, grants or approval history
   from incomplete rows. Revoke prior sessions/delegations as appropriate, bump
   policy, expire pending packets and record an attributable recovery receipt.
7. Include the marker, pending enrollment and recovery receipts in checkpoint and
   restore contracts. Validate the restored database/marker pair before serving;
   handle pre-marker legacy copies through an explicit local adoption procedure.
8. Test concurrent enrollment, each crash boundary, stale/missing/mismatched
   markers, retained and fully absent metadata, rollback to a pre-enrollment copy,
   symlinks/permissions, agent/remote denial, last-owner lockout and old-session
   invalidation. Exercise the actual local CLI and UI handoff in a private copy,
   with backup restore and light-mode flow captures before release.

This gate precedes policy-authorized autonomous decisions/publication. Closing
only the two-table reproduction is useful hardening but does not satisfy the
independent-marker, owner-recovery and full product enrollment requirements.

The subsequent [shared enrollment-state boundary](ENROLLMENT_STATE.md) implements
the database classification and corresponding UI cache/exception behavior.
The observations above remain the preserved audit at 56af1c0; the independent
host marker and supported owner ceremony are still implementation work.
