# Host input and provider credential boundaries

Individual-authenticated workspaces require an administrator-approved, specific business folder or file before live source registration, hashing, extraction or original capture. Uploads and the trusted host-configured sample directory are default roots. A source registration does not grant host filesystem authority. Source/domain permissions are checked separately.

Use the Sources page's **Host input path approvals** section, or native commands:

```sh
autoontology ops host-roots
autoontology ops host-root-allow /absolute/business/folder --reason 'Approved intake' --confirm
autoontology ops host-root-revoke /absolute/business/folder --reason 'Stop intake' --confirm
```

Revocation is checked again at live reads, including during a stream. Existing archived originals remain subject to their own source/domain permissions; revocation does not delete history. Overlapping approved roots may still authorize a path. Removing a replaced directory's approval uses the recorded path, including when that live path has become a symlink.

Secret and repository-internal paths are excluded even before individual identity enrollment. Live reads reject symbolic links, hard links and non-regular files. Roots are an input boundary, not a general filesystem browser or an operating-system sandbox. Legacy unconfigured workspaces retain compatible root behavior; enroll an individual owner to enforce explicit business roots. This change does not add a cross-resource crash-recovery journal for archives or environment files.

The shared settings service refuses to move an existing API key to another URL origin unless the caller explicitly replaces or clears that key. The CLI uses `provider use --api-key-file` with an owner-only regular file, or `--clear-api-key`. Keys never appear as command arguments or in success output. A shell-owned key cannot be cleared only in the environment file; adjust its actual host environment instead. Same-origin path changes retain the key. CLI environment-change previews redact endpoint paths and secret values.
