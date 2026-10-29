---
name: setup-autoontology
description: Set up and run AutoOntology locally in one go — check prerequisites, install, configure the chat provider (default the user's Claude login, no API key), build the quickstart sample set and show the web UI URL. Use when the user asks to install, set up, bootstrap, start or try AutoOntology.
---

# Set up AutoOntology

1. Work from the repository root (the folder with `scripts/bootstrap.sh`). If the repo is
   not cloned yet, run `git clone --recurse-submodules https://github.com/kimtoma/AutoOntology.git`
   in the folder the user named (default `~/AutoOntology`) and `cd` into it.
2. Run the bootstrap with the provider the user named (default `claude`):

   ```bash
   scripts/bootstrap.sh --yes --provider claude
   ```

   Other values: `codex`, `ollama`, `openai`. Add `--set hanbit` for the full sample
   company, `--no-demo` to stop after configuration, `--lang ko` (or
   `AUTOONTOLOGY_LANG=ko`) when the user writes in Korean. Use `--dry-run` first if the
   user wants to see the plan. The whole run takes about 10 minutes (the quickstart build
   alone is about 8): run it in the background and wait for it to finish, or give the
   command a timeout of at least 15 minutes. Always pass `--yes` from an agent's shell;
   without a terminal the script exits 2 otherwise.
3. Handle the exit code:
   - `0`: done.
   - `3`: the provider CLI is not logged in. Ask the user to run `claude auth login`
     themselves (Claude Enterprise/Team: sign in with the organization account) and
     confirm with `claude auth status`; then run the script again. Never handle their
     credentials.
   - `4`: a prerequisite is missing (Docker or Postgres, Ollama, uv, git, the provider
     CLI) or `claude auth status` could not be read (suggest `claude update`). Show the
     hint the script printed; install it only if the user agrees, then re-run. If
     `claude` works in the user's terminal but not here, it is probably a shell alias: set
     `AUTOONTOLOGY_CLAUDE_BIN=/path/to/claude` and re-run.
   - `1`: a step failed. Read the output above the failure, run `uv run autoontology doctor`
     for remedies, fix, re-run (the script is idempotent; `uv run autoontology demo
     --resume` continues a build).
4. Report: the UI URL the script printed (`http://127.0.0.1:<port>/ui/`), the total time,
   and a short summary of what was built — the sample sources (`quick`, `quick-docs`), the
   snapshot revision and export folder from the `demo` output, and where to look next
   (Ontology → Graph → Playground). Mention `uv run autoontology down` to stop the services.

Do not edit files under `vendor/`, do not print secrets, and only touch databases named
`autoontology` or `autoontology_src_*`.
