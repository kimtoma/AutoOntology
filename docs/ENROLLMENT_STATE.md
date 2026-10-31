# Shared enrollment-state boundary

The database part of the owner-recovery gate now has one read-only classifier.
`Identity.status`, the identity service's internal state check, local bootstrap,
the native CLI probe and authenticated effect context use that same decision.
See [the full owner-recovery acceptance gate](OWNER_RECOVERY_GATE.md); this is an
increment toward that gate, not a completed enrollment/recovery product.

## Decisions

| Database evidence | Result |
| --- | --- |
| No database, or genuine pre-identity legacy schema without identity metadata | Preserve the explicit existing local workflow; status does not create/migrate it |
| Complete modern identity schema, empty identity tables, no prior identity event sequence | Unconfigured local workspace |
| Valid workspace state and surviving principals | Managed workspace; current individual authorization is required |
| Missing modern tables, incomplete state columns, malformed workspace IDs/policy fields, or workspace state without principals | HTTP/CLI/effect denial: `identity_configuration_missing`, status 503 |
| Missing state with any surviving identity rows, including credentials, grants, source-domain history, login limits or audit | Same recovery denial; never fall back to legacy authority |
| Active authenticated execution changes or loses its bound workspace | Preserve the existing `execution_workspace_mismatch` denial |

Every classification runs within one caller-owned SQLite read or write
transaction. Metadata observations therefore share a snapshot. The classifier
does not repair, migrate, delete or rewrite any data. A denied local bootstrap
preserves the surviving history; it cannot create a replacement organization.

## UI behavior

The ordinary HTTP and SSE API adapters report `identity_configuration_missing`
to the identity gate. The gate immediately unmounts protected work, cancels its
requests and discards business-query and current-session caches. It shows the
existing access-unavailable/recovery guidance. It does not wait for the periodic
session refresh to hide old content.

Retry verifies status first, then reacquires the current session before mounting
work again. A session that previously used managed identity cannot downgrade to
an unconfigured local workspace after retry. Initial unconfigured local installs
keep their existing behavior. Credentials absent from a restored backup require
login; this is verified session behavior, not a new password-recovery mechanism.

The private built-UI acceptance uses a copied SQLite workspace, normal browser
login, explicitly induced metadata loss and a controlled same-workspace SQLite
backup restoration. It invokes no model, performs no PostgreSQL writes and
changes neither the frozen 20:00 review copy nor the public service. Light-mode
flow captures and exact regression/recovery results live in the private stage
record. SQLite's backup API is a test fixture, not an exposed recovery endpoint.

## Remaining boundary

A fully missing database or rollback to a genuinely empty pre-enrollment copy
can remove all database evidence. The independent host marker, first-enrollment
journal, supported owner enrollment/recovery ceremony and marker-aware backup
validation remain required. The trusted operating-system owner can already alter
local files; this database guard makes no claim to resist that owner. It prevents
partial identity loss from silently granting application-level local authority.

## Recovery storage

The isolated development checkpoint tool supports `--copy-mode clone` on macOS.
It requires native APFS copy-on-write cloning and refuses a full-copy fallback;
`auto` retains the cross-filesystem copy fallback, while `copy` explicitly makes
full copies. Clone targets contain independent file content when either copy is
subsequently changed. Integrity verification, private permissions, immutable
originals and restoring only into a new directory remain mandatory. SQLite still
uses its online backup API; source archives are normal git archives.

This avoids allocating another complete PostgreSQL cluster for each unchanged
development checkpoint when disk space is tight. It does not free existing files
or replace independent off-machine backups. Eight new copy/cache tests supplement
the three existing checkpoint tests; the stage also requires a real PostgreSQL
restore and re-verification that the original checkpoint hashes did not change.

New checkpoints exclude exactly `workspace/kordoc` and
`workspace/run/codex-home`: installed tool caches and the ephemeral Codex home
must not dereference the host login or executable symlinks into a data backup.
Originals, exports, processing receipts, ordinary run profiles and the identity
database remain included. Codex recreates its isolated home from the host login;
use the existing document-tool setup procedure before processing new documents
on a restored workspace without its tool cache. Other unexpected file symlinks
are refused. Earlier private checkpoints are retained unchanged.
