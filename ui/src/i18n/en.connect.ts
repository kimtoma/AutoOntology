/* English strings of the Connect page: tokens and settings for outside agents (MCP, REST). */
export const enConnect = {
  "nav.connect": "Connect",
  "nav.connect.hint": "Outside agents read the ontology over MCP and REST",

  "connect.title": "Connect",
  "connect.description":
    "Let Claude Code, Claude Desktop, Cursor or your own scripts use the ontology. A read token searches concepts "
    + "and objects, opens definitions with their evidence and runs SPARQL; a propose or execute token can also "
    + "submit actions, within each action's agent policy and approval rules. "
    + "Give each agent its own token. A token is shown only once, and you can revoke it at any time.",

  "connect.endpoints": "Endpoints",
  "connect.endpoints.hint": "Every request needs a token: Authorization: Bearer aok_…",
  "connect.endpoint.mcp": "MCP (Streamable HTTP)",
  "connect.endpoint.rest": "REST base",
  "connect.endpoint.openapi": "OpenAPI",
  "connect.endpoint.openapiNote": "needs the token too",
  "connect.rate": "Up to {n} requests a minute per token.",
  "connect.copy": "Copy",
  "connect.copied": "Copied",
  "connect.copyLabel": "Copy {what}",
  "connect.copyFailed": "Could not copy: select the text and copy it yourself.",

  "connect.tokens": "Tokens",
  "connect.tokens.hint": "One token per agent. Calls count the last 30 days.",
  "connect.create": "Create token",
  "connect.empty.title": "No tokens yet",
  "connect.empty.hint": "Create a token for each agent that should read the ontology.",
  "connect.col.name": "Name",
  "connect.col.status": "Status",
  "connect.col.revision": "Revision",
  "connect.col.expires": "Expires",
  "connect.col.lastUsed": "Last used",
  "connect.col.calls": "Calls",
  "connect.col.actions": "Actions",
  "connect.status.active": "Active",
  "connect.status.revoked": "Revoked",
  "connect.status.expired": "Expired",
  "connect.revision.latest": "Follows the latest",
  "connect.revision.pinned": "Pinned to revision {n}",
  "connect.never": "Never",
  "connect.notUsed": "Not used yet",
  "connect.rotate": "Rotate",
  "connect.revoke": "Revoke",
  "connect.rotateLabel": "Rotate token {name}",
  "connect.revokeLabel": "Revoke token {name}",
  "connect.rotate.title": "Rotate “{name}”?",
  "connect.rotate.body":
    "You get a new token right away. The old one stops working immediately, so every agent that uses it "
    + "needs the new token.",
  "connect.rotate.go": "Rotate token",
  "connect.revoke.title": "Revoke “{name}”?",
  "connect.revoke.body":
    "The token stops working immediately and cannot be switched back on. Its history stays in the usage numbers.",
  "connect.revoke.go": "Revoke token",
  "connect.revoked": "Token “{name}” revoked",
  "connect.failed": "Failed: {error}",

  "connect.dialog.title": "Create a token",
  "connect.dialog.description": "Name it after the agent that will use it, for example claude-code or a script name.",
  "connect.field.name": "Name",
  "connect.field.namePlaceholder": "claude-code",
  "connect.field.revision": "Revision",
  "connect.field.revision.latest": "Follow the latest revision",
  "connect.field.revision.pin": "Revision {n} ({date})",
  "connect.field.revision.hint":
    "Pin a reviewed revision to keep the agent on it while you keep building.",
  "connect.field.expiry": "Expires",
  "connect.expiry.30": "In 30 days",
  "connect.expiry.90": "In 90 days",
  "connect.expiry.never": "Never",
  "connect.creating": "Creating…",

  "connect.once.title": "Token for “{name}”",
  "connect.once.rotatedTitle": "New token for “{name}”",
  "connect.once.description": "Copy the token now. It is shown only this once; AutoOntology keeps only a hash.",
  "connect.once.warning": "This token will not be shown again. If you lose it, rotate the token.",
  "connect.once.token": "Token",
  "connect.once.snippets": "Add it to your agent",

  "connect.howto": "How to connect",
  "connect.howto.hint": "Replace <TOKEN> with your token. Settings shown right after creating a token have it filled in.",
  "connect.snippet.claudeCode": "Claude Code",
  "connect.snippet.desktop": "Claude Desktop / Cursor",
  "connect.snippet.curl": "REST (curl)",
  "connect.snippet.claudeCode.hint": "Run in a terminal.",
  "connect.snippet.desktop.hint":
    "Add to claude_desktop_config.json (Claude Desktop) or .cursor/mcp.json (Cursor).",
  "connect.snippet.curl.hint": "Search concepts, then run a SPARQL query.",
  "connect.snippet.sampleQuery": "contract",

  "connect.tools": "Tools",
  "connect.tools.hint": "What an agent can call (read-only). Protocol {protocol}.",
  "connect.tools.params": "Parameters",
  "connect.tools.noParams": "No parameters",
  "connect.tools.required": "required",
  "connect.tool.list_revisions":
    "List the ontology revisions, newest first: time, the run that wrote each, counts and included sources.",
  "connect.tool.get_ontology_summary":
    "Summary of one revision: workspace, base IRI, sources, counts of classes, individuals and properties, and alignments.",
  "connect.tool.search_concepts":
    "Search concepts (table terms and document concepts) by name, synonym or definition. An empty query lists them.",
  "connect.tool.get_concept":
    "One concept by id or exact name: definition, synonyms, relations, evidence quotes with their documents, alignments and review decisions.",
  "connect.tool.sparql_query":
    "Run a read-only SPARQL query over the revision's RDF/OWL graph. SERVICE, FROM and LOAD are refused; rows are capped.",
  "connect.tool.list_exports":
    "List the export folders (RDF/OWL, GraphML, Ossie, OKF) per revision; REST can download one as a zip.",

  "connect.skill": "Agent skill",
  "connect.skill.hint":
    "Download the active functions, actions and workflows as an Agent Skill: an agent that loads it knows your object types, rules and the safe order of tool calls (preview before submit, never assume approval).",
  "connect.skill.export": "Export skill",
  "connect.skill.md": "explains the object types, functions, actions, workflows and the procedure, in English.",
  "connect.skill.mcp": "connects to this server: replace {{AUTOONTOLOGY_URL}} and <token> (a token is never included).",
  "connect.skill.defs": "the active definitions as YAML (ao logic apply -f reads them).",

  "connect.usage": "Usage",
  "connect.usage.hint": "Tool calls in the last {days} days.",
  "connect.usage.total": "Calls",
  "connect.usage.ok": "Succeeded",
  "connect.usage.none": "No calls yet.",
  "connect.usage.byTool": "By tool",
  "connect.usage.byDay": "By day",
  "connect.usage.avg": "avg {ms} ms",
  "connect.usage.dayCalls": "{day}: {n} calls",
} as const
