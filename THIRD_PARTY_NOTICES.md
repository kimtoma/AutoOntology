# Third-party notices

AutoOntology is an independent project and is not affiliated with or endorsed by NVIDIA,
the Apache Software Foundation, or Google.

## Components in or referenced by this repository

| Component | Version / commit | License | URL | How it is used |
|---|---|---|---|---|
| NVIDIA Auto Ontology | submodule commit `eb8dd06` (`eb8dd0621d07db3033fe3fddc68f62ff72614610`, 2026-10-25), package `auto-ontology-server` 1.1.0 | Apache-2.0 | https://github.com/NVIDIA/auto-ontology | Git submodule `vendor/auto-ontology`, unmodified. Runs as the engine backend and is imported in-process (ingest, semantic compilation, vector tables). Not copied into AutoOntology's code. |
| Apache Ossie JSON Schemas (`ossie-schema.json`, `ontology.json`, `mapping.json`) | commit `89e2e17f8249e68f30df7b29ac8852fa9dd24501` (2026-10-27), spec 0.2.0.dev0 | Apache-2.0 | https://github.com/apache/ossie | Vendored unchanged into `third_party/ossie/` (with `LICENSE`, `NOTICE` and provenance in `README.md`) and shipped as package data in `autoontology/export/schemas/ossie/`. Used to validate the Ossie export. |
| `apache-ossie-nvidia-auto-ontology` (Ossie converter) | 0.1.0.dev0 | Apache-2.0 | https://github.com/apache/ossie | Installed as a dependency of the engine. Used to round-trip the exported semantic model. Not redistributed. |
| Open Knowledge Format (OKF) specification | v0.2 | Apache-2.0 | https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md | Referenced only. The OKF exporter and validator are written from the specification; no text or code is copied. |
| kordoc | 4.24.13 (default of `tools install-kordoc`) | MIT | https://github.com/chrisryugj/kordoc | Optional document engine (HWP/HWPX/PDF/DOCX/PPTX, image and scanned-PDF OCR). `autoontology setup --docs` installs it at runtime with npm into `<home>/kordoc`, then downloads its OCR models (see Models), and it runs as a subprocess. Not redistributed. |
| shadcn/ui | component sources copied with the shadcn CLI | MIT | https://github.com/shadcn-ui/ui | Component sources in `ui/src/components/ui/`, adapted, and compiled into the web UI bundle. Copyright (c) 2023 shadcn; the MIT licence text (fetched 2026-10-28) is in [`ui/src/components/ui/LICENSE.md`](ui/src/components/ui/LICENSE.md). |
| rhwp sample HWP (`samples/biz_plan.hwp`) | commit `1a76570e833917d15817415a53c09ad61ab3203f` | MIT | https://github.com/edwardkim/rhwp | `samples/unstructured/사업수행계획서.hwp` and `tests/fixtures/hwp/biz_plan.hwp`, copied unchanged. The license text is in `samples/unstructured/LICENSE` and `tests/fixtures/hwp/LICENSE`. |
| Web UI bundle dependencies (React, React Router, three.js, Radix UI, TanStack Query, lucide-react, and others) | as locked in `ui/pnpm-lock.yaml` | mostly MIT; also ISC, BSD-3-Clause, Apache-2.0 (class-variance-authority) and 0BSD (tslib) | see the list | Bundled, minified, into `autoontology/web/static/dist/`. Every package and its license text are listed in [`docs/licenses/ui-bundle.md`](docs/licenses/ui-bundle.md). |

## Models (not redistributed)

`autoontology setup` pulls these through Ollama; they are not part of this repository.

| Model | License | Use |
|---|---|---|
| `qwen3:8b` (Qwen3) | Apache-2.0 | Default chat model (extraction, judges, playground, engine compile). |
| `bge-m3` (BAAI) | MIT | Default embedding model. |
| PP-OCRv5 mobile det + korean rec (PaddleOCR) | Apache-2.0 | OCR of images and scanned PDFs. Downloaded by kordoc (`tools install-kordoc`) into kordoc's model cache, not by Ollama. |

A vision model you set with `AUTOONTOLOGY_VISION_MODEL` (optional image descriptions) is
pulled through Ollama and is subject to its own license.

A hosted model you configure through `AUTOONTOLOGY_OPENAI_*` is subject to its provider's
terms.

## Python and JavaScript dependencies (not redistributed)

`uv sync` and `pnpm install` download the dependencies; this repository does not contain
them, and each keeps its own license (see `uv.lock` and `ui/pnpm-lock.yaml`).

- **Python runtime:** mostly permissive (MIT, BSD-3-Clause, Apache-2.0), for example
  FastAPI, uvicorn, httpx, pydantic, rdflib, pandas, numpy, DuckDB, jsonschema, PyYAML and
  tiktoken. `psycopg` is LGPL-3.0-only and is used unmodified as an installed library.
  The engine pulls further packages (LangChain, the database connectors and others) under
  their own licenses.

| Package | Version | License | URL | Use |
|---|---|---|---|---|
| olefile | >= 0.47 (locked 0.47) | BSD-2-Clause | https://github.com/decalage2/olefile | Runtime: opens legacy Word `.doc` files (OLE compound files) for the native `.doc` reader. |
| python-calamine | >= 0.4 (locked 0.9.0) | MIT | https://github.com/dimastbk/python-calamine | Runtime: reads Excel 97-2003 `.xls` workbooks in structured sources. |
| xlwt | >= 1.3 (locked 1.3.0) | BSD | https://github.com/python-excel/xlwt | Development only: writes `.xls` test fixtures. |

- **Python development only:** pytest, pytest-asyncio, ruff, playwright and python-docx are
  permissive. `pymupdf` (AGPL-3.0 or commercial) is a development-only dependency, used
  solely to regenerate the sample PDF (`scripts/gen_unstructured_samples.py`). It is not a
  runtime dependency, and AutoOntology does not import it at runtime.
- **JavaScript build and test tooling** (Vite, TypeScript, Vitest, oxlint, Tailwind CSS,
  Testing Library, jsdom): permissive licenses. It is used only to build and test the UI.
