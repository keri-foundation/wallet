import { expect, test, type Request } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { classifyRequestUrl, isAllowedLiveRemote } from './utils/proxy-guard';

// Globals exposed by the in-page driver (app/live-drive.js).
declare global {
    interface Window {
        __liveRun?: () => Promise<any>;
        __liveReopen?: (state: any) => Promise<any>;
    }
}

/**
 * REAL Fort Web live DigitalOcean hosted onboarding.
 *
 * Boots the actual Fort Web app + runtime (production worker config), drives the
 * product APIs against the live Kf Boot / witness / watcher, and proves:
 *   - bootstrap connected, watcher_required
 *   - a FRESH session provisions a fresh hosted V2 witness + watcher
 *   - witness registration + rotation receipt, watcher OOBI + introduction
 *   - account onboarding complete
 *   - the DIRECT watcher ksn query and services.overview domain model both
 *     HARD-GATE test success (not just printed)
 *   - account/witness/watcher state survives a fresh worker + vault reopen
 *     with /oobi/ refetch hard-blocked (reconstructed from IndexedDB)
 *   - all remote traffic on HTTPS :5633/:7633 / kopn0; no :5632/:7632, no
 *     /_fortweb_proxy/ (even same-origin), no CDN, no V1 downgrade
 */
const LIVE_BOOT = 'https://kopn0.keri.foundation';
const WIT_LOC = 'https://138.68.53.132:5633';
const WAT_LOC = 'https://138.68.53.132:7633';
const OVERALL_TIMEOUT_MS = 300_000;

function allowedHost(hostname: string): boolean {
    return hostname === '127.0.0.1' || hostname === 'localhost';
}

function isForbiddenRemote(request: Request): { forbidden: boolean; reason?: string } {
    const verdict = isAllowedLiveRemote(request.url());
    return verdict.allowed ? { forbidden: false } : { forbidden: true, reason: verdict.reason };
}

test('FortWeb live DigitalOcean V2 hosted onboarding (real BrowserClienter)', async ({ page }) => {
    test.setTimeout(OVERALL_TIMEOUT_MS + 120_000);

    const pageErrors: string[] = [];
    const consoleIssues: string[] = [];
    const remoteUrls: string[] = [];
    const forbiddenUrls: string[] = [];
    const forbiddenReasons: Record<string, number> = {};
    let reopenMode = false; // block ALL /oobi/ after onboarding closes
    let reopenOobiBlocked = 0;

    const captureIssue = (text: string) => {
        const lower = text.toLowerCase();
        if (
            lower.includes('cors policy') ||
            lower.includes('mixed content') ||
            lower.includes('err_cert') ||
            lower.includes('err_ssl') ||
            lower.includes('tls')
        ) {
            consoleIssues.push(text);
        }
    };

    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
        if (message.type() === 'error' || message.type() === 'warning') {
            captureIssue(message.text());
        }
    });
    // Dedicated-worker console (the real PyScript/Pyodide worker).
    page.on('worker', (worker) => {
        worker.on('console', (message) => {
            const text = message.text();
            if (text.includes('[worker]') || text.includes('transport') || text.includes('REPLY')) {
                // eslint-disable-next-line no-console
                console.log(`[live-worker] ${text}`);
            }
        });
    });
    page.on('requestfailed', (request) => {
        captureIssue(`requestfailed ${request.url()} ${request.failure()?.errorText ?? ''}`);
    });

    await page.route('**/*', async (route) => {
        const url = route.request().url();
        // Proxy-path check must precede the localhost allow shortcut: a
        // same-origin /_fortweb_proxy/ request is a proxy use, not a local asset.
        const klass = classifyRequestUrl(url);
        if (klass === 'proxy') {
            remoteUrls.push(url);
            forbiddenUrls.push(url);
            forbiddenReasons['public Fort Web proxy'] = (forbiddenReasons['public Fort Web proxy'] ?? 0) + 1;
            await route.abort();
            return;
        }
        const hostname = new URL(url).hostname;
        if (allowedHost(hostname)) {
            if (reopenMode && url.includes('/oobi/')) {
                reopenOobiBlocked += 1;
                await route.abort();
                return;
            }
            await route.continue();
            return;
        }
        const verdict = isForbiddenRemote(route.request());
        remoteUrls.push(url);
        if (verdict.forbidden) {
            forbiddenUrls.push(url);
            forbiddenReasons[verdict.reason ?? 'forbidden'] = (forbiddenReasons[verdict.reason ?? 'forbidden'] ?? 0) + 1;
            await route.abort();
            return;
        }
        await route.continue();
    });

    await page.goto('/fortweb/app/live-drive.html');
    await expect(page.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

    // RUNTIME_WORKER_SYNC: served worker must match source.
    const sourceWorker = readFileSync(new URL('../app/runtime/wallet-worker.py', import.meta.url));
    const sourceSha = createHash('sha256').update(sourceWorker).digest('hex');
    const servedResponse = await page.request.get('/fortweb/app/runtime/wallet-worker.py');
    expect(servedResponse.ok(), 'served wallet-worker.py should be reachable').toBeTruthy();
    const servedSha = createHash('sha256').update(await servedResponse.body()).digest('hex');
    expect(servedSha, 'served worker must match source (build:runtime sync)').toBe(sourceSha);

    // ---- Phase 1: real live onboarding ----
    const live = (await page.evaluate(() => window.__liveRun!())) as any;
    // eslint-disable-next-line no-console
    console.log(`[live] Phase1 ${JSON.stringify(live)}`);
    expect(live.ok, `live onboarding failed: ${JSON.stringify(live)}`).toBe(true);
    expect(live.accountStatus).toBe('onboarded');
    expect(live.accountAid).toBeTruthy();
    expect(live.witnessEid).toBeTruthy();
    expect(live.watcherEid).toBeTruthy();
    expect(live.witnessUrl).toBe(WIT_LOC);
    expect(live.watcherUrl).toBe(WAT_LOC);
    expect(live.boot.watcherRequired).toBe(true);
    expect(live.boot.connectionOk).toBe(true);

    // ---- HARD-GATE the DIRECT functional criteria (not just res.ok) ----
    expect(live.queryOk, `direct watcher query must pass: ${JSON.stringify(live.watcherQuery)}`).toBe(true);
    const wq = live.watcherQuery ?? {};
    expect(wq.replySaid, 'watcher query replySaid must be present').toBeTruthy();
    expect(wq.protocolMajor, 'watcher query must be KERI v2').toBe(2);
    expect(wq.controller, 'watcher query controller must be the account AID').toBe(live.accountAid);
    expect(wq.sn, 'watcher query must report controller sn=1').toBe('1');

    expect(live.overviewOk, `services.overview must be connected: ${JSON.stringify(live.services)}`).toBe(true);
    const svc = live.services ?? {};
    expect(svc.witness?.directStatus).toBe('connected');
    expect(svc.witness?.oobiVerified).toBe(true);
    expect(svc.witness?.registered).toBe(true);
    expect(svc.witness?.receiptVerified).toBe(true);
    expect(svc.watcher?.directStatus).toBe('connected');
    expect(svc.watcher?.oobiVerified).toBe(true);
    expect(svc.watcher?.introduced).toBe(true);
    expect(svc.watcher?.queryVerified).toBe(true);
    expect(Number(svc.watcher?.observedSn ?? 0)).toBeGreaterThanOrEqual(1);
    expect(svc.witness?.endpoint).toBe(WIT_LOC);
    expect(svc.watcher?.endpoint).toBe(WAT_LOC);

    // ---- Phase 2: fresh worker/relaunch persistence, /oobi/ hard-blocked ----
    reopenMode = true;
    const reopenState = {
        vaultId: live.vaultId,
        passcode: live.passcode,
        storageName: live.storageName,
        accountAid: live.accountAid,
        witnessEid: live.witnessEid,
        watcherEid: live.watcherEid,
    };
    const pageB = await page.context().newPage();
    pageB.on('console', (message) => {
        if (message.type() === 'error' || message.type() === 'warning') {
            captureIssue(message.text());
        }
    });
    pageB.on('worker', (worker) => {
        worker.on('console', (message) => {
            const text = message.text();
            if (text.includes('[worker]') || text.includes('transport') || text.includes('REPLY')) {
                // eslint-disable-next-line no-console
                console.log(`[live-workerB] ${text}`);
            }
        });
    });
    await pageB.route('**/*', async (route) => {
        const url = route.request().url();
        // Proxy-path check must precede the localhost allow shortcut (see above).
        const klass = classifyRequestUrl(url);
        if (klass === 'proxy') {
            remoteUrls.push(url);
            forbiddenUrls.push(url);
            forbiddenReasons['public Fort Web proxy'] = (forbiddenReasons['public Fort Web proxy'] ?? 0) + 1;
            await route.abort();
            return;
        }
        const hostname = new URL(url).hostname;
        if (allowedHost(hostname)) {
            if (url.includes('/oobi/')) {
                reopenOobiBlocked += 1;
                await route.abort();
                return;
            }
            await route.continue();
            return;
        }
        const verdict = isForbiddenRemote(route.request());
        remoteUrls.push(url);
        if (verdict.forbidden) {
            forbiddenUrls.push(url);
            forbiddenReasons[verdict.reason ?? 'forbidden'] = (forbiddenReasons[verdict.reason ?? 'forbidden'] ?? 0) + 1;
            await route.abort();
            return;
        }
        await route.continue();
    });
    await pageB.goto('/fortweb/app/live-drive.html');
    await expect(pageB.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

    const reopen = (await pageB.evaluate((s) => window.__liveReopen!(s), reopenState)) as any;
    // eslint-disable-next-line no-console
    console.log(`[live] Phase2 reopen ${JSON.stringify(reopen)}`);
    expect(reopen.ok, `live reopen persistence failed: ${JSON.stringify(reopen)}`).toBe(true);
    expect(reopen.accountIdentityPersisted, 'account identity must survive relaunch').toBe(true);
    expect(reopen.afterReopen.witness.kever_cached_in_memory).toBe(false);
    expect(reopen.afterReopen.witness.kever_reconstructed).toBe(true);
    expect(reopen.afterReopen.witness.kever_usable).toBe(true);
    expect(reopen.afterReopen.witness.loc_url).toBe(WIT_LOC);
    expect(reopen.afterReopen.watcher.kever_cached_in_memory).toBe(false);
    expect(reopen.afterReopen.watcher.kever_reconstructed).toBe(true);
    expect(reopen.afterReopen.watcher.kever_usable).toBe(true);
    expect(reopen.afterReopen.watcher.loc_url).toBe(WAT_LOC);
    expect(reopen.oobiCounts.coobi).toBe(0);

    // ---- Transport invariants ----
    const kopn0 = remoteUrls.filter((u) => new URL(u).hostname === 'kopn0.keri.foundation').length;
    const wit = remoteUrls.filter((u) => u.startsWith('https://138.68.53.132:5633')).length;
    const wat = remoteUrls.filter((u) => u.startsWith('https://138.68.53.132:7633')).length;
    const http5632 = remoteUrls.filter((u) => u.includes(':5632')).length;
    const http7632 = remoteUrls.filter((u) => u.includes(':7632')).length;
    const proxy = remoteUrls.filter((u) => u.includes('/_fortweb_proxy/')).length;
    const cdn = remoteUrls.filter((u) => u.includes('cdn.jsdelivr.net')).length;
    // eslint-disable-next-line no-console
    console.log(
        `[live] transport kopn0=${kopn0} wit5633=${wit} wat7633=${wat} http5632=${http5632} http7632=${http7632} proxy=${proxy} cdn=${cdn} reopenOobiBlocked=${reopenOobiBlocked}`,
    );

    expect(kopn0, 'Fort Web must reach live Kf Boot over HTTPS').toBeGreaterThan(0);
    expect(wit, 'Fort Web must reach the live witness over HTTPS :5633').toBeGreaterThan(0);
    expect(wat, 'Fort Web must reach the live watcher over HTTPS :7633').toBeGreaterThan(0);
    expect(http5632, `plaintext :5632 used: ${forbiddenUrls.join(', ')}`).toBe(0);
    expect(http7632, `plaintext :7632 used: ${forbiddenUrls.join(', ')}`).toBe(0);
    expect(proxy, 'public /_fortweb_proxy/ must not be used').toBe(0);
    expect(cdn, 'CDN must not be used').toBe(0);
    expect(reopenOobiBlocked, 'no OOBI refetch during reopen reconstruction').toBe(0);
    // eslint-disable-next-line no-console
    console.log(`[live] forbiddenReasons=${JSON.stringify(forbiddenReasons)}`);
    expect(forbiddenUrls, `forbidden remote requests: ${forbiddenUrls.join(', ')}`).toEqual([]);
    expect(consoleIssues, `live console/TLS/CORS/mixed-content issues: ${consoleIssues.join(' | ')}`).toEqual([]);
    expect(pageErrors, `live page errors: ${pageErrors.join(' | ')}`).toEqual([]);
});
