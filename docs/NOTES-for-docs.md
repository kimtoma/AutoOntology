# Notes for the docs pass (backend final-fix wave A)

Backend behaviour changed in ways the README / README.ko.md / docs/ARCHITECTURE.md still
describe the old way. Apply these in the docs worktree, then delete this file.

1. **Quickstart without kordoc (README.md:109-112, ko equivalent; troubleshooting row
   README.md:390).** `build` no longer reports `ENGINE_MISSING` documents as failed. It
   skips them, prints `warning: documents:<src>:read: N document(s) need kordoc and were
   skipped (<files>): run `uv run autoontology tools install-kordoc`, then build again`,
   and exits 0 (`succeeded`). After installing kordoc, a plain `build` or `build --resume`
   reads them, because kordoc's availability is part of the documents source fingerprint.
   If every document of a source needs kordoc, the read step still fails.
2. **POSTGRES_DATABASE** (configuration table): it may not start with
   `autoontology_src_`. That prefix is reserved for per-source databases, and such a value
   is refused when settings load.
3. **Export folders** (export section, ARCHITECTURE extension table): `export.json` now
   has a `files` list of every file the exporter wrote. A re-export refuses, everywhere
   including under `exports/`, when the revision folder holds any file not on that list
   (also inside `okf/`, `ossie/`, `upstream/`), or when `export.json` has no list (an
   export from before this change: delete or move the folder once). The previous folder is
   renamed to `r<NNNN>.prev-<ts>-<run>` during the swap and is deleted after the new one is
   in place. The run is `succeeded` only after that.
4. **`source rm` of a documents source** (CLI reference, API `DELETE /api/sources/{name}`):
   it first removes, from the upstream Terms, the synonyms that source's alignments added.
   Manual aliases, other sources' aliases and aliases that another source's accepted
   `same` alignment still covers are kept. This needs the engine database. If it fails,
   the source is kept unless `--force` is given.
5. **SPARQL** (README.md:271, ARCHITECTURE.md:122-125): queries run in up to two
   spawn-context worker processes, not in-process threads. Past the 10 s deadline the
   worker is killed (408), so a query that never reads the graph also stops. A worker
   above 1024 MB resident memory exits, and the query answers 413 `sparql_memory_limit`.
6. **`build --resume`** (build section): a DB-URL source's fingerprint now includes its
   live schema, from `information_schema` for Postgres. If the database cannot be read,
   or it is MySQL or Snowflake (no bundled driver), its steps always run again.
   align/merge/snapshot also run again when the set of registered sources changed. A
   resumed align re-judges a concept when its candidate Terms changed.
7. **Store schema v3** (ARCHITECTURE store tables): `term_alias_additions.source_id`.
8. **GraphML**: edge ids are `e0`, `e1`, ... and unique across the file.
