import { expect, test } from '@playwright/test';
import { spawn, execSync, type ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Globals exposed by the in-page driver (app/oobi-drive.js) — the SAME Phase 8
// Worker A / Worker B entry points are reused here verbatim. Worker A creates a
// unique disposable vault, resolves witness + watcher through the real
// BrowserClienter, product-closes the vault (Habery.aclose) and destroys the
// worker. Worker B reopens the EXISTING vault in a completely fresh worker and
// proves witness/watcher kevers + loc + role reconstruct with zero OOBI refetch.
declare global {
    interface Window {
        __oobiP8WorkerA?: () => Promise<any>;
        __oobiP8WorkerB?: (state: any) => Promise<any>;
    }
}

// Resolve the fortweb repo root from this spec's own location (repo-portable;
// never a machine-specific absolute path).
const ROOT = fileURLToPath(new URL('..', import.meta.url));
const PORT = 4183;
const BASE = `http://127.0.0.1:${PORT}`;
const APP = `${BASE}/fortweb/app/`;

async function sha256Buffer(body: Buffer): Promise<string> {
    return createHash('sha256').update(body).digest('hex');
}

function startServer(): ChildProcess {
    // The test owns this PID directly. stdio piped so we never inherit a
    // terminal; logs are only read on failure.
    return spawn('python3', ['scripts/serve_local.py', '--no-open', '--port', String(PORT)], {
        cwd: ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });
}

function stopServer(child: ChildProcess): Promise<{ code: number | null; signal: NodeJS.Signals | null }> {
    return new Promise((resolve) => {
        let settled = false;
        const done = (code: number | null, signal: NodeJS.Signals | null) => {
            if (!settled) {
                settled = true;
                resolve({ code, signal });
            }
        };
        child.once('exit', (code, signal) => done(code, signal));
        child.once('error', (err) => {
            // eslint-disable-next-line no-console
            console.log(`[phase9] server error: ${err.message}`);
            done(null, null);
        });
        // SIGTERM first, bounded wait, SIGKILL fallback.
        child.kill('SIGTERM');
        setTimeout(() => {
            if (!settled && child.exitCode === null && !child.killed) {
                child.kill('SIGKILL');
            }
            // If still not settled shortly after SIGKILL, resolve with null.
            setTimeout(() => done(child.exitCode, child.signalCode), 1_000);
        }, 4_000);
    });
}

interface ServerReadiness {
    ready: boolean;
    workerSha: string;
}

async function waitForServerReadiness(timeoutMs = 90_000): Promise<ServerReadiness> {
    const started = Date.now();
    let lastError = '';
    while (Date.now() - started < timeoutMs) {
        try {
            const appResponse = await fetch(APP);
            if (appResponse.ok) {
                const workerResponse = await fetch(`${BASE}/fortweb/app/runtime/wallet-worker.py`);
                if (workerResponse.ok) {
                    const body = Buffer.from(await workerResponse.arrayBuffer());
                    return { ready: true, workerSha: await sha256Buffer(body) };
                }
                lastError = `worker asset ${workerResponse.status}`;
            } else {
                lastError = `app asset ${appResponse.status}`;
            }
        } catch (err) {
            lastError = String((err as Error).message);
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`server not ready after ${timeoutMs}ms: ${lastError}`);
}

async function assertOriginUnavailable(timeoutMs = 30_000): Promise<boolean> {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
        try {
            const response = await fetch(APP);
            if (response.ok) {
                return false; // still serving — restart not proven
            }
        } catch {
            return true; // connection refused → origin down
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
    }
    return false;
}

/**
 * Phase 9 — full HTTP-server-restart persistence (no OOBI refetch).
 *
 * Server process A dies completely; a NEW server process B starts on the same
 * origin/port; a NEW page in the SAME browser context (same IndexedDB) boots a
 * NEW PyWorker and reopens the EXISTING vault. Account + witness + watcher V2
 * state must remain usable with zero OOBI / external / CDN traffic.
 */
test('OOBI Phase 9 server-restart persistence (no OOBI refetch)', async ({ page }) => {
    test.setTimeout(360_000);

    // Build ONCE before Server A. Server B must serve the SAME built artifact
    // (no rebuild between A and B → this proves restart persistence, not a
    // version migration).
    execSync('npm run build:runtime', { cwd: ROOT, stdio: 'inherit' });

    const sourceWorker = readFileSync(`${ROOT}/app/runtime/wallet-worker.py`);
    const sourceSha = await sha256Buffer(sourceWorker);

    let serverA: ChildProcess | null = null;
    let serverB: ChildProcess | null = null;
    const externalUrls: string[] = [];
    let externalRequests = 0;
    let postRestartOobi = 0;
    const pageErrors: string[] = [];

    try {
        // ============ SERVER A ============
        serverA = startServer();
        const pidA = serverA.pid as number;
        const readyA = await waitForServerReadiness();
        const shaA = readyA.workerSha;
        expect(shaA, 'Server A served worker must match built source').toBe(sourceSha);
        // eslint-disable-next-line no-console
        console.log(`[phase9] SERVER_A pid=${pidA} ready=true workerSha=${shaA.slice(0, 12)}`);

        // Page A in the SAME context we keep for the whole test. Worker A phase
        // may use the localhost /oobi/ fixture endpoint but no external traffic.
        page.on('pageerror', (error) => pageErrors.push(error.message));
        await page.route('**/*', async (route) => {
            const url = route.request().url();
            const hostname = new URL(url).hostname;
            const isBootCdn = hostname === 'cdn.jsdelivr.net' && url.includes('pyodide');
            if (hostname !== '127.0.0.1' && hostname !== 'localhost' && !isBootCdn) {
                externalRequests += 1;
                externalUrls.push(url);
                await route.abort();
                return;
            }
            await route.continue();
        });

        await page.goto('/fortweb/app/oobi-drive.html');
        await expect(page.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

        // Worker A: unique disposable vault + V2 account + witness/watcher L4
        // resolve + product vaults.close + worker destroy (same proven Phase 8 path).
        const workerA = (await page.evaluate(() => window.__oobiP8WorkerA!())) as any;
        // eslint-disable-next-line no-console
        console.log(`[phase9] WorkerA ${JSON.stringify(workerA)}`);
        expect(workerA.ok, `Worker A failed: ${JSON.stringify(workerA)}`).toBe(true);
        expect(workerA.lastClose).toBeTruthy();
        expect(workerA.lastClose.returned).toBe(true);
        expect(workerA.lastClose.baser_opened).toBe(false);
        expect(workerA.lastClose.keeper_opened).toBe(false);
        expect(workerA.workerATerminated).toBe(true);
        const beforeW = workerA.beforeClose.witness;
        const beforeWat = workerA.beforeClose.watcher;
        expect(beforeW.persisted_kel).toBe(true);
        expect(beforeW.loc_url).toBe('https://138.68.53.132:5633');
        expect(beforeWat.persisted_kel).toBe(true);
        expect(beforeWat.loc_url).toBe('https://138.68.53.132:7633');
        expect(externalRequests, `external during Worker A: ${externalUrls.join(', ')}`).toBe(0);

        // ============ STOP SERVER A ============
        const serverAExit = await stopServer(serverA);
        serverA = null;
        // eslint-disable-next-line no-console
        console.log(`[phase9] SERVER_A exited code=${serverAExit.code} signal=${serverAExit.signal}`);
        expect(serverAExit.signal === 'SIGTERM' || serverAExit.code !== null, 'Server A must exit').toBe(true);
        const unavailable = await assertOriginUnavailable();
        expect(unavailable, 'origin must be unavailable between servers').toBe(true);
        // eslint-disable-next-line no-console
        console.log(`[phase9] ORIGIN_UNAVAILABLE_BETWEEN_SERVERS=true`);

        // ============ SERVER B (same origin/port, new OS process) ============
        serverB = startServer();
        const pidB = serverB.pid as number;
        expect(pidB, 'Server B must be a different OS process').not.toBe(pidA);
        const readyB = await waitForServerReadiness();
        const shaB = readyB.workerSha;
        expect(shaB, 'Server B served worker must equal Server A (no rebuild between)').toBe(shaA);
        // eslint-disable-next-line no-console
        console.log(`[phase9] SERVER_B pid=${pidB} ready=true workerSha=${shaB.slice(0, 12)} sameArtifact=${shaA === shaB}`);

        // ============ NEW PAGE in the SAME context, OOBI hard-blocked ============
        const pageB = await page.context().newPage();
        pageB.on('pageerror', (error) => pageErrors.push(error.message));
        await pageB.route('**/*', async (route) => {
            const url = route.request().url();
            const hostname = new URL(url).hostname;
            const isBootCdn = hostname === 'cdn.jsdelivr.net' && url.includes('pyodide');
            if (hostname !== '127.0.0.1' && hostname !== 'localhost' && !isBootCdn) {
                externalRequests += 1;
                externalUrls.push(url);
                await route.abort();
                return;
            }
            if (url.includes('/oobi/')) {
                postRestartOobi += 1;
                await route.abort();
                return;
            }
            await route.continue();
        });

        await pageB.goto('/fortweb/app/oobi-drive.html');
        await expect(pageB.locator('#status')).toHaveText('IDLE', { timeout: 10_000 });

        // Worker B: open EXISTING vault only, reconstruct witness/watcher with
        // zero /oobi/ (reusing the Phase 8 reopen entry point).
        const workerBState = {
            vaultId: workerA.vaultId,
            accountAlias: workerA.accountAlias,
            accountAid: workerA.accountAid,
            witness: { aid: workerA.witness.aid },
            watcher: { aid: workerA.watcher.aid },
            witnessUrl: workerA.witnessUrl,
            watcherUrl: workerA.watcherUrl,
        };
        const workerB = (await pageB.evaluate((s) => window.__oobiP8WorkerB!(s), workerBState)) as any;
        // eslint-disable-next-line no-console
        console.log(`[phase9] WorkerB ${JSON.stringify(workerB)}`);

        expect(externalRequests, `external/forbidden during restart: ${externalUrls.join(', ')}`).toBe(0);
        expect(postRestartOobi, `OOBI refetch after server restart: ${postRestartOobi}`).toBe(0);
        expect(workerB.ok, `Worker B failed: ${JSON.stringify(workerB)}`).toBe(true);
        expect(workerB.accountIdentityPersisted, 'account identity must survive server restart').toBe(true);

        const afterW = workerB.afterReopen.witness;
        const afterWat = workerB.afterReopen.watcher;
        expect(afterW.kever_cached_in_memory, 'witness must NOT come from prior process memory').toBe(false);
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
        expect(workerB.oobiCounts.coobi).toBe(0);
        expect(workerB.oobiCounts.oobis).toBe(0);

        // eslint-disable-next-line no-console
        console.log(
            `[phase9] PASS serverA=${pidA} serverB=${pidB} sameArtifact=true ` +
            `account=${workerB.accountIdentityPersisted} witness=${JSON.stringify(afterW.kever)} watcher=${JSON.stringify(afterWat.kever)} ` +
            `postRestartOobi=${postRestartOobi} external=${externalRequests}`,
        );
        expect(pageErrors).toEqual([]);
    } finally {
        // Stop only the server processes this test owns (never kill by port).
        if (serverB) {
            const exit = await stopServer(serverB);
            // eslint-disable-next-line no-console
            console.log(`[phase9] SERVER_B stopped code=${exit.code} signal=${exit.signal}`);
        }
        if (serverA) {
            await stopServer(serverA);
        }
    }
});
