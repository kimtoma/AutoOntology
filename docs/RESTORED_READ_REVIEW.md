# Reviewed read access after restoration

Stage 35 adds native `auth restore-read-review` and `auth restore-read-confirm`.
Use them after [authority transfer](RESTORE_AUTHORITY_TRANSFER.md) and startup of
the new trusted local runtime. They do not rewrite historical publications,
repair input locators, enable collection/builds or renew business approval.
Normal preview identity remains intentionally unconfigured; development and
acceptance stay local.

```bash
python /private/new-runtime/control.py start
python -m autoontology auth restore-read-review /private/new-runtime --confirm-local-owner
# Inspect the returned proposal, especially publication/legacy status and physical schema.
python -m autoontology auth restore-read-confirm /private/new-runtime \
  --review-sha256 <exact-proposal-sha256> --confirm-local-owner \
  --acknowledge-new-data-target
# After interruption repeat the exact confirmation with --resume.
```

Neither operation accepts an authenticated application context as host proof.
No HTTP path receives the destination or a host file. Confirmation requires the
exact SHA of the privately retained proposal and an explicit new-target
acknowledgment. This is an OS-owner physical recovery decision, not a business
approver or semantic definition decision.

## What is inspected

The proposal binds the workspace, transfer operation/event, destination database,
seven pinned runtime-file hashes, current policy, operational snapshot, source
rows and publication state. Only the explicitly selected operational revision is
used; no candidate/latest fallback is permitted.

For a committed publication the complete existing verifier checks model, index,
originals, processing/input receipts and export artifacts. For an older operational
revision with no publication manifest, the inspector checks its exact snapshot/run,
export marker and every artifact hash, versioned index vectors, originals and
recorded source/run binding. It records **legacy operational selection;
historical approver unverified**. The derived verification envelope is only part
of the local review proposal: it is not inserted as an approved publication and
cannot reconstruct missing historical approval or connection information.

For each included structured source this adapter currently supports only an
existing files source backed by its local `autoontology_src_*` PostgreSQL database.
It checks the stable source/catalog IDs, approved-or-historical native SQL mapping,
all frozen physical column names/types, PG system identifier and actual data
directory. The target must be the new loopback cluster inside this runtime.
Unrelated databases, external database URIs, unknown engines and missing mappings
remain unavailable rather than guessing a target. The inspection reads metadata,
not business rows. Query rows are live at execution, not a historical row archive.

Input locators and archived upstream connection records retain their history.
They are not used to authorize new ingestion or upstream writeback. Managed read
SQL uses the reviewed current settings and is revalidated before planning and
again before execution. An overlay binds the exact historical mapping to this
new physical target without editing the original manifest/export/catalog.
Legacy config equality alone cannot skip the restored-target gate.

## Durable decision and current authority

The host journal first reserves a pending review while `catalog_ready` stays
false. The SQLite transaction rechecks the frozen proposal and current policy,
appends an OS-owner `identity.restored_reads_enabled` event and advances policy.
Only after commit does the external journal activate the same review/event.
Five interruption boundaries support exact-operation resume. A committed resume
reuses the event instead of creating a second decision. Unknown or stale state
is preserved and held. A new explicit current proposal can supersede a held stale
review; resuming the obsolete SHA is denied.

An active review is not a permanent grant. Source/model/publication/policy drift,
a missing or changed proposal, or pinned runtime changes close the query gate.
The existing principal/domain/credential checks still apply. SQL additionally
rechecks the exact current endpoint and physical schema; changing a port cannot
send a probe to the old source before matching the reviewed connection identity.
Collection, preparation, source edits, publication, settings changes and other
effects remain held under their separate recovery/authority requirements.
Later password recovery changes policy and therefore requires a fresh read review.

The first supported version explicitly rejected `catalog_ready: true`; older
readers fail closed on the extended journal. Pins remain v4 and preserve the
original authority-transfer root and subsequent credential-recovery child.
Read status can be inspected without creating or migrating unreadable SQLite.

## UI, CLI and receipts

Refresh an existing browser after local confirmation. The light UI shows that
read connections were reviewed and that collection/builds/changes still need
separate authorization. Capability discovery, not this banner, controls access.
The access audit labels physical read review separately from transfer and business
review. Questions, SQL, stored results and original-file links keep their existing
UI/native interfaces and resource scope.

New query receipts include the exact read-proposal SHA, review and transfer event
SHAs, and policy version. Their original publication ID/legacy marker remains
unchanged. Reopening a saved result does not rerun a model or source query. The
same shared catalog/authority path serves UI, HTTP and native commands.

## Acceptance boundaries

Unit tests cover exact SHA confirmation, five interruptions, source/model/policy/
proposal/runtime drift, renewed holds, authenticated-context denial, no unapproved
candidate fallback and legacy-binding enforcement. Separate real-data acceptance
uses a full Korean-data clone, abrupt native process death after review commit,
new-process resume, the actual new PG cluster, wrong-old-endpoint rejection before
network access, actual temporary column rename/restore, completed model/SQL paths
and downloads. Exact test counts, screenshots, observed timing, commits and the
actual checkpoint recovery drill are in the private stage record.

The normal data, frozen review and public service are not upgraded by these test
ceremonies. This does not close customer-readiness gates, prove a business benefit, enable unattended
effects or finish broader development. The next product slice is the fictional Hanbit minimal
Definition/KnowledgeContract and its authorized consuming service, followed by
change/consumer impact and the remaining roadmap. Input reapproval/remapping,
external database adapters, repeated v4-home transfers and pending credential
recovery supersession remain explicit recovery work.
