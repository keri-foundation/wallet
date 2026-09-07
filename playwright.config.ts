import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
    testDir: './playwright',
    // The OOBI deterministic / restart / live suites are opt-in via their own
    // dedicated configs (playwright.oobi.config.ts, playwright.oobi-restart.
    // config.ts, playwright.live.config.ts). They need a built runtime on a
    // dedicated port (or live DigitalOcean), so they are excluded from the
    // default CI Playwright run.
    testIgnore: [
        '**/oobi-bridge.spec.ts',
        '**/oobi-restart.spec.ts',
        '**/live-hosted-onboarding.spec.ts',
    ],
    fullyParallel: false,
    workers: process.env['CI'] ? 1 : undefined,
    retries: process.env['CI'] ? 1 : 0,
    reporter: process.env['CI'] ? 'github' : 'list',
    webServer: {
        command: 'python3 scripts/serve_local.py --no-open --port 4173',
        url: 'http://127.0.0.1:4173/fortweb/app/',
        reuseExistingServer: !process.env['CI'],
        timeout: 60_000,
    },
    use: {
        baseURL: 'http://127.0.0.1:4173',
        trace: 'on-first-retry',
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],
});