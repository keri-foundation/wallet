import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const applicationOrigin = new URL(process.env['FORTWEB_BASE_URL'] ?? '').origin;
const rejectedExternalRequests = new WeakMap<BrowserContext, string[]>();

test.beforeEach(async ({ context }) => {
    const rejected: string[] = [];
    rejectedExternalRequests.set(context, rejected);
    await context.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if ((url.protocol === 'http:' || url.protocol === 'https:') && url.origin !== applicationOrigin) {
            rejected.push(url.href);
            await route.abort('blockedbyclient');
            return;
        }
        await route.continue();
    });
});

test.afterEach(async ({ context }) => {
    const rejected = rejectedExternalRequests.get(context) ?? [];
    try {
        expect(rejected).toEqual([]);
        console.log('application-network-guard external=0');
    } finally {
        await context.unroute('**/*');
        rejectedExternalRequests.delete(context);
    }
});

function isKnownRuntimeNoise(text: string): boolean {
    return (
        text.includes('SyntaxWarning: invalid escape sequence') ||
        text.includes("b'(?P<kind2>") ||
        text.includes('MapDom is a subclass of IceMapDom') ||
        text.includes('RawDom is subclass of MapDom')
    );
}

function collectUnexpectedPageErrors(page: Page): string[] {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
        pageErrors.push(error.message);
    });
    return pageErrors;
}

function collectUnexpectedConsoleErrors(page: Page): string[] {
    const consoleErrors: string[] = [];
    page.on('console', (message) => {
        if (message.type() !== 'error') {
            return;
        }

        const text = message.text();
        if (text.includes('favicon.ico')) {
            return;
        }
        if (isKnownRuntimeNoise(text)) {
            return;
        }

        consoleErrors.push(text);
    });
    return consoleErrors;
}

async function expectNoUnexpectedErrors(page: Page, pageErrors: string[], consoleErrors: string[]): Promise<void> {
    await page.waitForTimeout(250);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
}

test.describe('FortWeb route acceptance', () => {
    test('fixture index route renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures');

        await expect(page.getByRole('heading', { name: 'Fixture Routes' })).toBeVisible();
        await expect(page.getByRole('link', { name: 'vaults/populated' })).toBeVisible();
        await expect(page.getByRole('link', { name: 'watchers/populated' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('identifiers empty fixture renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/identifiers/empty');

        await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
        await expect(page.getByRole('heading', { name: 'No Local Identifiers Yet' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('remotes empty fixture renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/remotes/empty');

        await expect(page.getByRole('heading', { name: 'Remote Identifiers', exact: true })).toBeVisible();
        await expect(page.getByRole('heading', { name: 'No Remote Identifiers Yet' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('settings fixture renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/settings');

        await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('witnesses disconnected fixture renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/witnesses/disconnected');

        await expect(page.getByRole('heading', { name: 'Witnesses', level: 1 })).toBeVisible();
        await expect(page.locator('dl').getByText('Disconnected')).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('watchers placeholder fixture renders correctly', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/watchers/placeholder');

        await expect(page.getByRole('heading', { name: 'Watchers', level: 1 })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('route not found renders correctly for invalid paths', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/this-route-should-not-exist');

        await expect(page.getByRole('heading', { name: 'Route Not Found' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });
});


for (const outcome of ['saved', 'missing', 'failed'] as const) {
    test(`vault deep route waits for the initial catalog (${outcome})`, async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        let releaseCatalog!: () => void;
        let catalogRequested!: () => void;
        const catalogGate = new Promise<void>((resolve) => { releaseCatalog = resolve; });
        const catalogStarted = new Promise<void>((resolve) => { catalogRequested = resolve; });

        await page.route('**/app/runtime/bridge.js', async (route) => {
            await route.fulfill({
                contentType: 'text/javascript',
                body: `
                    export function createRuntimeBridge() {
                        return {
                            async request(method) {
                                if (method !== 'vaults.list') throw new Error('Unexpected method: ' + method);
                                const response = await fetch('/__test_vaults');
                                if (!response.ok) throw new Error(await response.text());
                                return response.json();
                            },
                            destroy() {},
                        };
                    }
                `,
            });
        });
        await page.route('**/__test_vaults', async (route) => {
            catalogRequested();
            await catalogGate;
            await route.fulfill(outcome === 'failed' ? {
                status: 503,
                body: 'Unable to load vaults.',
            } : {
                json: {
                    vaults: outcome === 'saved' ? [{
                        id: 'cold-vault',
                        alias: 'Saved Vault',
                        createdAt: '2026-01-01T00:00:00Z',
                    }] : [],
                },
            });
        });

        try {
            await page.goto('/fortweb/app/index.html#/vaults/cold-vault/identifiers');
            await catalogStarted;
            await expect(page.getByRole('heading', { name: 'Route Not Found' })).toHaveCount(0);
            await expect(page.getByRole('status')).toHaveText('Loading vault...');
            await expect(page).toHaveURL(/#\/vaults\/cold-vault\/identifiers$/);

            if (outcome === 'saved') {
                await page.evaluate(() => { window.location.hash = '#/'; });
                await expect(page.locator('.home-splash')).toBeVisible();
                await page.evaluate(() => { window.location.hash = '#/_fixtures/settings'; });
                await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
                await page.evaluate(() => { window.location.hash = '#/vaults/cold-vault/identifiers'; });
                await expect(page.getByRole('status')).toHaveText('Loading vault...');
            }

            releaseCatalog();
            if (outcome === 'saved') {
                await expect(page.getByRole('heading', { name: 'Open Saved Vault' })).toBeVisible();
                await expect(page).toHaveURL(/#\/vaults\/cold-vault\/unlock$/);
            } else if (outcome === 'missing') {
                await expect(page.getByRole('heading', { name: 'Route Not Found' })).toBeVisible();
            } else {
                await expect(page.getByRole('heading', { name: 'Runtime Error' })).toBeVisible();
                await expect(page.getByText('Unable to load vaults.', { exact: true })).toBeVisible();
                await expect(page.getByRole('heading', { name: 'Route Not Found' })).toHaveCount(0);
            }
            expect(pageErrors).toEqual([]);
        } finally {
            releaseCatalog();
        }
    });
}
