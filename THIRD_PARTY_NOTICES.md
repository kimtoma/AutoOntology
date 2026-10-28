# Third-party notices

AutoOntology is an independent project and is not affiliated with or endorsed by NVIDIA,
the Apache Software Foundation, or Google.

| Component | License | How it is used | Source |
|---|---|---|---|
| NVIDIA Auto Ontology | Apache-2.0 | Git submodule `vendor/auto-ontology`, unmodified; runs as the engine backend and is imported in-process | https://github.com/NVIDIA/auto-ontology |
| Apache Ossie JSON Schemas | Apache-2.0 | `ossie-schema.json`, `ontology.json`, `mapping.json` copied unchanged into `third_party/ossie/` (with `LICENSE`, `NOTICE`, provenance in `README.md`) and shipped as package data in `autoontology/export/schemas/ossie/`; used to validate the Ossie export | https://github.com/apache/ossie |
| kordoc | MIT | Optional document engine (HWP/HWPX/PDF/DOCX/PPTX), installed at runtime via npm into `<home>/kordoc` by `autoontology setup --docs`; not redistributed | https://github.com/chrisryugj/kordoc |
| shadcn/ui | MIT | Component sources copied into `ui/src/components/ui` | https://github.com/shadcn-ui/ui |
| rhwp sample HWP | MIT | `samples/unstructured/사업수행계획서.hwp` and `tests/fixtures/hwp/biz_plan.hwp`, copied unchanged; license text in `samples/unstructured/LICENSE` and `tests/fixtures/hwp/LICENSE` | https://github.com/edwardkim/rhwp |

Python and JavaScript dependencies installed by `uv sync` / `pnpm install` are not
redistributed in this repository; each keeps its own license (see `uv.lock` and
`ui/pnpm-lock.yaml`).

Default local models pulled by `autoontology setup` (not redistributed): `qwen3:8b`
(Apache-2.0) and `bge-m3` (MIT), via Ollama.
