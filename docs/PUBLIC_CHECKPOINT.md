# Public implementation checkpoint — 2026-11-01

A subsequent user-authorized [side-by-side Cloudflare deployment](../deploy/cloudflare/VERSIONS.md)
lets visitors choose classic and Ontology Ops. The source-validation checkpoint
below remains unchanged.

This is an incomplete local-development alpha (`0.3.0a1`). This source snapshot
adds the implemented features below to the earlier published checkpoint. It does
not deploy a service, change existing user data, or establish production readiness.
The normal local preview remains available at `http://127.0.0.1:7830/ui/?lang=ko`
on the development machine; a fresh clone does not include its runtime or data.

## Implemented additions

- Independent host-owned workspace pins, native first-owner enrollment, existing
  owner adoption, current-admin credential recovery and explicit interruption resume.
- Canonical filesystem identity checks for protected native and web operations.
- A frozen-data SQL comparison through UI, native CLI and REST, using fictional
  Hanbit data. Submitted/reference SQL is synthetic; results are not business
  approval, measured model performance or observed time savings.
- Offline whole-backup verification, independently reserved inactive restored
  runtimes and explicit source-to-destination authority transfer.
- Native review of a restored local SQL target and deliberate confirmation before
  questions/SQL are enabled. Historical approval gaps remain explicit. Collection,
  builds and other effects remain held; they are not authorized by a read review.
- Saved answer/SQL provenance and result downloads retain the shared authority
  boundary. Losing the active physical-review proof holds queries while keeping
  permitted historical-read discovery available.

The generated UI bundle is committed alongside its source. No menu redesign or
independent planning prototype is substituted for the implemented application.

## Validation evidence

The unchanged application code and generated bundle were validated locally:

- Python: **1,943 passed, 67 skipped**, with one dependency warning (574.90 s).
  Skipped integration tests are not claimed as executed.
- UI: **211 passed**. Ruff lint/format, UI build and bundle-license checks passed.
  UI lint exits successfully with an existing render-purity warning.
- An isolated full fictional Korean-data copy exercised actual process-death and
  resume, a new PostgreSQL target, wrong-target/schema-drift rejection, completed
  model answer with original evidence, SQL customer count **40**, and identical
  saved CSV downloads through UI and native CLI.
- Source, PostgreSQL, SQLite and originals were restored and compared separately.
  Screenshots, runtime archives, credentials and private verification logs are not
  distributed in this repository. These are local results, not hosted-CI results.

## Scope and continuation

Development is paused after this checkpoint. Customer approval, organizational
outcomes, broad authority/effect auditing, input remapping, external-database
adapters, repeated restored transfers and unattended automation remain incomplete.
Existing historical implementation/handoff documents describe earlier checkpoints;
use this page and the feature contracts below for these additions.

Technical contracts:

- [Host ownership](HOST_OWNER_PIN.md) and [first enrollment](OWNER_ENROLLMENT.md)
- [Credential recovery](OWNER_CREDENTIAL_RECOVERY.md)
- [Filesystem identity](FILESYSTEM_IDENTITY_PATHS.md)
- [Frozen comparison](FROZEN_COMPARISON.md)
- [Backup verification](FULL_BACKUP_VERIFICATION.md)
- [Inactive staging](RESTORE_STAGING.md)
- [Authority transfer](RESTORE_AUTHORITY_TRANSFER.md)
- [Reviewed restored reads](RESTORED_READ_REVIEW.md)

This public snapshot includes application source, tests, generated assets and
implementation documentation. Local planning records and their unpublished commit
history are intentionally kept separate. Existing local work remains preserved.
