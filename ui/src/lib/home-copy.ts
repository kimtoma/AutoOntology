/* The words of the Home page's five steps, kept apart so the tests can check them. */

import type { StepId } from "@/lib/checklist"

export const GUIDE_URL = "https://github.com/kimtoma/AutoOntology/blob/main/docs/GUIDE.md"

interface StepCopy { title: string; description: string; help: [string, string] }

/** Plain words: the reader is a domain expert, not an ontology engineer. */
export const STEP_COPY: Record<StepId, StepCopy> = {
  models: {
    title: "Models connected",
    description: "The service, its database and the language model that reads your data are running.",
    help: ["A language model reads your tables and documents and writes down the business concepts it finds.",
           "It runs on this machine (Ollama), through your Claude or ChatGPT subscription, or at an OpenAI-compatible endpoint; the Test button sends it one short question."],
  },
  sources: {
    title: "Sources added",
    description: "Tell the service which tables and documents to learn from.",
    help: ["A source is one folder of table files, one database or one folder of documents.",
           "Start with the sample data of a fictional company, or add your own files; nothing leaves this machine until you build."],
  },
  build: {
    title: "Ontology built",
    description: "The model turns your sources into one list of business concepts and how they relate.",
    help: ["Building finds the business concepts in your tables and documents, then matches the ones that mean the same thing.",
           "It takes minutes to hours depending on the model and the amount of data; you can close this page while it runs."],
  },
  explore: {
    title: "Explore",
    description: "Browse the concepts, see them as a graph and ask questions about your data.",
    help: ["The ontology lists every business concept with its definition and the places it was found.",
           "The graph shows how concepts connect, and the playground answers questions in plain language, SQL or SPARQL."],
  },
  export: {
    title: "Export",
    description: "Save the ontology as files other tools can open.",
    help: ["An export writes the latest revision as standard ontology files (Turtle, JSON-LD) and two bundles for data catalogs.",
           "Each export lands in its own folder named after the revision, so earlier ones stay as they were."],
  },
}
