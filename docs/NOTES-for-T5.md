# Notes for T5 (docs and cleanup)

Items found in the T2 review that need no code change now, but belong in the README or a
later cleanup:

- **Snapshots are never pruned.** Every successful `build` adds a row to
  `ontology_snapshots` with the full model JSON. Add a retention policy (or a
  `autoontology ontology prune` command) once workspaces grow.
- **Finished runs list only the steps that ran.** A run's step rows are the steps it
  executed (or kept on `--resume`); steps that were never reached are absent rather than
  `pending`. The UI/README should say so.
- **Migration v2 drops the playground conversations.** Upgrading a pre-T2 home deletes
  the stored playground chats without a copy. The README upgrade note must mention it.
- **identity-v2 rule** (documented in `autoontology/unstructured/align.py`): English
  singular/plural aliases match (`Contracts` = `Contract`), except a stop-list of known
  false-positive pairs (`goods`, `customs`, `news`, `arms`, `glasses`, `means`). README
  and plan should describe it.
- **`export --out DIR` writes `DIR/r<rev>/`** and refuses to replace a folder that holds
  files the exporter did not write; every export has an `export.json` marker.
- **`build --resume` sees changed input files** (content hashes) but not a schema change
  inside an external database source; a plain `build` picks that up.
- **Upstream files in an export are fetched at export time**, not build time
  (`report.md` and `export.json` `upstream_fetched_at` say so).
