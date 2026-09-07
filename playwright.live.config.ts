import { defineConfig, devices } from '@playwright/test';

/**
 * Dedicated config for the REAL Fort Web live DigitalOcean onboarding driver.
 *
 * Serves the actual Fort Web app + built runtime on 4183 (like the OOBI suite)
 * but the spec intentionally connects out to the public hosted services:
 *
 *   https://kopn0.keri.foundation
 *   https://138.68.53.132:5633   (witness TLS)
 *   https://138.68.53.132:7633   (watcher TLS)
 *
 * Opt-in only — excluded from the default CI Playwright run.
 */
const PORT = 4183;

export default defineConfig({
    testDir: './playwright',
    testMatch: /live-hosted-onboarding\.spec\.ts/,
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: 'line',
    timeout: 360_000,
    webServer: {
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
