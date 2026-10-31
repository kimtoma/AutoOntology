export const enHost = {
  "host.title": "Approved host input roots",
  "host.hint": "Individual workspaces read live inputs only from approved business folders/files, uploads and configured samples. Authentication files and linked inputs remain excluded. Registering a source does not grant host access.",
  "host.enforced": "Root restrictions apply to this individual workspace.",
  "host.legacy": "Individual identity is not active; secret exclusions apply, and root restrictions activate with individual enrollment.",
  "host.default": "Built-in intake root",
  "host.allowed": "Allowed",
  "host.revoked": "Revoked",
  "host.allow": "Approve business input root",
  "host.revoke": "Revoke this root",
  "host.retained": "Revocation blocks future live reads. Preserved originals and published knowledge remain subject to their own access policy. Another overlapping approved root may still allow the path.",
  "host.allowHint": "Approve one specific existing business folder or file. Editors may register inputs beneath this root; they still need source/domain authority.",
  "host.revokeHint": "Future reads, hashes and archival capture recheck this approval. Existing history is preserved.",
  "host.path": "Absolute business folder/file path",
  "host.reason": "Business reason",
  "host.ack": "I reviewed this exact path and its authority over future inputs.",
  "host.apply": "Apply this host policy"
} as const
