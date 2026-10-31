# Statechart guide and user scenarios

This is a documentation snapshot of application commit `6896be4`, produced with the
installed pstack `statechart` skill 0.15.36, TLC 1.7.4 and Pele 1.9.0. The models
were authored and checked outside the repository. Current implementation and planned
agent delegation are explicitly separate. The guide contains six charts and the
21 scenarios from the earlier lifecycle document, including concrete UI/CLI paths,
confirmation, failure/recovery and Enterprise AX time-measurement boundaries.

The guide opens with a whole Knowledge OS reading map: one-time setup, the repeated
input -> candidate -> review/evaluation -> publication -> use -> feedback loop,
and separate recovery and future domain/agent expansion. It explains incoming data,
preserved knowledge/evidence, human responsibilities and usable outcomes before
introducing individual scenarios. Each phase opens related scenarios; a scenario
returns to the phase it was opened from, including when it belongs to several phases.
All 21 scenarios have a primary location and 27 phase-to-scenario connections.
Search filters reset on navigation; scenario fragments, browser back and keyboard
navigation are supported.

`knowledge_os_overview.py` is the editorial navigation source. This map connects the
six existing checked models; it adds no TLC states, transitions or proof claims.
The versioned model evidence below remains unchanged. Identity foundation commit
`3100ed9` was added later, but its business HTTP/CLI/UI adapters are still pending;
the guide does not interpret that core as fixing the individual-approval gap.

## Generate the standalone HTML

From this isolated worktree, using the installed project virtual environment:

```sh
.venv/bin/python docs/statecharts/render_guide.py \
  --output /Users/macmini/Projects/AutoOntology/data/ontology-ops-local/autoontology-statechart-guide.html
```

The output includes SVG and fonts and makes no network requests when opened. The
renderer copies the checked model sources and raw evidence beside the HTML in
`statecharts/`. It does not rerun TLC, run application commands, activate identity
work, migrate a primary store or deploy anything. Run the checks below when changing
a model, rather than treating old reports as evidence of a new model.

## Repeat a model check

The skill's command requires a new output folder. It accepts explicit Java and
TLC paths, avoiding a global tool installation:

```sh
JAVA=/opt/homebrew/opt/openjdk@25/bin/java \
TLA2TOOLS=/private/tmp/autoontology-statechart-20261031/runtime/tla2tools.jar \
JAVA_TOOL_OPTIONS=-Xmx512m \
/Users/macmini/.codex/plugins/cache/cursor-plugins/pstack/0.15.36/skills/statechart/scripts/statechart \
  docs/statecharts/models/Publication.tla \
  --view docs/statecharts/models/Publication.view.mjs \
  --out /private/tmp/autoontology-statechart-new-check
```

The pinned jar is the official TLA+ release at
`https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar`, with
SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
Pele rendering uses `render(source, {limit: 1000000, outputLimit: 8000000,
maxEdges: 10000})`; retain the SVG next to its exact Mermaid source.

## Results and evidence limits

The six finite models explored 82 abstract states and 136 transitions. Thirty
checks held. `Publication.VerifiedHumanApproval` fails on the shortest seven-step
path: empty workspace -> connection check -> build -> candidate -> review ->
question validation -> prepared artifacts -> publication. The application requires
content review/validation but does not yet require a verified individual Principal.
The controlled replay in `publication-replay.json` reproduced this against the
reference commit using fake model functions and a separate temporary store. It did
not call a real model or mutate the normal local/public workspace.

`AgentTarget` passes its selected design rules; this is not a claim that the planned
identity/delegation/approval workflow is implemented. The models do not prove semantic
answer correctness, actual database-account privileges, organization-wide access
control, concurrent load or multi-service compensation. RAG transport completion is
kept separate from meaningful correctness and citation support. Current CLI coverage
gaps are labeled; no invented commands are advertised as available.

The charts omit terminal stuttering and some transitions that retain the same
visible state. Reports give the omitted counts. Invariants were retained even when
they fail; no rule was weakened merely to make it pass.

The separate identity integration in the worktree is in progress and is not
part of this model snapshot. Model sources/evidence and this renderer can be reverted
independently from that work. Runtime HTML, verification screenshots, model inputs,
raw TLC output and stage change records are available in the ignored local runtime.
