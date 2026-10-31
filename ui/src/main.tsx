import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router"

import App from "@/App"
import { IdentityGate } from "@/components/identity-gate"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { LangProvider } from "@/i18n"
import "@/index.css"

const queryClient = new QueryClient({
  queryCache: new QueryCache(),
  mutationCache: new MutationCache(),
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
              <IdentityGate><App /></IdentityGate>
              <Toaster richColors closeButton position="top-right" />
            </TooltipProvider>
          </BrowserRouter>
        </LangProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
