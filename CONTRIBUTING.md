# Contributing

Thanks for your interest. AutoOntology is a small MVP, so issues and focused pull requests
are the most helpful contributions.

## Set up

```bash
git clone --recurse-submodules https://github.com/kimtoma/AutoOntology.git
cd AutoOntology
uv sync
```

The Quickstart in the [README](README.md) brings up a full local stack (Postgres,
Ollama). The unit tests need neither.

## Before you open a pull request

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
cd ui && pnpm install && pnpm lint && pnpm test && pnpm build
```

- Integration tests use a real Postgres and Ollama, and run only with
  `AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration`. Please run the ones related
  to your change if you can.
- If you change `ui/`, rebuild the committed bundle with `uv run autoontology tools build-ui`
  and commit `autoontology/web/static/dist/` in the same pull request.
- If `ui/pnpm-lock.yaml` changes, regenerate `docs/licenses/ui-bundle.md` and commit it
  with the lockfile: run `pnpm install --frozen-lockfile` and then `pnpm licenses:generate`
  in `ui/` (or `node scripts/ui-licenses.mjs` from the repository root). Add
  `--check` to the node command to only verify that the file is up to date.
- Never edit `vendor/auto-ontology`. It is the unmodified NVIDIA Auto Ontology submodule;
  work around engine behaviour in `autoontology/` and pin the behaviour with a test.
- New Python files start with `# SPDX-License-Identifier: MIT`.
- Keep user-facing text in English. Prompts and sample data may stay in Korean.
- Never commit `.env`, API keys, or data from `data/` or `exports/`.

## Licence

By contributing you agree that your contribution is licensed under the MIT License of
this repository.
