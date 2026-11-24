/* The project a page works on: the `/p/:project` route sets it (React context for links and
   the archived state; `setCurrentProject` in api.ts for API URLs and query keys). Page links
   are written as before ("/build", "/ontology?concept=x"); `projectPath` puts them under the
   current project ("/p/<slug>/build"). */

import { createContext, useCallback, useContext } from "react"
import { useNavigate, type NavigateOptions } from "react-router"

import { t, type MessageKey } from "@/i18n/core"
import { ApiError, errorMessage, type Project, type ProjectList } from "@/lib/api"

export interface ProjectScope {
  /** the slug in the URL */
  slug: string
  /** its row of `GET /api/projects` (null while unknown: list not loaded or unreachable) */
  project: Project | null
  /** read-only: writes are refused (409 project_archived) */
  archived: boolean
}

export const ProjectContext = createContext<ProjectScope | null>(null)

/** The current project scope, or null outside a project route (tests rendering a bare page). */
export const useProject = () => useContext(ProjectContext)

/** Whether the current project is archived (read-only). */
export const useArchived = () => useContext(ProjectContext)?.archived ?? false

/** The display name of *project* (its name, else its slug). */
export const projectName = (project: Pick<Project, "name" | "slug"> | null | undefined, slug = "") =>
  project?.name?.trim() || project?.slug || slug

/** The project the app opens by default: the installation's default project when the caller
    may open it, else their first active (then archived) project; null when they are in none
    (accounts mode: a user nobody added to a project yet). */
export function homeSlug(list: ProjectList): string | null {
  const ps = list.projects
  if (ps.some((p) => p.slug === list.default)) return list.default
  return (ps.find((p) => !p.archived) ?? ps[0])?.slug ?? null
}

/** Pages that are not inside a project (no `/p/<slug>` prefix). */
const UNSCOPED = /^\/(p|projects|users|login|invite)(\/|\?|#|$)/

/** *to* (an app path such as "/build?x=1") under project *slug*: "/p/<slug>/build?x=1".
    Paths that are already scoped, unscoped pages and relative paths stay as they are. */
export function projectPath(slug: string | null | undefined, to: string): string {
  if (!slug || !to.startsWith("/") || UNSCOPED.test(to)) return to
  const base = `/p/${encodeURIComponent(slug)}`
  if (to === "/") return base
  if (to.startsWith("/?") || to.startsWith("/#")) return base + to.slice(1)
  return base + to
}

const SCOPED = /^\/p\/([^/?#]+)(\/[^?#]*)?$/

/** The page part of a router *pathname*: "/p/main/build" -> "/build", "/p/main" -> "/". */
export function pagePathname(pathname: string): string {
  const m = SCOPED.exec(pathname)
  if (!m) return pathname
  return m[2] && m[2] !== "/" ? m[2].replace(/\/+$/, "") : "/"
}

/** The project slug of a router *pathname* ("/p/main/build" -> "main"), or null. */
export function pathnameProject(pathname: string): string | null {
  const m = SCOPED.exec(pathname)
  return m ? decodeURIComponent(m[1]) : null
}

/** `(to) => projectPath(<current project>, to)`. */
export function useProjectPath(): (to: string) => string {
  const slug = useContext(ProjectContext)?.slug
  return useCallback((to: string) => projectPath(slug, to), [slug])
}

/** `useNavigate` for app paths under the current project. */
export function useProjectNavigate() {
  const navigate = useNavigate()
  const path = useProjectPath()
  return useCallback((to: string, options?: NavigateOptions) => navigate(path(to), options), [navigate, path])
}

export const LAST_PROJECT_KEY = "autoontology.project"

export function rememberProject(slug: string) {
  try { localStorage.setItem(LAST_PROJECT_KEY, slug) } catch { /* private mode */ }
}

export function lastProject(): string | null {
  try { return localStorage.getItem(LAST_PROJECT_KEY) } catch { return null }
}

/** A slug suggestion for a project *name*: lowercase ASCII letters, digits and "-", starting
    with a letter, at most 24 characters; "" when the name has none (Hangul names). */
export function suggestSlug(name: string): string {
  const s = name.normalize("NFKD").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^[^a-z]+/, "").slice(0, 24).replace(/-+$/, "")
  return s.length >= 2 ? s : ""
}

/** The first free "project-N" among *taken* slugs. */
export function freeSlug(taken: Iterable<string>, prefix = "project"): string {
  const set = new Set(taken)
  for (let n = set.size + 1; ; n++) {
    const s = `${prefix}-${n}`
    if (!set.has(s)) return s
  }
}

const PROJECT_ERRORS = new Set([
  "invalid_slug", "project_exists", "run_active", "exports_conflict", "default_project", "project_archived",
  "unknown_project",
])

/** A project API error in the UI language (409 run_active, exports_conflict, ...). */
export function projectErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.code && PROJECT_ERRORS.has(err.code)) {
    return t(`projects.error.${err.code}` as MessageKey)
  }
  return errorMessage(err)
}
