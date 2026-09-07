import { expect, test } from '@playwright/test';
import { collectUnexpectedPageErrors, collectUnexpectedConsoleErrors, expectNoUnexpectedErrors } from './utils/error-collector.js';

/**
 * Regression for the Direct Verified Service card endpoint rendering.
 *
 * The card must render the service endpoint (e.g. https://host:5633) when
 * available, falling back to EID only when no endpoint exists. A prior
 * operator-precedence bug caused a truthy endpoint to render an em dash ("—")
 * in its place.
 */
test.describe('FortWeb Direct Verified Service card', () => {
    test('renders witness and watcher HTTPS endpoints (not em dashes)', async ({ page }) => {
        const pageErrors = collectUnexpectedPageErrors(page);
        const consoleErrors = collectUnexpectedConsoleErrors(page);

        await page.goto('/fortweb/app/#/_fixtures/witnesses/direct-connected');

        await expect(page.getByRole('heading', { name: 'Direct Verified Service' })).toBeVisible();
        // Witness endpoint must be the HTTPS :5633 URL, not an em dash.
        const witnessEndpoint = page.locator('dt', { hasText: 'Witness Endpoint' }).locator('xpath=following-sibling::dd[1]');
        await expect(witnessEndpoint).toHaveText('https://138.68.53.132:5633');
        await expect(witnessEndpoint).not.toHaveText('\u2014');
        // Watcher endpoint must be the HTTPS :7633 URL, not an em dash.
        const watcherEndpoint = page.locator('dt', { hasText: 'Watcher Endpoint' }).locator('xpath=following-sibling::dd[1]');
        await expect(watcherEndpoint).toHaveText('https://138.68.53.132:7633');
        await expect(watcherEndpoint).not.toHaveText('\u2014');

        // Both services show Connected.
        await expect(page.getByText('Witness Direct Status')).toBeVisible();
        await expect(page.locator('dd', { hasText: 'Connected' }).first()).toBeVisible();

        await expectNoUnexpectedErrors(page, pageErrors, consoleErrors);
    });
});
