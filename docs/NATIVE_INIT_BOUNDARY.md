# Native initialization settings authority

The final native-command audit found that `init` wrote its environment file
directly, while `provider use` and the UI already used the shared settings
boundary. Controlled reproduction at `48a6bea` demonstrated three failures:
an existing synthetic API key survived a change to another HTTP origin; a policy
change during detection did not prevent saving; and a credential revoked during
detection did not prevent saving. All three commands returned success. PostgreSQL
and setup were controlled stubs, with no model or network calls.

`init` now uses the same `save_env` boundary as the other settings adapters. It
captures the policy before preparation, rejects a stale/revoked actor when saving,
and checks again before setup. Changing `--env` also rechecks the selected
workspace before PostgreSQL preparation: an authenticated execution cannot change
to another configured or unconfigured workspace.

Five regression cases cover the three reproduced failures and both destination
workspace states. Existing onboarding tests continue to cover first installation,
repeated initialization, private files/backups, shell overrides and provider
selection. Secrets are synthetic in tests and are not included in effect receipts.

To deliberately change a provider origin with a stored key, use the existing
`provider use --base-url ... --api-key-file /private/path` operation (or explicitly
clear the key with `--clear-api-key`) before running `init` again. The key file
must satisfy the existing owner-only file checks. `init` never asks for a raw key.

This fixes a local maintenance-command boundary; it does not complete the entire
authority audit. PostgreSQL setup, filesystem changes, upstream engine effects
and settings still lack a shared crash-atomic effect journal. Setup actions are
not claimed to be transactional with the saved environment file. Initial owner
enrollment/recovery, managed workers and policy-authorized autonomy remain open.
The frozen 20:00 review application stays on its recorded source reference; this
development change is not silently installed into that copy or the public site.
