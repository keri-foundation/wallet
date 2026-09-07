import { expect, test, type Page, type Request } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

// Globals set/read on the page by the in-page driver (app/oobi-drive.js).
declare global {
    interface Window {
        __oobiFixtureB64?: string;
        __oobiFixtureSha?: string;
        __oobiWatcherFixtureB64?: string;
        __oobiWatcherFixtureSha?: string;
        __oobiV1FixtureB64?: string;
        __oobiV1FixtureSha?: string;
        __oobiRun?: () => Promise<Record<string, unknown>>;
        __oobiP8WorkerA?: () => Promise<any>;
        __oobiP8WorkerB?: (state: any) => Promise<any>;
    }
}

/**
 * Real-worker OOBI no-network V2 regression (witness + watcher).
 *
 * Boots the REAL Fort Web worker via createRuntimeBridge with the test TOML,
 * creates+opens disposable vaults, and invokes the test-gated __test.oobi.parse
 * for the deterministic V2 witness AND watcher controller fixtures (L1 bare
 * Parser, L2 stock Oobiery parser, L3 stock processClients). Enforces an
 * external wall-clock result while allowing all localhost boot/runtime traffic.
 */
const FIXTURE_PATH = new URL('../app/fixtures/synth-witness-oobi.cesr', import.meta.url);
const WATCHER_FIXTURE_PATH = new URL('../app/fixtures/synth-watcher-oobi.cesr', import.meta.url);
const V1_FIXTURE_PATH = new URL('../app/fixtures/synth-v1json-oobi.cesr', import.meta.url);
const EXPECTED_FIXTURE_SHA = '48fcc323fc2f7956ecd81d2ced766dc09caecc25c9d79ebdc96bb17642a95453';
const EXPECTED_WATCHER_SHA = '6565f493cb65f21abc965dc5b8b5f065de5a1169b2f00453183b7633212f2e1a';
const OVERALL_TIMEOUT_MS = 300_000;

const ALLOW_PYODIDE_CDN = process.env.FORTWEB_ALLOW_PYODIDE_CDN === '1';

function isForbidden(request: Request): boolean {
    const url = request.url();
    if (ALLOW_PYODIDE_CDN && url.includes('cdn.jsdelivr.net/pyodide')) {
        return false;
    }
    return (
        url.includes('kopn0.keri.foundation') ||
        url.includes('138.68.53.132') ||
        url.includes('/_fortweb_proxy/')
    );
}

test('OOBI real-worker Level-1 parse (no network)', async ({ page }) => {
    test.setTimeout(OVERALL_TIMEOUT_MS + 30_000);

    const fixture = readFileSync(FIXTURE_PATH);
    const fixtureB64 = fixture.toString('base64');
    const watcherFixture = readFileSync(WATCHER_FIXTURE_PATH);
    const watcherB64 = watcherFixture.toString('base64');
    const v1Fixture = readFileSync(V1_FIXTURE_PATH);
    const v1B64 = v1Fixture.toString('base64');

    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    const workerMarkers: string[] = [];
    let externalRequests = 0;
    const externalUrls: string[] = [];

    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
        const text = message.text();
        if (message.type() === 'error') {
            consoleErrors.push(text);
        }
        if (text.includes('OOBI_L1_')) {
            workerMarkers.push(text);
            // eslint-disable-next-line no-console
            console.log(`[worker-marker] ${text}`);
        }
    });

    await page.route('**/*', async (route) => {
        const url = route.request().url();
        if (isForbidden(route.request())) {
            externalRequests += 1;
            externalUrls.push(url);
            await route.abort();
            return;
        }
        const hostname = new URL(url).hostname;
        const isBootCdn =
            hostname === 'cdn.jsdelivr.net' &&
            url.includes('pyodide');
        if (hostname !== '127.0.0.1' && hostname !== 'localhost' && !(ALLOW_PYODIDE_CDN && isBootCdn)) {
            externalRequests += 1;
            externalUrls.push(url);
            await route.abort();
            return;
        }
        await route.continue();
    });

    // Dedicated-worker console (the real PyScript/Pyodide Web Worker).
    page.on('worker', (worker) => {
        worker.on('console', (message) => {
            const text = message.text();
            if (text.includes('[worker]') || text.includes('OOBI_L1')) {
                // eslint-disable-next-line no-console
                console.log(`[dedicated-worker] ${text}`);
            }
        });
    });

    await page.goto('/fortweb/app/oobi-drive.html');
    await expect(page.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

    // RUNTIME_WORKER_SYNC: serve_local prefers the dist copy of the worker over
    // source app/. Assert the served wallet-worker.py matches current source so
    // a stale runtime can never silently invalidate the experiment.
    const sourceWorker = readFileSync(new URL('../app/runtime/wallet-worker.py', import.meta.url));
    const sourceSha = createHash('sha256').update(sourceWorker).digest('hex');
    const servedResponse = await page.request.get('/fortweb/app/runtime/wallet-worker.py');
    expect(servedResponse.ok(), 'served wallet-worker.py should be reachable').toBeTruthy();
    const servedSha = createHash('sha256').update(await servedResponse.body()).digest('hex');
    expect(servedSha, 'served worker must match source (build:runtime sync)').toBe(sourceSha);
    // eslint-disable-next-line no-console
    console.log(`[runtime-worker-sync] source=${sourceSha.slice(0, 12)} served=${servedSha.slice(0, 12)}`);

    // Inject fixtures + kick off the driver.
    const resultsPromise = page.evaluate(
        async ({ b64, sha, watcherB64, watcherSha, v1B64, v1Sha }) => {
            window.__oobiFixtureB64 = b64;
            window.__oobiFixtureSha = sha;
            window.__oobiWatcherFixtureB64 = watcherB64;
            window.__oobiWatcherFixtureSha = watcherSha;
            window.__oobiV1FixtureB64 = v1B64;
            window.__oobiV1FixtureSha = v1Sha;
            return window.__oobiRun!();
        },
        { b64: fixtureB64, sha: EXPECTED_FIXTURE_SHA, watcherB64, watcherSha: EXPECTED_WATCHER_SHA, v1B64, v1Sha: '6f6a86bb63ce5a15bcb2bced2020ea821cecd29a596a40b711efa38c9e5a0b61' },
    );

    // Poll the #result element (the driver writes JSON when finished).
    const started = Date.now();
    let resultText = '';
    while (Date.now() - started < OVERALL_TIMEOUT_MS) {
        const status = (await page.locator('#status').innerText().catch(() => '')).trim();
        if (status && !status.startsWith('IDLE')) {
            // eslint-disable-next-line no-console
            console.log(`[driver-status] ${status}`);
        }
        resultText = (await page.locator('#result').innerText().catch(() => '(none)')).trim();
        if (resultText !== '(none)' && resultText.startsWith('{')) {
            break;
        }
        await page.waitForTimeout(2_000);
    }

    const results = resultText.startsWith('{') ? JSON.parse(resultText) : { error: `no result; status=${await page.locator('#status').innerText().catch(() => '')}` };
    const elapsedMs = Date.now() - started;

    // eslint-disable-next-line no-console
    console.log(`[oobi-drive] elapsed=${elapsedMs}ms external=${externalRequests}`);
    // eslint-disable-next-line no-console
    console.log(`[oobi-drive] RESULT ${JSON.stringify(results, null, 2)}`);

    expect(externalRequests, `external/forbidden request attempted: ${externalUrls.join(', ')}`).toBe(0);
    expect(results.realWorkerBoot).toBe('PASS');

    // Permanent V2 regression matrix (no parser monkeypatch, no V1 fallback):
    //   witness: L1 bare V2 Parser, L2 stock Oobiery parser, L3 stock processClients
    //   watcher: L1/L2/L3 same (watcher is not equivalent to witness coverage).
    // Each result must carry a kever + HTTPS loc + controller role + V2 majors.
    const variants = results.variants ?? {};
    const l1ok = results.level1 === 'PASS';
    const l2 = variants.level2;
    const l3 = variants.level3;
    const l2ok = !!l2 && l2.result === 'PASS';
    const l3ok = !!l3 && l3.result === 'PASS';
    const w1 = variants.watcherL1;
    const w2 = variants.watcherL2;
    const w3 = variants.watcherL3;
    const w1ok = !!w1 && w1.result === 'PASS';
    const w2ok = !!w2 && w2.result === 'PASS';
    const w3ok = !!w3 && w3.result === 'PASS';
    // eslint-disable-next-line no-console
    console.log(
        `[oobi-drive] L1=${l1ok ? 'PASS' : results.level1} L2=${l2ok ? 'PASS' : JSON.stringify(l2)} L3=${l3ok ? 'PASS' : JSON.stringify(l3)}`,
    );
    // eslint-disable-next-line no-console
    console.log(
        `[oobi-drive] WAT L1=${w1ok ? 'PASS' : JSON.stringify(w1)} L2=${w2ok ? 'PASS' : JSON.stringify(w2)} L3=${w3ok ? 'PASS' : JSON.stringify(w3)}`,
    );
    // eslint-disable-next-line no-console
    console.log(`[oobi-drive] normalConfigRejectsTestMethod=${results.normalConfigRejectsTestMethod}`);

    const okV2 = (r: any, loc: string) =>
        !!r &&
        r.result === 'PASS' &&
        r.habery_version_major === 2 &&
        r.parser_version_major === 2 &&
        r.keystate === true &&
        r.loc_url === loc &&
        r.controller_role === true;

    const okL4 = (r: any, loc: string) =>
        !!r &&
        r.result === 'PASS' &&
        r.l4 === 'real_browserclienter' &&
        r.http_complete === true &&
        r.processclients_returned === true &&
        r.roobi_state === 'resolved' &&
        r.keystate === true &&
        r.keystore_size >= 1 &&
        r.loc_url === loc &&
        r.controller_role === true;

    const witnessOk =
        l1ok &&
        okV2(l2, 'https://138.68.53.132:5633') &&
        okV2(l3, 'https://138.68.53.132:5633');
    const watcherOk =
        okV2(w1, 'https://138.68.53.132:7633') &&
        okV2(w2, 'https://138.68.53.132:7633') &&
        okV2(w3, 'https://138.68.53.132:7633');
    const l4 = variants.level4;
    const w4 = variants.watcherL4;
    const witnessL4Ok = okL4(l4, 'https://138.68.53.132:5633');
    const watcherL4Ok = okL4(w4, 'https://138.68.53.132:7633');
    // eslint-disable-next-line no-console
    console.log(
        `[oobi-drive] WIT L4=${witnessL4Ok ? 'PASS' : JSON.stringify(l4)} WAT L4=${watcherL4Ok ? 'PASS' : JSON.stringify(w4)}`,
    );

    // V1-unsupported policy: genuine V1 hosted stream must be rejected fast by the
    // V2-only boundary guard (never accepted, never resolved, bounded duration).
    const v1 = variants.v1policy;
    const v1Ok =
        !!v1 &&
        v1.result === 'PASS' &&
        v1.policy === 'reject_v1' &&
        v1.accepted === false &&
        v1.error_code === 'UNSUPPORTED_KERI_VERSION' &&
        typeof v1.duration_ms === 'number' &&
        v1.duration_ms < 5000;
    // eslint-disable-next-line no-console
    console.log(`[oobi-drive] V1_POLICY=${v1Ok ? 'PASS' : JSON.stringify(v1)}`);

    if (
        witnessOk &&
        watcherOk &&
        witnessL4Ok &&
        watcherL4Ok &&
        v1Ok &&
        results.normalConfigRejectsTestMethod === 'YES (rejected by allowlist)'
    ) {
        // eslint-disable-next-line no-console
        console.log(
            `[oobi-drive] V2_MATRIX=PASS witnessL4=${JSON.stringify(l4)} watcherL4=${JSON.stringify(w4)} v1=${JSON.stringify(v1)}`,
        );
        expect(pageErrors).toEqual([]);
        return;
    }

    const summary = {
        level1: results.level1,
        l2,
        l3,
        l4,
        w1,
        w2,
        w3,
        w4,
        v1,
        error: results.error,
        workerMarkers,
        realWorkerBoot: results.realWorkerBoot,
        normalConfigRejectsTestMethod: results.normalConfigRejectsTestMethod,
        vaultCreated: results.vaultCreated,
        vaultOpened: results.vaultOpened,
        elapsedMs,
        externalRequests,
        consoleErrors,
        pageErrors,
    };
    expect(`matrix: ${JSON.stringify(summary)}`).toBe('matrix: PASS');
});

/**
 * Phase 8 — reopen persistence hard gate (no OOBI refetch).
 *
 * Worker A resolves witness + watcher through the REAL BrowserClienter in one
 * dedicated vault, proves persisted material, closes via the product
 * vaults.close (Habery.aclose) path, and is destroyed completely.
 *
 * Worker B then reopens the SAME vault in a COMPLETELY FRESH worker with every
 * /oobi/ request hard-blocked, and must reconstruct witness/watcher kevers +
 * HTTPS locs + endpoint roles from persisted browser KERI records (read-through
 * on db.kevers from db.states), proving the state survived the worker lifecycle
 * without any network refetch.
 */
test('OOBI Phase 8 reopen persistence (no OOBI refetch)', async ({ page }) => {
    test.setTimeout(OVERALL_TIMEOUT_MS + 90_000);

    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    let externalRequests = 0;
    const externalUrls: string[] = [];
    let blockedOobi = 0;
    const blockedOobiUrls: string[] = [];
    let reopenMode = false; // flipped after Worker A is destroyed

    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
        if (message.type() === 'error') {
            consoleErrors.push(message.text());
        }
    });

    await page.route('**/*', async (route) => {
        const url = route.request().url();
        if (isForbidden(route.request())) {
            externalRequests += 1;
            externalUrls.push(url);
            await route.abort();
            return;
        }
        const hostname = new URL(url).hostname;
        const isBootCdn =
            hostname === 'cdn.jsdelivr.net' &&
            url.includes('pyodide');
        if (hostname !== '127.0.0.1' && hostname !== 'localhost' && !(ALLOW_PYODIDE_CDN && isBootCdn)) {
            externalRequests += 1;
            externalUrls.push(url);
            await route.abort();
            return;
        }
        // Reopen phase: NO /oobi/ at all (not even the localhost fixture).
        if (reopenMode && url.includes('/oobi/')) {
            blockedOobi += 1;
            blockedOobiUrls.push(url);
            await route.abort();
            return;
        }
        await route.continue();
    });

    await page.goto('/fortweb/app/oobi-drive.html');
    await expect(page.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

    // RUNTIME_WORKER_SYNC: served wallet-worker.py must match current source.
    const sourceWorker = readFileSync(new URL('../app/runtime/wallet-worker.py', import.meta.url));
    const sourceSha = createHash('sha256').update(sourceWorker).digest('hex');
    const servedResponse = await page.request.get('/fortweb/app/runtime/wallet-worker.py');
    expect(servedResponse.ok(), 'served wallet-worker.py should be reachable').toBeTruthy();
    const servedSha = createHash('sha256').update(await servedResponse.body()).digest('hex');
    expect(servedSha, 'served worker must match source (build:runtime sync)').toBe(sourceSha);

    // ---- Worker A: create/open vault, resolve witness+watcher L4, close, destroy ----
    const workerA = (await page.evaluate(() => window.__oobiP8WorkerA!())) as any;
    // eslint-disable-next-line no-console
    console.log(`[phase8] WorkerA ${JSON.stringify(workerA)}`);
    expect(workerA.ok, `P8 Worker A failed: ${JSON.stringify(workerA)}`).toBe(true);
    expect(externalRequests, `external/forbidden during Worker A: ${externalUrls.join(', ')}`).toBe(0);

    const beforeW = workerA.beforeClose.witness;
    const beforeWat = workerA.beforeClose.watcher;
    // Persisted material (KEL/state/loc/role) present before close.
    expect(beforeW.persisted_kel).toBe(true);
    expect(beforeW.persisted_state).toBe(true);
    expect(beforeW.loc_url).toBe('https://138.68.53.132:5633');
    expect(beforeW.controller_role).toBe(true);
    expect(beforeWat.persisted_kel).toBe(true);
    expect(beforeWat.persisted_state).toBe(true);
    expect(beforeWat.loc_url).toBe('https://138.68.53.132:7633');
    expect(beforeWat.controller_role).toBe(true);
    // In Worker A these kevers were just resolved, so they are cached in memory.
    expect(beforeW.kever_cached_in_memory).toBe(true);
    expect(beforeWat.kever_cached_in_memory).toBe(true);
    // Product close proof: aclose returned and baser/keeper marked closed
    // (guaranteed-flush close path => IndexedDB writes committed).
    expect(workerA.lastClose).toBeTruthy();
    expect(workerA.lastClose.returned).toBe(true);
    expect(workerA.lastClose.baser_opened).toBe(false);
    expect(workerA.lastClose.keeper_opened).toBe(false);
    expect(workerA.workerATerminated).toBe(true);

    // ---- Flip to reopen mode: /oobi/ now hard-blocked ----
    reopenMode = true;

    // ---- Worker B: fresh worker, reopen SAME vault, NO OOBI network ----
    const workerBState = {
        vaultId: workerA.vaultId,
        accountAlias: workerA.accountAlias,
        accountAid: workerA.accountAid,
        witness: { aid: workerA.witness.aid },
        watcher: { aid: workerA.watcher.aid },
        witnessUrl: workerA.witnessUrl,
        watcherUrl: workerA.watcherUrl,
    };
    const workerB = (await page.evaluate((s) => window.__oobiP8WorkerB!(s), workerBState)) as any;
    // eslint-disable-next-line no-console
    console.log(`[phase8] WorkerB ${JSON.stringify(workerB)}`);
    expect(externalRequests, `external/forbidden during reopen: ${externalUrls.join(', ')}`).toBe(0);
    expect(blockedOobi, `OOBI refetch during reopen: ${blockedOobiUrls.join(', ')}`).toBe(0);
    expect(workerB.ok, `P8 Worker B failed: ${JSON.stringify(workerB)}`).toBe(true);
    expect(workerB.accountIdentityPersisted, 'account identity must persist across worker teardown').toBe(true);

    const afterW = workerB.afterReopen.witness;
    const afterWat = workerB.afterReopen.watcher;
    // Fresh worker => NOT cached in memory; reconstructed via persisted state.
    expect(afterW.kever_cached_in_memory, 'witness kever must NOT be a leftover in-memory cache').toBe(false);
    expect(afterW.kever_reconstructed, 'witness kever must reconstruct from persisted state').toBe(true);
    expect(afterW.kever_usable, 'witness kever must be usable').toBe(true);
    expect(afterW.persisted_kel).toBe(true);
    expect(afterW.persisted_state).toBe(true);
    expect(afterW.loc_url).toBe('https://138.68.53.132:5633');
    expect(afterW.controller_role).toBe(true);
    expect(afterWat.kever_cached_in_memory).toBe(false);
    expect(afterWat.kever_reconstructed, 'watcher kever must reconstruct from persisted state').toBe(true);
    expect(afterWat.kever_usable, 'watcher kever must be usable').toBe(true);
    expect(afterWat.persisted_kel).toBe(true);
    expect(afterWat.persisted_state).toBe(true);
    expect(afterWat.loc_url).toBe('https://138.68.53.132:7633');
    expect(afterWat.controller_role).toBe(true);
    // No new OOBI resolution started (no pending coobi).
    expect(workerB.oobiCounts.coobi).toBe(0);

    // eslint-disable-next-line no-console
    console.log(
        `[phase8] PASS witness=${JSON.stringify(afterW)} watcher=${JSON.stringify(afterWat)} ` +
        `account=${workerB.accountIdentityPersisted} blockedOobi=${blockedOobi} external=${externalRequests}`,
    );
    expect(pageErrors).toEqual([]);
});
