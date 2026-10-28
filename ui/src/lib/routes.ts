/** Where an old route lands: /runs -> /build, /settings -> /status, /concepts -> /ontology
    (playground chips of older services link `/concepts?concept=<store id>`, the concept API
    id is `concept-<store id>`). null = not an old route. */
export function legacyTarget(pathname: string, search: string): string | null {
  const params = new URLSearchParams(search)
  if (pathname === "/runs") return `/build${search}`
  if (pathname === "/settings") return "/status"
  if (pathname === "/concepts") {
    const concept = params.get("concept")
    if (concept && !/^(term|concept)-/.test(concept)) params.set("concept", `concept-${concept}`)
    const qs = params.toString()
    return `/ontology${qs ? `?${qs}` : ""}`
  }
  return null
}
