# Illustrated Knowledge OS guide

The standalone Korean field guide explains the operating lifecycle, structured and
unstructured inputs, candidate/publication separation, query routes, local runtime
boundary, external Codex generation, and human/agent responsibilities. It reuses
the local lifecycle, statechart and implementation ledger. Current, partial and
planned capabilities remain distinct. Examples are fictional.

Generate next to the existing local documents (no network or application execution):

```sh
.venv/bin/python scripts/render_illustrated_guide.py --output /absolute/path/to/ontology-ops-local/autoontology-illustrated-guide.html
```

The output embeds all 27 Hairline SVG figure engines, scripts and font bytes. It
needs no server, external fonts, CDN, npm install or model requests. Links to the
existing lifecycle/statechart/change records require those sibling files; the
visual guide itself works independently. A companion generation JSON records
the HTML hash and references.

Hairline 0.5.1 is the unmodified MIT-licensed official npm distribution, verified
against its registry SHA-512 integrity. The generator removes only its source-map
URL in the embedded copy and encloses document controls in a separate lexical
block. Upstream source and license are retained here. Font bytes and OFL notices
come from the application's committed font assets.

Sources: https://hairline.lucasmarkes.com/docs and the implementation ledger in
`../KNOWLEDGE_OS_IMPLEMENTATION.md`. No new dependencies are installed.

Executed Chromium checks: 27 SVG figures, six phase selectors, three query modes,
keyboard tabs, technical-label toggle, play/pause and reduced motion, print/PDF,
valid internal anchors, no horizontal overflow at 1440/1024/768/390 px, no page
errors and no external runtime requests. Desktop/mobile screenshots were visually
inspected. Rebuilding from this tracked script reproduces the verified HTML bytes.
The private runtime retains the output, captures and verification manifest.
