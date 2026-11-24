import { Link, NavLink, type LinkProps, type NavLinkProps } from "react-router"

import { useProjectPath } from "@/lib/project"

/** `Link` whose app path ("/build") goes under the current project ("/p/<slug>/build"). */
export function ProjectLink({ to, ...props }: LinkProps) {
  const path = useProjectPath()
  return <Link to={typeof to === "string" ? path(to) : to} {...props} />
}

/** `NavLink` under the current project. */
export function ProjectNavLink({ to, ...props }: NavLinkProps) {
  const path = useProjectPath()
  return <NavLink to={typeof to === "string" ? path(to) : to} {...props} />
}
