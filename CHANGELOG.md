# Changelog

All notable changes to AutoOntology. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
behaviour).

## 0.2.0 - 2026-10-29

Onboarding for domain experts, many more input formats, subscription model providers and a
Korean interface.

### Added

- **Guide**: `docs/GUIDE.md` and `docs/GUIDE.ko.md`, a manual for domain experts (words
  explained, checklist, provider choice, the 10-minute path, preparing your own files,
  reading the results, troubleshooting, FAQ).
- **Onboarding commands**: `autoontology init` (detects Postgres, the chat providers,
  embeddings and Node, writes `.env` with a backup, runs `setup`; `--yes` for scripts),
  `autoontology demo [--set quickstart|hanbit] [--resume] [--replace] [--no-open]`
  (services, sample sources, build with per-step times, export, web UI) and
  `autoontology provider status|test|use`.
- **Claude and ChatGPT subscriptions as chat providers** (`AUTOONTOLOGY_CHAT_PROVIDER=claude`
  or `codex`): the logged-in `claude` / `codex` CLI runs as a locked-down subprocess per
  call, routed through the service's `/v1` router; login tokens are never read or logged.
  `GET /api/status` reports the provider state, `POST /api/providers/test` makes one test
  call.
- **Formats**: CSV/TSV and legacy Word `.doc` documents (native), Excel 97-2003 `.xls`
  tables (python-calamine), OCR of images (PNG/JPEG/WebP) and scanned PDFs through kordoc's
  PP-OCRv5 models (`tools install-kordoc` downloads them), and optional image descriptions
  by an Ollama vision model (`AUTOONTOLOGY_VISION_MODEL`). `GET /api/status` lists every
  supported extension as `formats`.
- **Sample sets**: `samples/quickstart` (the previous samples) and `samples/hanbit`, a
  fictitious company with 9 table files in 7 formats (11 tables, 5,603 rows), 15 documents
  in 12 formats and 6 images, generated reproducibly by `scripts/gen_samples.py`.
  `GET /api/samples` and `POST /api/samples/load` serve them to the web UI.
- **Web UI**: a **Get started** page with a live five-step checklist, a provider card with a
  Test button, sample loading, a file-by-file format preview in the Sources dialog, plain
  step names and timings on the Build page, and OCR/vision rows on the Status page.
- **Korean**: the web UI switches between English and Korean (globe menu, `?lang=ko`), and
  the onboarding commands print Korean with `AUTOONTOLOGY_LANG=ko` or a Korean locale.
- `doctor` rows for the chat provider login, the OCR models, the vision model and orphaned
  upstream connections; every FAIL/WARN row has a remedy. `tools prune-connections`
  removes upstream connections whose database is gone.
- `CHANGELOG.md`.

### Changed

- The README starts with a five-line quickstart (`init` + `demo`), a provider comparison
  and the sample sets with measured build times; the reference sections follow.
- The 0.1.0 samples (the tables and the documents folder) moved to
  `samples/quickstart/structured` and `samples/quickstart/documents`; the demo source names
  are `quick` and `quick-docs`.
- With a non-Ollama chat provider, `setup` pulls only the embedding model.
- `source rm --force --drop-db` keeps the database when the upstream connection could not
  be deleted, and says how to clean it up later.
- New runtime dependencies: `olefile` (BSD-2-Clause) and `python-calamine` (MIT).

## 0.1.0 - 2026-10-28

First public release.

- One build over structured sources (CSV, TSV, XLSX, JSON, Parquet, SQLite, Postgres,
  MySQL, Snowflake) through NVIDIA Auto Ontology, and documents (Markdown, text, HTML, and
  with kordoc HWP/HWPX, PDF, DOCX, PPTX, XLSX) through LLM extraction, consolidation and
  alignment under the `identity-v2.1` policy, saved as versioned snapshots.
- Exports to Turtle, JSON-LD, GraphML, Apache Ossie and OKF, with validation.
- Web UI (Sources, Build, Ontology, Graph, Playground, Exports, Status), playground chat
  with citations, NL-to-SQL and read-only SPARQL.
- Ollama or any OpenAI-compatible endpoint as model providers.
