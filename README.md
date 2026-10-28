# AutoOntology

**English** | [한국어](README.ko.md)

AutoOntology turns structured data (CSV, TSV, XLSX, JSON, Parquet, SQLite, or a
Postgres/MySQL/Snowflake database) and unstructured documents (Markdown, text, HTML, and
with the optional kordoc engine HWP/HWPX/PDF/DOCX/PPTX) into **one ontology**. It is built
on [NVIDIA Auto Ontology](https://github.com/NVIDIA/auto-ontology), which compiles tables
into business Terms. AutoOntology adds document concept extraction, aligns the document
concepts to those Terms, and merges both into a single versioned snapshot. It exports the
snapshot as RDF/OWL (Turtle, JSON-LD), GraphML, an
[Apache Ossie](https://github.com/apache/ossie) bundle and an
[Open Knowledge Format (OKF)](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle. Everything runs on your machine with Postgres + pgvector and Ollama, or with any
OpenAI-compatible endpoint.

![Ontology page](docs/screenshots/ontology.png)

## Features

- **One build, one ontology.** `autoontology build` runs every registered source:
  structured sources (load, register, ingest, compile), document sources (read, chunk,
  extract, consolidate), then alignment, merge and a snapshot with a revision number.
- **Structured path** through the NVIDIA Auto Ontology engine. Tables become Terms with
  column attributes, and semantic foreign keys are detected even when the files declare no
  constraints.
- **Documents path** using a local LLM. Concepts, relations, business rules and processes
  are extracted with verbatim evidence quotes, then merged across chunks and documents.
- **Alignment.** Document concepts are matched to Terms by embedding retrieval plus an LLM
  judge, under a conservative, versioned identity policy (`identity-v2.1`). Accepted
  `same` concepts fold into their Term.
- **Exports** of every snapshot to `exports/r<rev>/`: `ontology.ttl`, `ontology.jsonld`,
  `ontology.graphml`, `ossie/` (validated against the Ossie JSON Schemas and round-tripped
  through the engine's converter), `okf/` (validated for OKF v0.2 conformance) and
  `report.md`.
- **Web UI** at `/ui/`: Sources, Build (live log), Ontology (searchable concept list with
  origin badges), Graph (3D with a 2D fallback), Playground, Exports (preview and
  download) and Status.
- **Playground**: chat with the ontology (retrieval with citations), natural language to
  SQL against the structured sources, and a read-only SPARQL console over the snapshot.
- **Providers**: Ollama by default (`qwen3:8b` + `bge-m3`), or any OpenAI-compatible API
  (OpenAI, NVIDIA build.nvidia.com, vLLM, LM Studio).
- **Local by design**: the service binds to 127.0.0.1 and rejects requests from other
  hosts or origins.

## Architecture

```
                 browser (/ui/)                  autoontology CLI
                       |                                |
                       v                                v
  +--------------------------------------------------------------------------+
  | AutoOntology service  127.0.0.1:7800   (FastAPI, autoontology.web)       |
  |   /api  REST + SSE   /api/playground   /ui  React app   /v1 model router |
  |   in-process: build pipeline, SQLite store (data/autoontology.db),       |
  |               exporters (RDF, GraphML, Ossie, OKF), SPARQL (rdflib)      |
  +--------------------------------------------------------------------------+
        | HTTP /api/...         | imports auto_ontology        | /v1/chat/completions
        v                       v  (ingest, compile, vectors)  v
  +-----------------------+   +-----------------------+   +-------------------------+
  | NVIDIA Auto Ontology  |   | Postgres 17+pgvector  |   | Ollama :11434, or an    |
  | backend 127.0.0.1:3101|-->| autoontology (catalog)|   | OpenAI-compatible API   |
  | (vendor/auto-ontology)|   | autoontology_src_<n>  |   | (chat + embeddings)     |
  +-----------------------+   +-----------------------+   +-------------------------+
```

The engine is an unmodified git submodule. AutoOntology calls it over HTTP for
connections, exports and NL-to-SQL, and imports it in-process for ingest, semantic
compilation and the vector tables. Both the backend and the service send their chat calls
through the service's `/v1` router, which forwards them to Ollama or to the configured
OpenAI-compatible endpoint. Details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart

Prerequisites:

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Docker, **or** your own Postgres 17 with the [pgvector](https://github.com/pgvector/pgvector)
  extension available.
- [Ollama](https://ollama.com) running locally. `setup` pulls `qwen3:8b` (about 5 GB) and
  `bge-m3`. To use a hosted API instead, see the next section.
- Optional: Node.js 20 or newer, for HWP/PDF/DOCX/PPTX documents through kordoc.

```
git clone --recurse-submodules https://github.com/kimtoma/AutoOntology.git
cd AutoOntology
cp .env.example .env
docker compose up -d            # or point POSTGRES_* in .env at your own Postgres
uv sync
uv run autoontology setup       # add --docs for HWP/PDF/DOCX via kordoc
uv run autoontology up
uv run autoontology source add samples/structured --name hanbit
uv run autoontology source add samples/unstructured --name hanbit-docs --kind documents
uv run autoontology build
uv run autoontology export
uv run autoontology ui
```

What to expect:

- `up` starts the engine backend on :3101 and the service on :7800, both in the
  background. `down` stops them, and `status` shows them.
- `build` takes the longest. With a local `qwen3:8b`, each document chunk takes one to two
  minutes. The sample documents produce 9 chunks without kordoc and 28 with it, so on an
  Apple-silicon machine the first build takes roughly 20 minutes without kordoc and 50
  minutes with it. Later builds reuse everything that did not change.
- The sample folder has three files that need kordoc (`.pdf`, `.hwp`, `.docx`). **If you
  skipped `--docs`, `build` reports them as `ENGINE_MISSING`, still saves the snapshot from
  everything else, and exits with status 1 ("partial").** To add them later, run
  `uv run autoontology tools install-kordoc` and then `uv run autoontology build --resume`.
- `export` prints the folder it wrote, for example `exports/r0001/`.
- `ui` opens http://127.0.0.1:7800/ui/.

The sample data is a fictitious Korean trading company ("한빛상사"): three related CSV files
(customers, contracts, invoices) and its contract and billing rules as documents. See
[samples/README.md](samples/README.md).

### Using an OpenAI-compatible API instead of Ollama

Set these in `.env`. Type the key into the file yourself; never paste it into a chat or
commit it. The example uses NVIDIA's hosted endpoints; the model id is a placeholder.

```
AUTOONTOLOGY_CHAT_PROVIDER=openai
AUTOONTOLOGY_OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
AUTOONTOLOGY_OPENAI_API_KEY=your-api-key-here
AUTOONTOLOGY_OPENAI_CHAT_MODEL=<chat model id from build.nvidia.com>
# keep local embeddings (bge-m3 via Ollama) ...
AUTOONTOLOGY_EMBED_PROVIDER=ollama
# ... or embed through the same endpoint:
# AUTOONTOLOGY_EMBED_PROVIDER=openai
# AUTOONTOLOGY_OPENAI_EMBED_MODEL=<embedding model id>
```

Then run `uv run autoontology doctor` to check the configuration, and restart with
`down` / `up`. With both providers set to `openai`, `setup` skips Ollama entirely. Write
model ids as the endpoint knows them; AutoOntology adds the `openai/` prefix the engine
needs and strips it again in the router. The embedding dimension is fixed once the first
source has been ingested (see Troubleshooting).

## CLI reference

| Command | What it does |
|---|---|
| `autoontology setup [--docs] [--skip-models] [--force-alias]` | Idempotent preparation: creates the catalog database and the `vector` and `pg_trgm` extensions, runs the migrations, pulls the Ollama models and builds the `openai/qwen3:8b` alias, and with `--docs` installs kordoc. |
| `autoontology doctor [--skip-models]` | Checks Postgres, pgvector, migrations, model endpoints, the vector tables and the ports. |
| `autoontology up` / `down` / `status` | Starts, stops or shows the engine backend and the service. |
| `autoontology ui [--no-browser]` | Prints the web UI URL and opens it. |
| `autoontology source add <path\|db-url> --name N [--kind structured\|documents] [--recursive]` | Registers a source. Structured sources are loaded into `autoontology_src_<name>`. |
| `autoontology source list` | Lists the sources. |
| `autoontology source rm N [--drop-db] [--force]` | Removes a source; `--drop-db` also drops its database. |
| `autoontology build [--source N ...] [--resume]` | Builds every source into one snapshot. `--source` rebuilds only the named sources (align, merge and snapshot still cover all of them). `--resume` continues the latest build and keeps steps whose inputs did not change. |
| `autoontology ontology` | Shows the latest snapshot: revision, sources and counts. |
| `autoontology runs [--limit N]` | Shows recent runs and their steps. |
| `autoontology export [--revision R] [--format all\|rdf\|ossie\|okf] [--out DIR]` | Writes a snapshot to `exports/r<rev>/` (or `DIR/r<rev>/`). |
| `autoontology tools install-kordoc [--version V] [--omit-optional] [--force]` | Installs kordoc into `<home>/kordoc` with npm. |
| `autoontology tools build-ui` | Rebuilds the web UI into `autoontology/web/static/dist` (needs pnpm). |
| `autoontology tools status` | Shows the kordoc engine and the Node binary in use. |
| `autoontology version` | Prints the version. |

`build` exits 1 when a run is partial (for example a document failed to read or a chunk
failed to extract), and prints a `--resume` hint. A partial run still saves its snapshot.

## HTTP API

The service listens on `http://127.0.0.1:7800`. All JSON routes live under `/api`. Every
error has the shape `{"error": <code>, "detail": <message>}`.

| Method and path | Purpose |
|---|---|
| `GET /api/status` | Version, backend and Postgres health, Ollama models, `kordoc_available`, and a provider summary (never the key). |
| `GET /api/sources` | Lists the sources. |
| `POST /api/sources` | Adds a source: JSON `{name, kind: files\|database\|documents, locator, recursive?}`, or a multipart upload. |
| `DELETE /api/sources/{name}` | Removes a source (`?drop_db=true` also drops its database). |
| `POST /api/runs` | Starts a run: `{kind: "build", sources?, resume?}` or `{kind: "export", revision?, format?}`. |
| `GET /api/runs`, `GET /api/runs/{id}` | Lists runs; shows one run with its steps. |
| `GET /api/runs/{id}/events` | Server-sent events: the live log and step updates of a run. |
| `POST /api/runs/{id}/cancel` | Cancels a run. |
| `GET /api/ontology[?revision=]` | Snapshot header: revision, workspace, base IRI, sources and counts (404 `no_snapshot` before the first build). |
| `GET /api/ontology/graph?layer=all\|structured\|documents\|aligned&evidence=&revision=` | The unified graph. Every node has an `origin`. |
| `GET /api/ontology/concepts?q=&kind=&origin=&source=&page=&size=&revision=` | Terms and document concepts in one paged list. |
| `GET /api/ontology/concepts/{term-…\|concept-…}[?revision=]` | One concept: definition, synonyms, columns, alignments, relations and evidence. |
| `GET /api/exports` | The export folders and their files. |
| `GET /api/exports/{rev}/{path}` | One exported file (`?inline=true` for a preview). |
| `POST /api/playground/chat` | Chat with the ontology (SSE, with citations). |
| `GET /api/playground/conversations[/{id}]`, `DELETE …/{id}` | Stored chats. |
| `GET` / `POST /api/playground/index` | State of the retrieval index; rebuilds it. |
| `POST /api/playground/sql` | Natural language to SQL through the engine (SSE): `{question, source?}`. |
| `GET /api/playground/sparql/examples` | Example queries. |
| `POST /api/playground/sparql` | `{query, limit?}` runs a read-only SPARQL query against the snapshot. |
| `POST /v1/chat/completions`, `GET /v1/models` | The model router used by the engine; not meant for clients. |
| `GET /health/live`, `GET /health` | Liveness and readiness. |

## Export formats

`autoontology export` writes everything below into `exports/r<rev:04d>/`, together with
`export.json`, a marker that records the workspace, the revision and which export run
wrote each format.

| File | Format | What it contains |
|---|---|---|
| `ontology.ttl` | [RDF 1.1 Turtle](https://www.w3.org/TR/turtle/) | OWL classes for Terms and document concepts, object and datatype properties, individuals (rules, processes), `rdfs:subClassOf` and `skos:related` axioms, `skos:altLabel` synonyms, and evidence nodes with quotes, all under the `ao:` namespace (`AUTOONTOLOGY_BASE_IRI`). It is checked by a parse round-trip. |
| `ontology.jsonld` | [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) | The same graph as JSON-LD. |
| `ontology.graphml` | [GraphML](http://graphml.graphdrawing.org/specification.html) | Nodes and edges with string attributes, for Gephi, yEd or networkx. |
| `ossie/semantic_model.yaml`, `ossie/ontology.yaml`, `ossie/mapping.yaml` | [Apache Ossie](https://github.com/apache/ossie) spec 0.2.0.dev0 | The structured sources' datasets merged into one semantic model; the unified ontology (Terms and document concepts as `EntityType`s, column and document relationships with verbalizations, `extends` for subclass axioms); object mappings for Terms whose dataset declares a key. `validation.json` holds the JSON Schema results, structural checks and the round-trip through the engine's Ossie converter. |
| `okf/` | [OKF v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) | A Markdown bundle with YAML frontmatter: `index.md`, `log.md`, `concepts/*.md` (type `Business Concept`), `tables/<source>.<table>.md` (type `Table`) and `documents/*.md` (type `Document`), with relationships as bundle links and evidence as footnotes. `validation.json` holds the conformance report. |
| `report.md` | Markdown | A human-readable summary: counts, sources, merged concepts and every file with its sha256. |
| `upstream/<source>.model.yaml`, `upstream/<source>.ossie.zip` | Engine native | The engine's own exports per structured source, fetched at export time. |

Options:

- `--format rdf|ossie|okf` rewrites only that format (`rdf` covers ttl, jsonld, graphml and
  `upstream/`) and keeps the other formats' files from the previous export of the same
  revision.
- `--out DIR` writes `DIR/r<rev>/`. It only ever replaces files the exporter wrote, and
  refuses a non-empty folder that has no `export.json`.

## How the unified ontology is built

1. **Terms (structured).** Each structured source is loaded into its own Postgres
   database. The engine ingests the catalog and compiles a semantic layer: business Terms
   with descriptions, column attributes and semantic foreign keys between Terms.
2. **Document concepts.** Documents are read (natively, or with kordoc), split into
   section-aware chunks, and sent to the chat model. It extracts concepts (entity,
   attribute, metric, process, rule, other), typed relations and verbatim evidence. Consolidation
   merges duplicates across chunks and documents using embedding similarity plus an LLM
   merge judge.
3. **Alignment.** Each alignable document concept (entity, metric, attribute) retrieves its
   top 5 Terms by embedding cosine (floor `AUTOONTOLOGY_ALIGN_MIN_SIM`). An LLM judge
   returns `same`, `broader`, `narrower`, `related` or `none`, with a confidence and a
   rationale, and the verdict is validated fail-closed.
4. **Identity policy `identity-v2.1`.** A `same` verdict is accepted only with confidence
   ≥ max(`AUTOONTOLOGY_ALIGN_MIN_CONF`, 0.90) **and** an exact match between a whole
   alias of the concept (name, English name, synonyms) and a trusted alias of the Term.
   Aliases are compared after NFKC normalization, case folding and whitespace removal. An
   all-Latin alias longer than 3 characters also matches as its English singular
   (`Contracts` = `Contract`, `categories` = `category`; words ending in `-ss`, `-us` or `-is`
   are kept as they are). A stop-list of pairs that are different words keeps them apart:
   goods/good, customs/custom, news/new, arms/arm, glasses/glass and means/mean. It applies
   to whole aliases and to the last word of a multi-word alias (`Trade Goods` is not
   `Trade Good`). Substrings never count. Any other `same` is downgraded to `related` and
   kept as a review proposal.
5. **Merge.** An accepted `same` **folds** the concept into its Term: the concept's names
   become synonyms (`skos:altLabel`) of the Term, and its document definition and evidence
   attach to the Term. A `broader` verdict makes the concept a subclass of the Term
   (concept extends Term), a `narrower` verdict makes the Term a subclass of the concept
   (Term extends concept), and `related` becomes a `related_to` (`skos:related`) link.
   Other entity, metric and attribute concepts become their own classes; rules and
   processes become individuals of `ao:BusinessRule` and `ao:Process`. Every node gets an
   origin: `document`, `structured` (a Term untouched by documents), or `aligned` (a Term
   with a folded concept or an accepted alignment axiom).
6. **Snapshot.** The merged model is stored as JSON in `ontology_snapshots` with a new
   revision. The UI, the playground and every export read snapshots; nothing is rebuilt on
   read.

With the samples, the judge accepts 계약 → Contracts, 고객 → Customers and
청구서 → Invoices, so all three Terms show as `aligned`.

## Playground

- **Chat** answers questions about the latest snapshot. It retrieves the most relevant
  elements (Terms, concepts, rules, relations, evidence) and cites them; each citation
  opens the concept on the Ontology page or in the graph. Its system prompt answers in
  Korean, like the sample data. Conversations are stored per workspace.
- **SQL** turns a question into SQL through the engine's NL-to-SQL agent, runs it against
  a structured source and shows the table.
- **SPARQL** runs read-only queries in-process with rdflib over the snapshot's Turtle. The
  limits are 10 s per query, 500 rows and 2 concurrent queries. `SERVICE`, `FROM` and
  `LOAD` are refused. Example:

  ```bash
  curl -s http://127.0.0.1:7800/api/playground/sparql -H 'content-type: application/json' \
    -d '{"query": "SELECT ?class ?label WHERE { ?class a owl:Class ; rdfs:label ?label } LIMIT 5"}'
  ```

## Configuration

Copy `.env.example` to `.env`. Every value has a default. Precedence is: shell environment,
then `.env`, then the built-in default.

| Variable | Default | Meaning |
|---|---|---|
| `AUTOONTOLOGY_HOME` | `<repo>/data` | Data directory: SQLite store, logs, PID files, kordoc, uploads. |
| `AUTOONTOLOGY_EXPORTS_DIR` | `<repo>/exports` | Export root (one `r<rev>/` folder per revision). |
| `AUTOONTOLOGY_ENV_FILE` | `<repo>/.env` | Which `.env` to load (set it in the shell). |
| `AUTOONTOLOGY_WORKSPACE` | `default` | Name of the one ontology this home builds. |
| `AUTOONTOLOGY_BASE_IRI` | `http://example.com/autoontology#` | Namespace of every minted IRI (prefix `ao:`). |
| `AUTOONTOLOGY_PORT` | `7800` | Service port (UI, `/api`, `/v1`). |
| `BACKEND_PORT` | `3101` | Engine backend port. |
| `INGESTION_SERVICE_URL` | derived | Always derived from `AUTOONTOLOGY_PORT`; a value you set is overridden. |
| `APP_URL` | unset | Read only by the engine's own web frontend, which AutoOntology does not run. |
| `AUTOONTOLOGY_UPLOAD_MAX_MB` | `200` | Web UI upload limit per request. |
| `AUTOONTOLOGY_GRAPH_MAX_NODES` | `1500` | Most nodes the graph view receives (the best-connected nodes are kept). |
| `AUTOONTOLOGY_DEBUG` | `0` | `1` prints full tracebacks for unexpected CLI errors. |
| `AUTOONTOLOGY_LOCK_TIMEOUT_S` | `300` | Maximum wait for the Postgres advisory lock around vector-table setup. |
| `AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S` | `120` | Maximum total wait for a SQLite store lock. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | Postgres server. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `autoontology` / `autoontology` | Postgres credentials; this user needs permission to create databases. |
| `POSTGRES_DATABASE` | `autoontology` | Catalog database. Source databases are `autoontology_src_<source>`; AutoOntology refuses any other name. |
| `AUTOONTOLOGY_CHAT_PROVIDER` | `ollama` | `ollama` or `openai` (any OpenAI-compatible endpoint). |
| `AUTOONTOLOGY_EMBED_PROVIDER` | `ollama` | `ollama` or `openai`. |
| `AUTOONTOLOGY_OLLAMA_ENDPOINT` | `http://localhost:11434/v1` | Ollama's OpenAI-compatible endpoint. |
| `AUTOONTOLOGY_OLLAMA_CHAT_MODEL` | `openai/qwen3:8b` | Chat model: an alias of `qwen3:8b` with `num_ctx` 16384 and thinking off, built by `setup`. The `openai/` prefix is part of the alias name. |
| `AUTOONTOLOGY_OLLAMA_EMBED_MODEL` | `bge-m3` | Embedding model (1024 dimensions). |
| `AUTOONTOLOGY_OLLAMA_NO_THINK` | `1` | The router adds `reasoning_effort: none`, so Ollama enforces JSON schemas. |
| `AUTOONTOLOGY_OPENAI_BASE_URL` | unset | Base URL including `/v1`. |
| `AUTOONTOLOGY_OPENAI_API_KEY` | unset | API key (masked everywhere). |
| `AUTOONTOLOGY_OPENAI_CHAT_MODEL` | unset | Chat model id as the endpoint knows it. |
| `AUTOONTOLOGY_OPENAI_EMBED_MODEL` | unset | Embedding model id (with `AUTOONTOLOGY_EMBED_PROVIDER=openai`). |
| `AUTOONTOLOGY_OPENAI_TIMEOUT_S` | `180` | Whole-request timeout of the router's openai passthrough. |
| `LLM_INVOKE_TIMEOUT_S` / `LLM_MAX_INFLIGHT` / `SEMANTIC_LLM_MAX_INFLIGHT` | `180` / `2` / `1` | Engine LLM limits, applied with the ollama chat provider unless you set them. |
| `KORDOC_CLI` | unset | Use another kordoc install (path to `dist/cli.js`). |
| `KORDOC_NODE` | `node` on PATH | Node binary for kordoc. |
| `AUTOONTOLOGY_KORDOC_TIMEOUT` | `300` | Seconds per document (kordoc always runs offline). |
| `AUTOONTOLOGY_CHUNK_TOKENS` / `_CHUNK_OVERLAP_TOKENS` / `_CHUNK_PACK_TOKENS` | `1200` / `100` / `400` | Chunk size, overlap when a section is split, and how far small neighbouring sections are packed together (tiktoken `cl100k_base` tokens). |
| `AUTOONTOLOGY_LLM_WORKERS` | `1` with ollama, else `3` | Parallel chunk extraction calls. |
| `AUTOONTOLOGY_EXTRACT_MODE` | `auto` | `auto`, `structured` or `json` extraction output. |
| `AUTOONTOLOGY_MERGE_THRESHOLD` | `0.80` | Embedding cosine above which two concepts go to the merge judge. |
| `AUTOONTOLOGY_MERGE_MAX_JUDGE` | `100` | Most merge-judge calls per consolidation. |
| `AUTOONTOLOGY_ALIGN_MIN_SIM` | `0.55` | Candidate Term cosine floor. |
| `AUTOONTOLOGY_ALIGN_MIN_CONF` | `0.7` | Judge confidence needed to apply a verdict (`same` also needs 0.90, see above). |
| `AUTOONTOLOGY_ALIGN_ALLOW_METRIC_SAME` | `0` | `1` lets a metric concept be judged `same` as a Term. |
| `AUTOONTOLOGY_INTEGRATION` | `0` | `1` runs the integration tests (real Postgres and Ollama; set it in the shell). |

The engine's own model variables (`DEFAULT_MODELS_*`, `REASONING_*`, `NON_REASONING_*`,
`EMBED_*`, `RERANK_*`) are derived from the provider settings. Values you set for them
are overridden.

## Limitations

- **Postgres is required.** The engine keeps its catalog and vectors in Postgres with
  pgvector; there is no SQLite-only mode.
- **Small local models merge conservatively.** With `qwen3:8b`, extraction is slow (one to
  two minutes per chunk), and the identity policy deliberately prefers `related` over a wrong
  `same`. Expect review proposals rather than many folded concepts.
- **OCR is untested.** Scanned PDFs and images go through kordoc's OCR path, which this
  project has not tested.
- **Ossie and OKF are pre-1.0 specifications** (Ossie 0.2.0.dev0, OKF v0.2) and may change;
  the vendored schemas are pinned in `third_party/ossie/`.
- **Mappings need keys.** Ossie object and link mappings are written only for datasets
  whose upstream semantic model declares a key. In the samples only `customers` does.
- **Snapshots are never pruned.** Each successful build adds a revision with the full model
  JSON.
- **`build --resume` hashes files, not database schemas.** It notices changed input files
  and documents, but a database source is identified by its URL only. Run a plain `build`
  after a schema change inside that database.
- **Finished runs list only the steps that ran.** Steps that were never reached do not
  appear as pending.
- **Upgrading a pre-release home:** store migration v2 drops older playground
  conversations, and the first `build --resume` after upgrading rebuilds every source once.
- Names stay in the source language: Korean concept names are kept in Ossie identifiers
  and OKF file names.

## Security notes

- The service and the engine backend bind to `127.0.0.1`. Requests with a non-local
  `Host` or a cross-site `Origin` are rejected. **There is no authentication:** do not
  expose either port, and do not put them behind a public proxy.
- Document text is treated as untrusted. It is quoted inside delimited prompt blocks,
  model output is validated against schemas (fail-closed), and the OKF bundle escapes
  Markdown and HTML in extracted text. If you render the `.md` files as HTML elsewhere,
  sanitize them.
- The SPARQL console is read-only and refuses `SERVICE`, `FROM` and `LOAD`.
- API keys are masked in logs, status responses and error bodies relayed from upstream.
- Database names are restricted to `autoontology` and `autoontology_src_*`, so
  AutoOntology never touches other databases on a shared Postgres server.

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `up` says a port is in use | Set `AUTOONTOLOGY_PORT` and/or `BACKEND_PORT` in `.env` (for example 7820 and 3121), then run `up` again. |
| `docker compose up -d` fails on port 5432 | A local Postgres already listens there. Either set `POSTGRES_PORT=5433` in `.env` before `docker compose up -d`, or use that Postgres directly. |
| `setup`: `extension "vector" is not available` | Install pgvector for your Postgres (`brew install pgvector`, or the `postgresql-17-pgvector` package), or use `docker compose`, which ships it. |
| `setup`: permission denied to create database | Give the user `CREATEDB`, or create the `autoontology` database yourself. |
| Chat model `openai/qwen3:8b` not found | Run `uv run autoontology setup` (or `setup --force-alias`). The `openai/` prefix is part of the Ollama alias name; `ollama list` should show it. |
| `doctor` FAIL "vector tables": the embedder emits N dimensions | You switched embedding models after ingesting. Restore the old `*_EMBED_MODEL`, or remove every source (`source rm`) so that the vector tables are empty. An empty table is recreated automatically. |
| `build` fails a document with `ENGINE_MISSING` | Install kordoc (`uv run autoontology tools install-kordoc`, Node 20 or newer), then `build --resume`. |
| `/ui/` returns 503 `ui_not_built` | The committed UI is missing (for example in a source archive). Run `uv run autoontology tools build-ui` (needs Node and pnpm). |
| A build seems stuck | Run `uv run autoontology runs` and read `data/logs/`. Each chunk takes one to two minutes with `qwen3:8b`. |

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q   # unit tests
AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration                    # real PG + Ollama
cd ui && pnpm install && pnpm lint && pnpm test && pnpm build
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

![Graph page](docs/screenshots/graph.png)
![Exports page](docs/screenshots/exports.png)

## Credits and licences

AutoOntology's own code is released under the [MIT License](LICENSE), Copyright (c) 2026
kimtoma.

AutoOntology is built on **NVIDIA Auto Ontology** (Apache-2.0), included unmodified as a
git submodule. It vendors the **Apache Ossie** JSON Schemas (Apache-2.0) and follows the
**Open Knowledge Format** specification (Apache-2.0), which is referenced, not copied. It
uses **kordoc** (MIT; installed at runtime, not redistributed), **shadcn/ui** components
(MIT), and a sample HWP file from **rhwp** (MIT). AutoOntology is an independent project,
not affiliated with or endorsed by NVIDIA, the Apache Software Foundation or Google. See
[NOTICE.md](NOTICE.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
