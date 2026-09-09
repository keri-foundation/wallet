import { defineConfig, devices } from '@playwright/test';

const applicationBaseURL = 'http://127.0.0.1:4173';
process.env['FORTWEB_BASE_URL'] ??= applicationBaseURL;

export default defineConfig({
    testDir: './playwright',
    outputDir: 'test-results/application',
    fullyParallel: false,
    workers: process.env['CI'] ? 1 : undefined,
    retries: process.env['CI'] ? 1 : 0,
    reporter: process.env['CI'] ? 'github' : 'list',
    webServer: {
        command: 'python3 scripts/serve_local.py --runtime-dir dist/runtime --no-open --port 4173',
        url: 'http://127.0.0.1:4173/fortweb/app/',
        reuseExistingServer: !process.env['CI'],
        timeout: 60_000,
    },
    use: {
        baseURL: applicationBaseURL,
        trace: 'on-first-retry',
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],
});
