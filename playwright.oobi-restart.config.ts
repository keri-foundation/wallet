import { defineConfig, devices } from '@playwright/test';

/**
 * Narrow config for the Phase 9 server-restart persistence test.
 *
 * Unlike playwright.oobi.config.ts this config deliberately declares NO
 * webServer: the Phase 9 test must own its serve_local child process directly
 * so it can stop Server A, prove the origin is gone, and start Server B on the
 * SAME origin/port — all while preserving the browser context's IndexedDB.
 *
 * There must be exactly ONE owner of port 4183 per run, so this spec runs in
 * its own config and is NOT matched by playwright.oobi.config.ts (whose
 * testMatch is /oobi-bridge\.spec\.ts/).
 */
const PORT = 4183;

export default defineConfig({
    testDir: './playwright',
    testMatch: /oobi-restart\.spec\.ts/,
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: 'line',
    timeout: 300_000,
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
