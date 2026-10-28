import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { DEV_PORT, rewriteDevOrigin, SERVICE } from './dev-proxy.ts'

// three.js itself in a named chunk; only the lazy 3D canvas imports it (the rest of the graph
// stack is split automatically along the React.lazy boundaries, which keeps chunks acyclic)
const THREE = /[\\/]node_modules[\\/](three|three-spritetext)[\\/]/

const configure = (proxy: Parameters<typeof rewriteDevOrigin>[0]) => rewriteDevOrigin(proxy, DEV_PORT)

// Built into autoontology/web/static/dist/ (committed) and served by FastAPI at /ui/.
export default defineConfig({
  base: '/ui/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: [
      { find: '@', replacement: path.resolve(import.meta.dirname, './src') },
      // see src/lib/no-webgpu.ts
      { find: /^three\/webgpu$/, replacement: path.resolve(import.meta.dirname, './src/lib/no-webgpu.ts') },
    ],
  },
  build: {
    outDir: path.resolve(import.meta.dirname, '../autoontology/web/static/dist'),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1000,
    rolldownOptions: {
      output: {
        // three.js in its own chunk that only the lazy graph page loads. No wider groups:
        // a group that does not pull its dependencies in creates chunk cycles (the 2D chunk
        // then crashed with "Class extends value undefined"), one that does pulls react in.
        codeSplitting: {
          groups: [{ name: 'three', test: THREE }],
        },
      },
    },
  },
  server: {
    port: DEV_PORT,
    strictPort: true, // the Origin rewrite in dev-proxy.ts is tied to this port
    proxy: {
      '/api': { target: SERVICE, changeOrigin: true, configure },
      '/v1': { target: SERVICE, changeOrigin: true, configure },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
