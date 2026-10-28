# AutoOntology web UI

Vite + React 19 + TypeScript + Tailwind CSS v4 + shadcn/ui. Built into
`../autoontology/web/static/dist/` (committed) and served by the AutoOntology service at `/ui/`.

```bash
pnpm install --frozen-lockfile
pnpm dev     # http://localhost:5173/ui/ — proxies /api and /v1 to the service on :7800
pnpm lint    # oxlint
pnpm test    # vitest (mocked fetch)
pnpm build   # tsc -b && vite build -> ../autoontology/web/static/dist/
```

`uv run autoontology tools build-ui` runs install + build from the repo root. Commit the
rebuilt `dist/` with every change here. All API calls go through `src/lib/api.ts`.
