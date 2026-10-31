export const IDENTITY_REQUIRED = "autoontology:identity-required"
export const AUTHORITY_CHANGED = "autoontology:authority-changed"

export function identityResponse(status: number) {
  if (typeof window === "undefined") return
  if (status === 401) window.dispatchEvent(new Event(IDENTITY_REQUIRED))
  if (status === 403) window.dispatchEvent(new Event(AUTHORITY_CHANGED))
}
