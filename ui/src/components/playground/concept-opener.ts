import { createContext } from "react"

/** Opens the concept sheet of the page (Playground); null = chips link to the Ontology page. */
export const ConceptOpener = createContext<((id: string) => void) | null>(null)
