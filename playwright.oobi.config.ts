import { defineConfig, devices } from '@playwright/test';

/**
 * Dedicated config for the OOBI no-network reproducer.
 *
 * The shared playwright.config.ts reuses an existing server on :4173, which in
 * this environment is an unrelated VS Code process — not serve_local.py. This
 * config always starts its own serve_local on a dedicated port (4183) and only
 * runs the oobi-repro spec.
 */
const PORT = 4183;

export default defineConfig({
    testDir: './playwright',
    testMatch: /oobi-bridge\.spec\.ts/,
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: 'line',
    timeout: 240_000,
    webServer: {
        // Build the runtime FIRST so serve_local serves the current worker.
        // serve_local prefers dist/runtime/app/runtime/*.py over source app/;
        // without the build, edited wallet-worker.py/vaulting.py are silently
        // shadowed by a stale dist copy (see RUNTIME_WORKER_SYNC check in the
        // oobi-bridge spec).
        command: `npm run build:runtime && python3 scripts/serve_local.py --no-open --port ${PORT}`,
        url: `http://127.0.0.1:${PORT}/fortweb/app/`,
        reuseExistingServer: false,
        timeout: 120_000,
    },
    use: {
        baseURL: `http://127.0.0.1:${PORT}`,
        trace: 'retain-on-failure',
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],
});
