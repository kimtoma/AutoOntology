# AutoOntology

> Work in progress: this public MVP is being assembled; interfaces may change.

AutoOntology turns structured data (CSV, XLSX, SQLite, Parquet, databases) and unstructured
documents (Markdown, text, HTML, and with the optional kordoc engine HWP/PDF/DOCX/PPTX) into
one ontology, built on NVIDIA Auto Ontology, and exports it as RDF (Turtle, JSON-LD),
GraphML, Apache Ossie and the Open Knowledge Format. Everything runs locally with Postgres +
pgvector and Ollama, or with any OpenAI-compatible endpoint.

## Quickstart

```bash
git clone --recurse-submodules <repository-url> AutoOntology
cd AutoOntology
uv sync
docker compose up -d                 # Postgres 17 + pgvector
uv run autoontology setup            # database, migrations, Ollama models (--docs: kordoc)
uv run autoontology up               # engine backend (:3101) + service (:7800)
uv run autoontology source add samples/structured --name hanbit
uv run autoontology source add samples/unstructured --name hanbit-docs --kind documents
uv run autoontology build
uv run autoontology export
uv run autoontology ui
```

Configuration: copy `.env.example` to `.env` (optional; every value has a default).

## License

MIT for AutoOntology's own code; see `NOTICE.md` and `THIRD_PARTY_NOTICES.md` for the
engine (Apache-2.0) and other components.
