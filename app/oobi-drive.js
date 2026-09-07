/**
 * In-page driver for the OOBI real-worker no-network experiment.
 *
 * Boots the REAL Fort Web worker through createRuntimeBridge with the
 * test TOML (pyscript-oobi-test.toml, fort_oobi_test=true), proves the worker
 * boots, creates+opens a disposable vault, then invokes the test-gated
 * __test.oobi.parse method. Structured result is written to #result.
 *
 * Playwright sets window.__oobiFixtureB64 / window.__oobiFixtureSha before
 * calling window.__oobiRun().
 */
import { createRuntimeBridge } from "../app/runtime/bridge.js";

function setStatus(text) {
    const el = document.querySelector("#status");
    if (el) {
        el.innerText = text;
    }
    // eslint-disable-next-line no-console
    console.log("[oobi-drive]", text);
}

const EXPECTED_FIXTURE_SHA = "48fcc323fc2f7956ecd81d2ced766dc09caecc25c9d79ebdc96bb17642a95453";
const EXPECTED_WATCHER_SHA = "6565f493cb65f21abc965dc5b8b5f065de5a1169b2f00453183b7633212f2e1a";
const EXPECTED_V1_SHA = "6f6a86bb63ce5a15bcb2bced2020ea821cecd29a596a40b711efa38c9e5a0b61";

async function run() {
    const results = {
        steps: [],
        lastStep: "(none)",
        realWorkerBoot: "NOT_RUN",
        testConfigPropagated: "NOT_RUN",
        normalConfigRejectsTestMethod: "NOT_RUN",
        vaultCreated: "NOT_RUN",
        vaultOpened: "NOT_RUN",
        level1: "NOT_RUN",
    };
    const step = (name, ok) => {
        results.steps.push(`${ok ? "OK" : "FAIL"} ${name}`);
        // eslint-disable-next-line no-console
        console.log(`[oobi-drive] STEP ${ok ? "OK" : "FAIL"} ${name}`);
        setStatus(`STEP_${ok ? "OK" : "FAIL"}_${name}`);
    };
    const request = async (bridge, method, params, timeoutMs, label) => {
        results.lastStep = label;
        // eslint-disable-next-line no-console
        console.log(`[oobi-drive] REQ ${label} -> ${method}`);
        return bridge.request(method, params, timeoutMs);
    };
    try {
        const fixtureB64 = window.__oobiFixtureB64;
        if (!fixtureB64) {
            throw new Error("window.__oobiFixtureB64 was not set by the test harness.");
        }
        const watcherB64 = window.__oobiWatcherFixtureB64;
        const watcherSha = window.__oobiWatcherFixtureSha || EXPECTED_WATCHER_SHA;
        const v1B64 = window.__oobiV1FixtureB64;
        const v1Sha = window.__oobiV1FixtureSha || EXPECTED_V1_SHA;

        // ---- worker + config URLs mirror main.ts but point at the test TOML ----
        // The TEST bridge uses the test TOML whose `fort_oobi_test = true` flag is
        // read dynamically by the worker at request time. The NORMAL bridge uses
        // production pyscript-ci.toml (no flag) to prove the gate stays closed.
        const workerUrl = new URL("./runtime/wallet-worker.py", import.meta.url);
        const testConfigUrl = new URL("../pyscript-oobi-test.toml", import.meta.url);
        const normalConfigUrl = new URL("../pyscript-ci.toml", import.meta.url);

        const aliasBase = `oobitest-${Date.now()}`;
        const passcode = "oobitest-pass";

        // ================= TEST-config bridge =================
        setStatus("STEP create bridge");
        const testBridge = createRuntimeBridge({ workerUrl, configUrl: testConfigUrl });

        // 1a. Disposable vault (create needs no open state).
        const created = await request(testBridge, "vaults.create", { name: aliasBase, passcode }, 120_000, "vaults.create");
        const vaultId = created.vault.id;
        results.vaultCreated = "PASS";
        results.createdShape = created.vault;
        step("vaults.create", true);

        // 1b. Open it — establishes the open-state requirement for parse.
        const opened = await request(testBridge, "vaults.open", { vaultId, passcode }, 120_000, "vaults.open");
        results.vaultOpened = "PASS";
        results.openShape = opened;
        step("vaults.open", true);

        // 2. Real worker boot proof (settings.get now that a vault is open).
        const settings = await request(testBridge, "settings.get", { vaultId }, 60_000, "settings.get");
        results.realWorkerBoot = "PASS";
        results.settings = settings;
        step("settings.get", true);

        // 3. Level experiments (1 => bare Parser V2, 2 => Oobiery.parser,
        //    3 => Oobiery.processClients w/ completed CESR response). Each level
        //    is isolated so a hang/error on one cannot clobber the others. Once a
        //    level TIMEOUTs the worker is wedged (synchronous parse hold under the
        //    request lock), so remaining levels are skipped on that worker.
        const runLevel = async (level, label) => {
            results.lastStep = label;
            // eslint-disable-next-line no-console
            console.log(`[oobi-drive] REQ ${label} -> __test.oobi.parse level=${level}`);
            setStatus(`STEP ${label}`);
            try {
                const r = await testBridge.request(
                    "__test.oobi.parse",
                    { fixtureBase64: fixtureB64, expectedSha: EXPECTED_FIXTURE_SHA, level },
                    20_000,
                );
                step(label, true);
                return { status: "PASS", data: r };
            } catch (err) {
                const message = String((err && err.message) || err);
                const timedOut = /timed out|timeout|TIMEOUT/i.test(message);
                step(label, false);
                return { status: timedOut ? "TIMEOUT" : "ERROR", error: message.slice(0, 300) };
            }
        };

        results.testConfigPropagated = "PASS"; // gate opened => gated method callable

        const l1 = await runLevel(1, "__test.oobi.parse L1");
        results.level1 = l1.status;
        results.l1 = l1.status === "PASS" ? l1.data : { status: l1.status, error: l1.error };

        // Each higher variant must run on a FRESH worker + vault: a synchronous
        // parse hang wedges that worker under the request lock (the bridge then
        // reboots it and a retry sees an unopened vault -> LOCKED), so we never
        // reuse a worker after a non-PASS variant.
        const runVariantOnFreshWorker = async (spec) => {
            const { level, label, b64, sha, oobiUrl } = spec;
            setStatus(`STEP ${label}`);
            const variantAlias = `${aliasBase}-v${level}-${Date.now() % 100000}`;
            const freshBridge = createRuntimeBridge({ workerUrl, configUrl: testConfigUrl });
            const createdV = await request(freshBridge, "vaults.create", { name: variantAlias, passcode }, 120_000, `${label} vaults.create`);
            await request(freshBridge, "vaults.open", { vaultId: createdV.vault.id, passcode }, 120_000, `${label} vaults.open`);
            const startedAt = Date.now();
            // eslint-disable-next-line no-console
            console.log(`[oobi-drive] REQ ${label} -> __test.oobi.parse level=${level}`);
            try {
                const params = { level };
                if (b64) {
                    params.fixtureBase64 = b64;
                    params.expectedSha = sha;
                }
                if (oobiUrl) {
                    params.oobiUrl = oobiUrl;
                }
                const r = await freshBridge.request("__test.oobi.parse", params, 30_000);
                const durationMs = Date.now() - startedAt;
                return { status: "PASS", data: r, durationMs };
            } catch (err) {
                const message = String((err && err.message) || err);
                const durationMs = Date.now() - startedAt;
                // A parse hang surfaces as a bridge timeout followed by a retry
                // against a rebooted (unopened) worker -> LOCKED; treat either a
                // timeout message or a long elapsed time as the hang signal.
                const timedOut = /timed out|timeout|TIMEOUT/i.test(message) || durationMs >= 10_000;
                return { status: timedOut ? "TIMEOUT" : "ERROR", error: message.slice(0, 300), durationMs };
            }
        };

        const variantSpecs = [
            // Permanent V2 regression — stock Oobiery parser (L2), stock
            // Oobiery.processClients (L3), and L4 REAL BrowserClienter against
            // the localhost deterministic OOBI endpoint, for BOTH witness and
            // watcher. No parser monkeypatch, no V1 fallback.
            { key: "level2", level: 2, label: "WIT L2 stock Oobiery", b64: fixtureB64, sha: EXPECTED_FIXTURE_SHA },
            { key: "level3", level: 3, label: "WIT L3 processClients", b64: fixtureB64, sha: EXPECTED_FIXTURE_SHA },
            { key: "level4", level: 4, label: "WIT L4 BrowserClienter", oobiUrl: `${window.location.origin}/oobi/BIWLbdRiC1X2ylzDl-blkqkXKz7LI-1ErzbjokYVlk9Z/controller` },
            { key: "watcherL1", level: 1, label: "WAT L1 bare Parser", b64: watcherB64, sha: watcherSha },
            { key: "watcherL2", level: 2, label: "WAT L2 stock Oobiery", b64: watcherB64, sha: watcherSha },
            { key: "watcherL3", level: 3, label: "WAT L3 processClients", b64: watcherB64, sha: watcherSha },
            { key: "watcherL4", level: 4, label: "WAT L4 BrowserClienter", oobiUrl: `${window.location.origin}/oobi/BBYFnq8-_i2hjGemEsUMdE6M4RAB9Bi3iY7dvfEGKV2O/controller` },
            { key: "v1policy", level: 1, label: "V1 reject policy", b64: v1B64, sha: v1Sha },
        ];
        const variantResults = {};
        for (const spec of variantSpecs) {
            if (!spec.b64 && !spec.oobiUrl) {
                variantResults[spec.key] = { status: "ERROR", error: "variant has no fixture or oobiUrl" };
                step(spec.label, false);
                continue;
            }
            const res = await runVariantOnFreshWorker(spec);
            variantResults[spec.key] =
                res.status === "PASS" ? res.data : { status: res.status, error: res.error, durationMs: res.durationMs };
            step(spec.label, res.status === "PASS");
            if (res.status === "TIMEOUT") {
                // Keep going: each remaining variant is independent on a fresh worker.
                // eslint-disable-next-line no-console
                console.log(`[oobi-drive] ${spec.label} TIMEOUT (hang reproduced)`);
            }
        }
        results.variants = variantResults;

        // ================= normal-config bridge (fresh worker) =================
        // Prove production config rejects the reserved test method. Open a second
        // disposable vault first so the failure is the ALLOWLIST rejection, not
        // "Vault is required".
        setStatus("STEP normal bridge create");
        const normalBridge = createRuntimeBridge({ workerUrl, configUrl: normalConfigUrl });
        const normalAlias = `${aliasBase}-normal`;
        const normalCreated = await request(normalBridge, "vaults.create", { name: normalAlias, passcode }, 120_000, "normal vaults.create");
        const normalVaultId = normalCreated.vault.id;
        await request(normalBridge, "vaults.open", { vaultId: normalVaultId, passcode }, 120_000, "normal vaults.open");
        step("normal vault open", true);
        try {
            await request(
                normalBridge,
                "__test.oobi.parse",
                { vaultId: normalVaultId, fixtureBase64: fixtureB64, expectedSha: EXPECTED_FIXTURE_SHA, level: 1 },
                15_000,
                "normal __test.oobi.parse",
            );
            results.normalConfigRejectsTestMethod = "NO (method unexpectedly allowed)";
        } catch (err) {
            const message = String((err && err.message) || err);
            results.normalConfigRejectsTestMethod = /not allowed|not.*allow|forbidden/i.test(message)
                ? "YES (rejected by allowlist)"
                : `PARTIAL (${message.slice(0, 160)})`;
        }
        step("normal __test.oobi.parse reject", results.normalConfigRejectsTestMethod.startsWith("YES"));
    } catch (err) {
        const message = String((err && err.message) || err);
        results.error = message.slice(0, 500);
        if (results.level1 !== "PASS") {
            results.level1 = message.includes("timed out") || message.includes("TIMEOUT") ? "TIMEOUT" : "ERROR";
        }
        step(`exception @ ${results.lastStep}`, false);
        setStatus(`FAIL ${message.slice(0, 300)}`);
    }
    document.querySelector("#result").innerText = JSON.stringify(results, null, 2);
    return results;
}

// ---------------------------------------------------------------------------
// Phase 8 — reopen persistence hard gate.
//
// Worker A: create+open a dedicated vault, resolve witness AND watcher through
// the REAL BrowserClienter (L4) in the SAME vault, verify persisted material is
// present through the normal KERI stores, close via the PRODUCT vaults.close
// path (Habery.aclose), then destroy the bridge/worker completely.
//
// Worker B (orchestrated by the spec AFTER Worker A is destroyed, with /oobi/
// network hard-blocked): opens the EXISTING vault in a completely fresh worker,
// and proves the remote witness/watcher kevers + loc + role are reconstructed
// from persisted browser KERI records with ZERO OOBI refetch.
//
// These are two separate window entry points so the Playwright spec can flip
// network blocking between them (Worker A may use the localhost fixture
// endpoint; Worker B must never issue an /oobi/ request).
// ---------------------------------------------------------------------------
const P8_WITNESS_AID = "BIWLbdRiC1X2ylzDl-blkqkXKz7LI-1ErzbjokYVlk9Z";
const P8_WATCHER_AID = "BBYFnq8-_i2hjGemEsUMdE6M4RAB9Bi3iY7dvfEGKV2O";
const P8_WIT_LOC = "https://138.68.53.132:5633";
const P8_WAT_LOC = "https://138.68.53.132:7633";
const P8_PASSCODE = "p8passcode-2026";

function p8Result(initial = {}) {
    return { ok: false, steps: [], ...initial };
}

async function __oobiP8WorkerA() {
    const res = p8Result();
    const step = (name, ok) => {
        res.steps.push(`${ok ? "OK" : "FAIL"} ${name}`);
        // eslint-disable-next-line no-console
        console.log(`[oobi-drive] P8A STEP ${ok ? "OK" : "FAIL"} ${name}`);
        setStatus(`P8A_${ok ? "OK" : "FAIL"}_${name}`);
    };
    const workerUrl = new URL("./runtime/wallet-worker.py", import.meta.url);
    const testConfigUrl = new URL("../pyscript-oobi-test.toml", import.meta.url);
    const bridge = createRuntimeBridge({ workerUrl, configUrl: testConfigUrl });
    const alias = `p8vault-${Date.now()}`;
    try {
        const created = await bridge.request("vaults.create", { name: alias, passcode: P8_PASSCODE }, 120_000, "P8A vaults.create");
        const vault = created.vault;
        res.vaultId = vault.id;
        res.alias = vault.alias;
        res.storageName = vault.storageName;
        res.createdAt = vault.createdAt;
        step("vaults.create", true);

        const opened = await bridge.request("vaults.open", { vaultId: vault.id, passcode: P8_PASSCODE }, 120_000, "P8A vaults.open");
        res.openStorageName = opened.vault.storageName;
        res.openCreatedAt = opened.vault.createdAt;
        step("vaults.open", true);

        // Stable local account identity so Worker B can prove it reopened the
        // SAME stored vault (same AID) rather than a fresh empty one.
        const acct = await bridge.request("identifiers.create", { alias: "p8-account", vaultId: vault.id }, 90_000, "P8A identifiers.create");
        res.accountAid = acct.identifier.aid;
        res.accountAlias = acct.identifier.alias;
        step("identifiers.create", true);

        // L4 real BrowserClienter resolve for BOTH witness and watcher, in the
        // SAME open vault (no fixture bytes needed — served localhost endpoint).
        const witUrl = `${window.location.origin}/oobi/${P8_WITNESS_AID}/controller`;
        const wit = await bridge.request("__test.oobi.parse", { level: 4, oobiUrl: witUrl }, 30_000, "P8A witness L4");
        res.witness = {
            aid: P8_WITNESS_AID,
            l4: wit.l4,
            roobi_state: wit.roobi_state,
            loc_url: wit.loc_url,
            keystate: wit.keystate,
            controller_role: wit.controller_role,
        };
        step("witness L4 BrowserClienter", !!(wit.l4 === "real_browserclienter" && wit.roobi_state === "resolved" && wit.loc_url === P8_WIT_LOC));
        res.witnessUrl = witUrl;

        const watUrl = `${window.location.origin}/oobi/${P8_WATCHER_AID}/controller`;
        const wat = await bridge.request("__test.oobi.parse", { level: 4, oobiUrl: watUrl }, 30_000, "P8A watcher L4");
        res.watcher = {
            aid: P8_WATCHER_AID,
            l4: wat.l4,
            roobi_state: wat.roobi_state,
            loc_url: wat.loc_url,
            keystate: wat.keystate,
            controller_role: wat.controller_role,
        };
        step("watcher L4 BrowserClienter", !!(wat.l4 === "real_browserclienter" && wat.roobi_state === "resolved" && wat.loc_url === P8_WAT_LOC));
        res.watcherUrl = watUrl;

        // Persisted source material BEFORE close (normal KERI store reads).
        const iw = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: P8_WITNESS_AID, url: witUrl }, 30_000, "P8A inspect witness");
        const iwa = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: P8_WATCHER_AID, url: watUrl }, 30_000, "P8A inspect watcher");
        res.beforeClose = { witness: iw, watcher: iwa };
        step("persisted material present", !!(iw.persisted_kel && iw.persisted_state && iw.loc_url === P8_WIT_LOC && iwa.persisted_kel && iwa.persisted_state && iwa.loc_url === P8_WAT_LOC));

        // PRODUCT vault lifecycle close (vaults.close -> Habery.aclose flush).
        await bridge.request("vaults.close", { vaultId: vault.id }, 60_000, "P8A vaults.close");
        step("vaults.close", true);

        const lc = await bridge.request("__test.oobi.p8", { cmd: "last_close" }, 30_000, "P8A last_close");
        res.lastClose = lc.last_close || null;
        const acloseOk = !!(lc.last_close && lc.last_close.returned === true && lc.last_close.baser_opened === false && lc.last_close.keeper_opened === false);
        step("aclose returned + baser/keeper flush", acloseOk);

        // DESTROY WORKER A completely.
        bridge.destroy();
        res.workerATerminated = true;
        step("worker A destroyed", true);
        res.ok = acloseOk;
        return res;
    } catch (err) {
        res.error = String((err && err.message) || err).slice(0, 2000);
        res.ok = false;
        try {
            bridge.destroy();
        } catch {
            // ignore destroy failure in error path
        }
        return res;
    }
}

async function __oobiP8WorkerB(state) {
    const res = p8Result();
    const step = (name, ok) => {
        res.steps.push(`${ok ? "OK" : "FAIL"} ${name}`);
        // eslint-disable-next-line no-console
        console.log(`[oobi-drive] P8B STEP ${ok ? "OK" : "FAIL"} ${name}`);
        setStatus(`P8B_${ok ? "OK" : "FAIL"}_${name}`);
    };
    const workerUrl = new URL("./runtime/wallet-worker.py", import.meta.url);
    const testConfigUrl = new URL("../pyscript-oobi-test.toml", import.meta.url);
    const bridge = createRuntimeBridge({ workerUrl, configUrl: testConfigUrl });
    try {
        // Worker B must NEVER call vaults.create — only reopen the EXISTING vault.
        const opened = await bridge.request("vaults.open", { vaultId: state.vaultId, passcode: P8_PASSCODE }, 120_000, "P8B vaults.open");
        res.openStorageName = opened.vault.storageName;
        res.openCreatedAt = opened.vault.createdAt;
        step("vaults.open existing", true);

        const ids = await bridge.request("__test.oobi.p8", { cmd: "identities" }, 30_000, "P8B identities");
        res.identities = ids.identifiers || [];
        const acct = (res.identities || []).find((x) => x.alias === state.accountAlias);
        res.accountIdentityPersisted = !!(acct && acct.aid === state.accountAid);
        step("account identity persisted", res.accountIdentityPersisted === true);

        // Reconstructed witness + watcher — NO /oobi/ network (spec blocks it).
        const iw = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: state.witness.aid, url: state.witnessUrl }, 30_000, "P8B witness inspect");
        const iwa = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: state.watcher.aid, url: state.watcherUrl }, 30_000, "P8B watcher inspect");
        res.afterReopen = { witness: iw, watcher: iwa };

        const witOk = !!(iw.persisted_kel && iw.persisted_state && iw.kever_reconstructed && iw.kever_usable && iw.loc_url === P8_WIT_LOC && iw.controller_role === true);
        const watOk = !!(iwa.persisted_kel && iwa.persisted_state && iwa.kever_reconstructed && iwa.kever_usable && iwa.loc_url === P8_WAT_LOC && iwa.controller_role === true);
        step("witness kever/loc/role reconstructed", witOk);
        step("watcher kever/loc/role reconstructed", watOk);

        const counts = await bridge.request("__test.oobi.p8", { cmd: "oobi_counts" }, 30_000, "P8B oobi_counts");
        res.oobiCounts = counts;
        step("no pending oobi resolution (coobi empty)", counts.coobi === 0);

        res.witnessUsable = iw.kever_usable;
        res.watcherUsable = iwa.kever_usable;
        step("reconstructed state usable", !!(iw.kever_usable && iwa.kever_usable));

        await bridge.request("vaults.close", { vaultId: state.vaultId }, 60_000, "P8B vaults.close");
        const lc = await bridge.request("__test.oobi.p8", { cmd: "last_close" }, 30_000, "P8B last_close");
        res.lastClose = lc.last_close || null;
        step("worker B vault close", !!(lc.last_close && lc.last_close.returned === true));
        bridge.destroy();
        step("worker B destroyed", true);

        res.witnessPASS = witOk;
        res.watcherPASS = watOk;
        res.noRefetchPASS = counts.coobi === 0;
        res.ok = witOk && watOk && res.accountIdentityPersisted === true && counts.coobi === 0;
        return res;
    } catch (err) {
        res.error = String((err && err.message) || err).slice(0, 2000);
        res.ok = false;
        try {
            bridge.destroy();
        } catch {
            // ignore destroy failure in error path
        }
        return res;
    }
}

window.__oobiRun = run;
window.__oobiP8WorkerA = __oobiP8WorkerA;
window.__oobiP8WorkerB = __oobiP8WorkerB;
