/* Dev server (`pnpm dev`) proxy of /api and /v1 to the AutoOntology service.

   The service refuses cross-origin writes (Origin must match Host). Behind the proxy the
   page's Origin is the dev server's, so exactly that Origin is rewritten to the service's
   own; any other Origin (a foreign site posting to localhost:5173) is forwarded untouched
   and still refused by the service. Sec-Fetch-Site is never touched. */

/** The service the dev server proxies to: `SERVICE=http://127.0.0.1:7810 pnpm dev`. */
const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env
export const SERVICE = (env?.SERVICE || 'http://127.0.0.1:7800').replace(/\/+$/, '')
export const DEV_PORT = 5173

export function devOrigins(port: number = DEV_PORT): string[] {
  return [`http://localhost:${port}`, `http://127.0.0.1:${port}`, `http://[::1]:${port}`]
}

/** The Origin header to send upstream for a request that arrived with *origin*. */
export function proxiedOrigin(origin: string | undefined, port: number = DEV_PORT): string | undefined {
  if (origin === undefined) return undefined
  return devOrigins(port).includes(origin.toLowerCase()) ? SERVICE : origin
}

interface ProxyReq {
  getHeader(name: string): string | string[] | number | undefined
  setHeader(name: string, value: string): void
}

interface Proxy {
  on(event: 'proxyReq', handler: (req: ProxyReq) => void): void
}

export function rewriteDevOrigin(proxy: Proxy, port: number = DEV_PORT): void {
  proxy.on('proxyReq', (req) => {
    const origin = req.getHeader('origin')
    if (typeof origin !== 'string') return
    const next = proxiedOrigin(origin, port)
    if (next !== undefined && next !== origin) req.setHeader('origin', next)
  })
}
