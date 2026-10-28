# Notice

AutoOntology
Copyright (c) 2026 kimtoma

AutoOntology's own code is licensed under the MIT License (see `LICENSE`). Python files
carry the header `# SPDX-License-Identifier: MIT`.

AutoOntology is built on **NVIDIA Auto Ontology** (Apache License 2.0,
https://github.com/NVIDIA/auto-ontology). The engine is included as an unmodified git
submodule at `vendor/auto-ontology` and keeps its own license and notices; AutoOntology
does not copy or change its code.

This repository also contains, unmodified and under their own licenses:

- the Apache Ossie JSON Schemas (Apache License 2.0) in `third_party/ossie/` and
  `autoontology/export/schemas/ossie/`, with their `LICENSE` and `NOTICE` files;
- a sample HWP file from rhwp (MIT) in `samples/unstructured/` and `tests/fixtures/hwp/`,
  with its license text;
- shadcn/ui component sources (MIT, Copyright (c) 2023 shadcn), adapted, in
  `ui/src/components/ui/`, with the licence text in `ui/src/components/ui/LICENSE.md`;
- a minified build of the web UI in `autoontology/web/static/dist/`, which bundles npm
  packages listed with their license texts in `docs/licenses/ui-bundle.md`.

AutoOntology is an independent project and is not affiliated with or endorsed by NVIDIA,
the Apache Software Foundation, or Google. "NVIDIA", "Apache", "Ossie" and "Google" are
trademarks of their respective owners and are used here only to identify the software
and specifications AutoOntology works with.

Third-party components and their licenses are listed in `THIRD_PARTY_NOTICES.md`.
