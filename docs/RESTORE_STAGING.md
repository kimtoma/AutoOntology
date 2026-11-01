# Inactive restored-runtime staging

`auth restore-stage` prepares a new **inactive** local Codex/Ollama runtime from a
complete enrolled backup. It does not retire the original, revoke credentials,
select a new administrator or start services. The subsequent [authority-transfer stage](RESTORE_AUTHORITY_TRANSFER.md) adds
source retirement, owner relogin and historical reads. Physical consumers remain
held there; Complete recovery is not complete.

```bash
python -m autoontology auth backup-verify /private/checkpoint --confirm-local-owner
python -m autoontology auth restore-stage /private/checkpoint /private/new-runtime \
  --workspace-id <existing-workspace-id> --request-id pilot-restore-01 \
  --ui-port 7850 --backend-port 3151 --postgres-port 55435 --confirm-local-owner
python -m autoontology auth restore-status /private/new-runtime --confirm-local-owner
# After interruption, repeat the exact stage command with --resume.
```

The destination must be new, inside an existing owner-only parent. All three
ports must be distinct, free literal-loopback ports above 1023, and different
from the archived runtime's ports. There is no fallback. The original home,
exports, PostgreSQL data and app directories cannot contain the destination.
`clone` is the default and requires macOS APFS clone support; `--copy-mode copy`
is an explicit full-copy choice. Never silently allocate a second full cluster.

These are local OS-owner operations. Confirm explicitly, supply the existing
workspace and stable non-secret request ID, and omit application credentials.
Authenticated execution contexts are rejected by the common operation. No HTTP
path accepts a host backup/destination path. The currently selected home is not
opened or migrated to authorize staging.

## Authority and retained data

The complete offline verifier first checks the input. The separately trusted
current source pin must be active version 1/2, with the same workspace,
organization and original root enrollment/adoption receipt as the frozen pair.
Its current password recovery must not be pending. The source SQLite metadata
itself may be lost or corrupt: staging reads the independent pin and the good
backup, not a fabricated replacement identity. An unpinned legacy backup is not
automatically adopted. Normal preview identity remains intentionally unconfigured.

Before copying a database, the operation durably reserves an independent
version-3 `restore` marker outside the replaceable workspace. Reserved/staged
markers cannot serve or be rewritten by ordinary enroll/adopt/password recovery.
All Store constructors/connections and identity probes stop before opening a
reserved DB, including after deletion or a blank-database rollback. Native owner
status reports inactive state without opening SQLite. Supported previous v1/v2
readers reject the new protocol discriminator.

The active source owner lock serializes v1/v2-source staging requests. Independent
destination markers index each workspace/request binding; the same request cannot
move to another destination even if all files in the first destination are lost.
Different explicit requests may prepare multiple inert copies. This is not the
future single-active-home transfer journal: the source stays the only active
binding throughout this unit.

## Files and settings

- `recovery/input/` retains every verified input file, including original env,
  controller, PID/security files and PostgreSQL config, privately and unchanged.
  It is an archive, never an execution home or source import path.
- New `workspace/`, `exports/` and `postgres/` retain data bytes and original-file
  modes. Effective workspace run/security directories start empty; copied
  settings.json is retained only in the input archive.
- Fresh local.env uses the new home/export/env paths and three ports, local
  PostgreSQL user, Codex chat and existing local Ollama embedding model. Public,
  tunnel, demo-secret and unrelated env settings are not promoted. Dotenv values
  are parsed without interpolation or execution. Archived Codex binary paths are
  replaced by the current host's Codex executable; it is not called here.
- Fresh PostgreSQL config listens on 127.0.0.1 with no Unix socket, uses UTC and
  local-user trust matching this Mac adapter. No copied include/data_directory,
  auto.conf commands, HBA, ident or postmaster.opts becomes effective. PG is not
  started by staging. Other deployment/database profiles are unsupported here.
- `profile.json` sets `launch_authorized: false`. Fresh control.py is a guard
  which returns `restore_activation_required` for startup. It does not load an
  env, import archived code or inspect copied PIDs. A future trusted activation
  controller must scrub conflicting shell settings before resolving the profile.

Per-file copies go through deterministic operation-owned temporary paths, private
from creation, with hash checks, fsync and atomic installation under the target
lock. Resume skips only matching files and redoes only its known incomplete
copy. It never overwrites a mismatching existing file. Unknown files prevent
finalization and are retained. File/directory inventory and profile are frozen in
a bounded receipt whose digest is held by the independent marker. A changed DB,
profile, copied input, removed empty directory or receipt denies status/resume.

The reservation binds manifest/source-pin/backup-pin/builder digests, OS UID,
workspace/org, request/operation, destination, ports and copy mode. Resume must
match them. Builder changes require deliberate compatibility handling, rather
than silently continuing unknown code. Source-pin changes remain visible in
status and cannot silently change an existing stage binding.

## Results and remaining work

`phase: staged`, `integrity_verified: true` means the reserved artifact matches
its receipt. `activation_supported: false`, `runtime_started: false` and
`source_authority_retired: false` remain explicit. A free-port probe is not a
reservation against unrelated future processes; activation must check again.
The trusted OS account can still use raw filesystem/database tools and remains
outside application delegation boundaries.

Next: a durable transfer journal and source fence; existing human-admin selection;
backup credential/delegation/packet revocation; worker pause; new owner login;
explicit physical catalog/locator rebinding while retaining historical approvals;
and real graph/original evidence/completed answer/SQL/download on the activated
runtime. Restored active homes must support subsequent owner recovery, backups,
repeat transfers and interruption handling. Legacy manifest compatibility,
pending-recovery supersession, UI handoff and the broader development roadmap also remain.
