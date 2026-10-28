/* SPARQL 콘솔 over the source's latest exported ontology.ttl. */

import { useMutation, useQuery } from "@tanstack/react-query"
import { Loader2, Play } from "lucide-react"
import { useState } from "react"

import { CopyButton, ResultTable } from "@/components/playground/shared"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api, errorMessage } from "@/lib/api"

const DEFAULT = `SELECT ?class ?label WHERE {
  ?class a owl:Class ; rdfs:label ?label .
}
LIMIT 20`

export function SparqlTab({ source }: { source: string }) {
  const examples = useQuery({ queryKey: ["sparql-examples"], queryFn: api.playground.sparqlExamples,
                              staleTime: Infinity })
  const [query, setQuery] = useState(DEFAULT)
  const [example, setExample] = useState("")
  const run = useMutation({ mutationFn: (q: string) => api.playground.sparql({ source, query: q, limit: 500 }) })
  const res = run.data

  return (
    <div className="grid gap-4" id="playground-sparql">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={example} onValueChange={(id) => {
          setExample(id)
          const ex = examples.data?.find((e) => e.id === id)
          if (ex) { setQuery(ex.query); run.mutate(ex.query) }
        }}>
          <SelectTrigger className="w-full sm:w-64" aria-label="예시 쿼리" id="sparql-example">
            <SelectValue placeholder="예시 쿼리 고르기" />
          </SelectTrigger>
          <SelectContent>
            {(examples.data ?? []).map((e) => (
              <SelectItem key={e.id} value={e.id} data-example={e.id}>{e.title}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {example && (
          <p className="text-xs text-muted-foreground">{examples.data?.find((e) => e.id === example)?.description}</p>
        )}
      </div>
      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); run.mutate(query) }}>
        <Textarea id="sparql-query" value={query} onChange={(e) => setQuery(e.target.value)} rows={10}
                  spellCheck={false} aria-label="SPARQL 쿼리"
                  className="font-mono text-xs leading-relaxed"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault()
                      run.mutate(query)
                    }
                  }} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            접두사 owl·rdfs·rdf·skos·prov·dcterms·xsd·os 는 미리 선언되어 있습니다. SELECT·ASK·CONSTRUCT·DESCRIBE만, 10초 제한. ⌘/Ctrl+Enter 실행.
          </p>
          <Button type="submit" id="sparql-run" disabled={run.isPending || !query.trim()}>
            {run.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />} 실행
          </Button>
        </div>
      </form>
      {run.error && (
        <p role="alert" id="sparql-error"
           className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 font-mono text-xs text-destructive break-words whitespace-pre-wrap">
          {errorMessage(run.error)}
        </p>
      )}
      {res && (
        <div className="grid gap-2" id="sparql-result" data-type={res.type}>
          <p className="text-xs text-muted-foreground">
            내보내기 <code>{res.export}</code> · 트리플 {res.triples.toLocaleString()}개 · 결과 {res.total.toLocaleString()}{res.truncated && "+"}
            {res.truncated && " (잘림)"}
          </p>
          {res.type === "ASK" ? (
            <Badge variant="outline" className="w-fit">{res.boolean ? "true" : "false"}</Badge>
          ) : res.turtle !== undefined ? (
            <div className="grid gap-2">
              <div className="flex justify-end"><CopyButton text={res.turtle} label="Turtle 복사" /></div>
              <pre className="max-h-[28rem] overflow-auto rounded-md border bg-muted p-3 text-xs">{res.turtle}</pre>
            </div>
          ) : (
            <ResultTable columns={res.columns} rows={res.rows} max={500} csvName="sparql-result.csv" />
          )}
        </div>
      )}
    </div>
  )
}
