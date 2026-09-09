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
        /^\/lib\/python3\.14\/site-packages\/hio\/help\/doming\.py:(364|634): SyntaxWarning:/.test(text) ||
        text === '  _update(\\*pa, \\*\\*kwa): update attributes using dict like update syntax' ||
        text.startsWith("/lib/python3.14/site-packages/keri/core/parsing.py:651: SyntaxWarning: 'break' in a 'finally' block") ||
        text === '  break' ||
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

test.describe('FortWeb smoke', () => {
    test('app boot renders the vault landing page', async ({ page }) => {
        test.setTimeout(300_000);
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);
        const nativeMessages: Array<{ type?: string; message?: string }> = [];
        await page.addInitScript(() => {
            const messages: Array<{ type?: string; message?: string }> = [];
            Object.defineProperty(window, '__fortwebNativeMessages', {
                configurable: false,
                value: messages,
                writable: false,
            });
            window.webkit = {
                messageHandlers: {
                    bridge: {
                        postMessage(payload: { type?: string; message?: string }) {
                            messages.push(structuredClone(payload));
                        },
                    },
                },
            };
        });

        const runtimeConfig = await page.request.get('/fortweb/pyscript-ci.toml');
        expect(runtimeConfig.status()).toBe(200);
        expect(await runtimeConfig.text()).toMatch(/^sha256\s*=\s*"[0-9a-f]{64}"$/m);

        await page.goto('/fortweb/app/index.html');

        await expect(page.locator('#app-root')).toBeAttached();
        await expect(page.locator('.topbar__brand-link')).toBeVisible();
        await expect(page.locator('.topbar__title')).toHaveText('FortWeb');
        await expect(page.getByRole('heading', { name: 'FortWeb', exact: true })).toBeAttached();
        await expect(page.locator('.home-splash')).toBeVisible();
        await expect(page.locator('.shell-tabbar')).toHaveCount(0);
        await expect(page.getByText('Browser Wallet')).toHaveCount(0);
        await expect(page.getByText('Create your first vault to begin using the mobile wallet.')).toHaveCount(0);
        await expect(page.getByRole('heading', { name: 'Your Vaults' })).toHaveCount(0);
        await expect(page).toHaveTitle(/FortWeb \| FortWeb/);

        await page.waitForFunction(() => (
            (window as typeof window & { __fortwebNativeMessages?: Array<{ message?: string }> })
                .__fortwebNativeMessages?.some(({ message }) => message?.includes('event=worker_preload_complete'))
        ));
        await page.getByRole('button', { name: 'Vaults' }).click();
        await page.getByText('Initialize New Vault').click();
        const vaultName = `locked-settings-${Date.now()}`;
        await page.locator('[data-create-vault-form] input[name="name"]').fill(vaultName);
        await page.locator('[data-dialog-submit]').click();
        await expect(page.getByRole('heading', { name: `Open ${vaultName}` })).toBeVisible();
        const unlockHash = await page.evaluate(() => window.location.hash);
        const vaultId = decodeURIComponent(unlockHash.match(/^#\/vaults\/([^/]+)\/unlock$/)?.[1] ?? '');
        expect(vaultId).toBeTruthy();
        await page.getByRole('button', { name: 'Open', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
        await page.getByRole('button', { name: 'Lock vault' }).click();
        await expect(page.getByRole('heading', { name: `Open ${vaultName}` })).toBeVisible();

        await page.getByRole('button', { name: 'Vaults', exact: true }).click();
        const drawer = page.getByRole('dialog', { name: 'Vault switcher' });
        await drawer.getByRole('button', { name: 'Initialize New Vault' }).click();
        await expect(page.locator('[data-create-vault-form]')).toBeVisible();
        await page.getByRole('button', { name: 'Cancel', exact: true }).click();
        await page.getByRole('button', { name: 'Vaults', exact: true }).click();
        await drawer.getByRole('button', { name: new RegExp(vaultName) }).click();
        await expect(drawer).not.toBeVisible();
        await expect(page.getByRole('heading', { name: `Open ${vaultName}` })).toBeVisible();

        await page.evaluate(() => {
            const scope = window as typeof window & { __fortwebNativeMessages?: Array<{ type?: string; message?: string }> };
            scope.__fortwebNativeMessages?.splice(0);
        });
        await page.evaluate((lockedVaultId) => {
            window.location.hash = `#/vaults/${encodeURIComponent(lockedVaultId)}/settings`;
        }, vaultId);
        await expect(page).toHaveURL(new RegExp(`#\/vaults\/${encodeURIComponent(vaultId)}\/unlock$`));
        await expect(page.getByRole('heading', { name: `Open ${vaultName}` })).toBeVisible();
        nativeMessages.push(...await page.evaluate(() => (
            (window as typeof window & { __fortwebNativeMessages?: Array<{ type?: string; message?: string }> })
                .__fortwebNativeMessages ?? []
        )));
        expect(nativeMessages.some(({ message }) => (
            message?.includes('event=request_start') && message.includes('method="settings.get"')
        ))).toBe(false);

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('fixture index route lists deterministic fixture pages', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures');

        await expect(page.getByRole('heading', { name: 'Fixture Routes' })).toBeVisible();
        await expect(page.getByRole('link', { name: 'vaults/populated' })).toBeVisible();
        await expect(page.getByRole('link', { name: 'watchers/populated' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('identifiers fixture renders populated table state', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/identifiers/populated');

        await expect(page).toHaveTitle(/Identifiers \| FortWeb/);
        await expect(page.getByText('Local Identifiers')).toBeVisible();
        await expect(page.getByRole('link', { name: 'primary-aid' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });

    test('witness fixture renders hosted witness account state', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/index.html#/_fixtures/witnesses/account');

        await expect(page).toHaveTitle(/KERI Foundation Witnesses \| FortWeb/);
        await expect(page.getByText('Hosted Witnesses')).toBeVisible();
        await expect(page.getByRole('cell', { name: 'KF Witness wan-0' })).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });
});
