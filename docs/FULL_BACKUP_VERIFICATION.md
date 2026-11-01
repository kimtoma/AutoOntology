# Complete offline backup verification

The native local-owner command is an inspection prerequisite for restored-home
activation. It does **not** transfer authority, revoke sessions, change a host
pin, start a runtime or make copied configuration safe to execute.

```bash
python -m autoontology auth backup-verify /absolute/private/checkpoint \
  --confirm-local-owner
```

Run it with the trusted installed development Python. No password is required.
Do not use application `--credential-file` or an authenticated execution context:
application grants do not authorize OS-owner recovery. There is deliberately no
HTTP mutation or inspection route exposing private host backup paths. The command
does not construct Settings or Store for the currently selected workspace; even
an absent/corrupt current home is not needed to inspect a frozen backup.

## Accepted format and checks

Version 1 is the full isolated-runtime format created by
`scripts/local_ops_checkpoint.py create`, not a SQLite-only backup. Inspection:

- Bounds the manifest to 2 MiB and the payload to 40,000 file/directory entries;
  rejects duplicate JSON keys, unexpected fields, noncanonical/escaping paths,
  wrong types, unsupported versions and unrelated declared database names.
- Requires the SQLite database, source archive, runtime settings/controller,
  exports directory, PostgreSQL 17 control/configuration and base/WAL directories.
  Checks exact file inventory and SHA-256 twice around format/identity inspection.
  Symlinks, hardlinks, special files and non-owner/private entries are rejected.
- Refuses SQLite WAL/SHM sidecars and PostgreSQL live/recovery signals. Uses
  `mode=ro&immutable=1`, a read transaction, query-only/trusted-schema-off mode,
  SQLite integrity and foreign-key checks, enrollment classification and the
  complete configured identity audit chain. It never migrates or reaps jobs.
- Checks the exact frozen database/host-pin header and existing enrollment or
  credential-recovery receipt. It never canonicalizes, opens or reads the
  original home/registry. A disconnected, corrupt or unsafe original path cannot
  make frozen-pair inspection depend on that live path. Ordinary live host gates
  retain native filesystem comparison; this literal parser cannot activate a pin.
- Inspects an uncompressed Git PAX source archive without extraction/import;
  requires its declared commit comment to match the manifest, bounded entries,
  regular files/directories and no duplicate/escaping/link/special/sparse entries.
  Archive bytes are capped at 128 MiB; member bytes at 1 GiB.

The result contains hashes, counts, schema version and non-secret identity IDs.
It never prints environment values, session secrets, passwords, database contents
or archived controller output. Historical partial backup drill fixtures still
use the private script's smaller `verify` contract; they cannot pass this full
product-level inspection.

## Meaning and limits

`verified: true` means consistency with the **supplied private manifest** and
these offline structural/SQLite checks. A hash or Git archive comment is not a
signature or proof that the archive is the trusted commit's exact tree. Verify
provenance and trusted source separately. PostgreSQL file hashes/control presence
are not logical row verification, successful crash recovery or a runnable cluster.
The report explicitly retains:

```json
{
  "integrity_only": true,
  "activation_supported": false,
  "host_authority_checked": false,
  "runtime_started": false,
  "postgres_started": false,
  "postgres_logical_contents_checked": false
}
```

A copied `local.env`, controller, job PID/credential directory or PostgreSQL
configuration can still point at the original system. Do not execute them to
activate a restore. The next implementation units must stage fresh local ports,
paths and trusted configuration, reserve independent destination authority,
fence the original home, revoke backup credentials/delegations, pause workers,
record the local-owner transfer and prove actual work after a new login. Those
units and the broader development roadmap remain open. This command is not the complete owner
gate or complete restoration acceptance.

Older full checkpoints with a host-pin header spelling different from their
manifest are rejected rather than silently merged by case folding. Preserve
both original files for deliberate owner-reviewed recovery. Newly created
checkpoints record the pin's actual literal header, preserving legacy key identity.
