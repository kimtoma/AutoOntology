# Apache Ossie schemas (vendored)

- Source: https://github.com/apache/ossie
- Commit: `89e2e17f8249e68f30df7b29ac8852fa9dd24501` (2026-10-27T16:52:55Z)
- License: Apache License 2.0 (`LICENSE`, `NOTICE` in this folder)
- Status: **unmodified** copies of

| File | Upstream path |
|---|---|
| `ossie-schema.json` | `core-spec/ossie-schema.json` (semantic model, spec 0.2.0.dev0) |
| `ontology.json` | `ontology/ontology.json` (ontology, spec 0.2.0.dev0) |
| `mapping.json` | `ontology/mapping.json` (standalone mapping document) |

The exporter (`autoontology/export/ossie.py`) validates `exports/r<rev>/ossie/*.yaml`
against byte-identical copies shipped as package data in
`autoontology/export/schemas/ossie/` (a test asserts the two copies match). The schemas'
remote `$ref`s (`https://raw.githubusercontent.com/apache/ossie/main/...`) are resolved to
these local files; nothing is fetched over the network.
