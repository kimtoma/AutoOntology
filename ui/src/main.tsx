import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router"

import App from "@/App"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { keys } from "@/hooks/queries"
import { LangProvider } from "@/i18n"
import { isProjectStale } from "@/lib/api"
import "@/index.css"

/* A request that finds its project gone (renamed elsewhere: 404 unknown_project) or archived
   (409 project_archived) refreshes the project list: the route then redirects to the default
   project, or the archived banner appears. */
const onApiError = (err: unknown) => {
  if (isProjectStale(err)) void queryClient.invalidateQueries({ queryKey: keys.projects })
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onApiError }),
  mutationCache: new MutationCache({ onError: onApiError }),
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <LangProvider>
          <BrowserRouter basename="/ui">
            <TooltipProvider delayDuration={300}>
              <App />
              <Toaster richColors closeButton position="top-right" />
            </TooltipProvider>
          </BrowserRouter>
        </LangProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
