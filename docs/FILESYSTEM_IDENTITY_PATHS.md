# Workspace identity and filesystem spellings

The host registry identifies a database by its native canonical path, not by the
spelling supplied to a CLI, a Python `Path.resolve()` result, or a case-folded
string. On macOS the descriptor's `F_GETPATH` spelling resolves case and Unicode
normalization aliases. File identity is checked against the opened descriptor. Directory children may
change without changing the directory identity; regular files retain link-count
checks as well as device/inode checks.
On a case-sensitive filesystem distinct existing paths remain distinct.

Database files must be regular files with one link. The final component cannot
be a symlink. Parent aliases are resolved as in the previous registry contract;
this does not grant a new workspace home. Missing tails are appended to the
nearest existing native ancestor without creating directories or files. An
ambiguous missing path near a surviving same-fold pin fails closed rather than
opening a fresh enrollment path.

## Existing host pins

A pin filename remains the SHA-256 of its **literal stored database field**.
Older pins with nonnative case or Unicode spellings remain readable when they
resolve to the same native path. Writes retain that literal header, filename and
lock key. No automatic migration, adoption, duplicate marker or grant is created.
Two entries identifying the same physical path require local-owner attention;
the application does not choose the newest or most privileged entry.

Case/Unicode folding selects candidate entries only. It never proves identity.
The bounded header cache stores only immutable path headers, whose literal hash
must match the filename. Each lookup enumerates names afresh and rereads the
selected payload with the existing owner/mode/link/size checks. Pending and
active identity state is not cached. Corrupt managed headers fail closed.

An authenticated execution uses the same native path comparison. The host-input
policy uses native paths for protected database/security files and approved
roots. Revocation clears every recorded spelling that resolves to that root;
an exact recorded path can still be revoked after replacement by a symlink.
Secret paths are excluded even beneath an approved business folder.

## Operation and recovery

No new command is needed. UI requests and native CLI commands share the registry
gate. Use the existing `auth owner-status --confirm-local-owner` diagnosis and
preserve both database and host pin on an identity configuration error. A status
read does not create a database or registry. Do not rename or delete pins to
make a restored database appear unconfigured.

This closes the observed same-file alias gap. It does not implement restored-home
rebind, reconstruct lost metadata, supersede an interrupted credential recovery,
or authorize historical sessions from a backup. Those remain explicit recovery
work in the [the public implementation checkpoint](PUBLIC_CHECKPOINT.md).

## Evidence scope

Stage 30 records the failing before-tests, passing corrected tests, actual native
CLI acceptance on a private copy of existing Korean data, and complete checkpoint
restoration separately. The warm local pin-read experiment compares 200 reads
in each implementation: median 0.094 to 0.158 ms and p95 0.100 to 0.185 ms with
one controlled registry entry. This is filesystem overhead evidence, not a UI
loading benchmark, a release benchmark or an organizational time-saving claim.
No UI layout/menu or public deployment changes are included.
