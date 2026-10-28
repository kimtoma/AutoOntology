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
- **identity-v2.1 rule** (documented in `autoontology/unstructured/align.py`): English
  singular/plural aliases match (`Contracts` = `Contract`), except a stop-list of known
  false-positive pairs (`goods`, `customs`, `news`, `arms`, `glasses`, `means`). README
  and plan should describe it.
- **`export --out DIR` writes `DIR/r<rev>/`** and refuses to replace a folder that holds
  files the exporter did not write; every export has an `export.json` marker.
- **`build --resume` sees changed input files** (content hashes) but not a schema change
  inside an external database source; a plain `build` picks that up.
- **Upstream files in an export are fetched at export time**, not build time
  (`report.md` and `export.json` `upstream_fetched_at` say so).

Items from T3 (Ossie and OKF exporters):

- **`export --format all|rdf|ossie|okf`** (default `all`; `POST /api/runs` takes `format`).
  A `--format` export rewrites only its own entries and carries the other formats' files
  over from the previous export of the same revision (same workspace and revision in
  `export.json`); `export.json` `formats` records which export run wrote each of `rdf`,
  `ossie` and `okf`. `okf` needs no backend; `ossie` and `rdf` fetch the upstream files
  (`rdf` = ttl, jsonld, graphml and `upstream/`).
- **Ossie alignment semantics follow the model, not the T3 brief's wording.** `broader`
  means the Term is broader (concept `extends` Term) and `narrower` means the Term is
  narrower (Term `extends` concept), as in `ontology.ttl` and `unstructured/schemas.py`;
  `related` becomes `related_to`. Unaccepted proposals are exported like the TTL does.
- **Concept names keep Hangul** (`ontology.json` has no name pattern; names are Python-style
  identifiers). IRIs are the TTL IRIs (`ao:term-<id>`, `ao:concept-<id>`) so the Ossie,
  OKF and RDF exports name each node the same way.
- **OKF `generated.by` is `autoontology/<version>`** (the OKF actor convention), and
  `generated.at` is the snapshot time, so exports of one snapshot are byte-identical.
- **OKF file names keep Hangul** (`okf/concepts/계약-상태.md`); `/api/exports` accepts
  Unicode letters in path segments and up to three segments.
- **A document attribute/metric attached by `has_attribute`/`measured_by`** becomes a value
  relationship of its subject in `ontology.yaml` and has no concept of its own unless
  something else refers to it (listed in `validation.json` warnings).
- **Link mappings need single-field keys at both ends.** On the sample data only
  `customers` declares a key upstream, so `Contracts`/`Invoices` get no object mapping (a
  warning each).
- **OKF body text is escaped, not sanitized.** Definitions, evidence quotes and labels get
  `<` as `&lt;` and a backslash before a line-leading `#`, `>`, list marker or `1.`, so
  they cannot open raw HTML or a block. A UI that renders the `.md` files as HTML must
  still sanitize its output (the API serves them as `text/plain`).
