/**
 * In-page driver for the REAL Fort Web live DigitalOcean onboarding.
 *
 * Boots the REAL Fort Web worker through createRuntimeBridge using the
 * PRODUCTION runtime config (pyscript-ci.toml — no test marker, test methods
 * rejected) and drives the actual product APIs:
 *
 *   Phase 1 (__liveRun): vaults.create/open -> kf.bootstrap.get (kopn0) ->
 *     kf.onboarding.start (fresh session; Kf Boot provisions a fresh hosted
 *     V2 witness + watcher; Fort Web registers with the witness, resolves both
 *     OOBIs through the real BrowserClienter, rotates for witness receipts,
 *     introduces the account to the watcher, and completes onboarding) ->
 *     kf.account.witnesses.list / watchers.list / watchers.status.
 *
 *   Phase 2 (__liveReopen): after a product close + worker destroy + fresh
 *     page/worker, reopen the SAME vault with the TEST config (marker present)
 *     purely so the reserved __test.oobi.p8 inspect probe can prove the live
 *     witness/watcher kevers + loc + role reconstruct from IndexedDB with
 *     /oobi/ refetch hard-blocked. Product lifecycle (vaults.open/close) is
 *     identical; only the read-only inspect probe is enabled by the marker.
 *
 * Structured result is written to #result.
 */
import { createRuntimeBridge } from "../app/runtime/bridge.js";

function setStatus(text) {
    const el = document.querySelector("#status");
    if (el) {
        el.innerText = text;
    }
    // eslint-disable-next-line no-console
    console.log("[live-drive]", text);
}

function liveResult(initial = {}) {
    return { ok: false, steps: [], ...initial };
}

function bootUrls() {
    const workerUrl = new URL("./runtime/wallet-worker.py", import.meta.url);
    const prodConfigUrl = new URL("../pyscript-ci.toml", import.meta.url);
    const testConfigUrl = new URL("../pyscript-oobi-test.toml", import.meta.url);
    return { workerUrl, prodConfigUrl, testConfigUrl };
}

const BOOT_URL = "https://kopn0.keri.foundation";
const WIT_LOC = "https://138.68.53.132:5633";
const WAT_LOC = "https://138.68.53.132:7633";

async function __liveRun() {
    const res = liveResult();
    const step = (name, ok, detail) => {
        res.steps.push(`${ok ? "OK" : "FAIL"} ${name}`);
        // eslint-disable-next-line no-console
        console.log(`[live-drive] LIVE STEP ${ok ? "OK" : "FAIL"} ${name}${detail ? " :: " + JSON.stringify(detail) : ""}`);
        setStatus(`LIVE_${ok ? "OK" : "FAIL"}_${name}`);
    };
    const { workerUrl, prodConfigUrl } = bootUrls();
    const bridge = createRuntimeBridge({ workerUrl, configUrl: prodConfigUrl });
    const alias = `live-v2-${Date.now()}`;
    const passcode = `live-pass-${Date.now()}`;
    const acctAlias = `KF-Acct-${Date.now()}`;
    try {
        // ---- disposable vault via product APIs ----
        const created = await bridge.request("vaults.create", { name: alias, passcode }, 120_000, "live vaults.create");
        const vault = created.vault;
        res.vaultId = vault.id;
        res.vaultAlias = vault.alias;
        res.storageName = vault.storageName;
        res.vaultCreatedAt = vault.createdAt;
        step("vaults.create", true, { vaultId: vault.id });

        await bridge.request("vaults.open", { vaultId: vault.id, passcode }, 120_000, "live vaults.open");
        step("vaults.open", true);

        // ---- connect to LIVE Kf Boot ----
        const boot = await bridge.request("kf.bootstrap.get", { vaultId: vault.id, bootUrl: BOOT_URL }, 90_000, "live kf.bootstrap.get");
        res.boot = {
            bootUrl: boot.bootUrl,
            connectionOk: (boot.connection || {}).ok,
            watcherRequired: (boot.bootstrap || {}).watcherRequired,
            accountOptions: (boot.bootstrap || {}).accountOptions,
            onboardingSurface: (boot.surfaces || {}).onboardingUrl,
            accountSurface: (boot.surfaces || {}).accountUrl,
        };
        const watcherRequired = res.boot.watcherRequired === true;
        step("kf.bootstrap.get (BOOT_CONNECTED)", boot.connection && boot.connection.ok === true, res.boot);
        step("watcher_required", watcherRequired, { watcherRequired });

        // ---- start FRESH live onboarding (provision + register + receipt + watcher) ----
        const startedAt = Date.now();
        const onboard = await bridge.request(
            "kf.onboarding.start",
            { vaultId: vault.id, alias: acctAlias, witnessProfileCode: "1-of-1", bootUrl: BOOT_URL },
            240_000,
            "live kf.onboarding.start",
        );
        res.onboardingDurationMs = Date.now() - startedAt;
        res.onboarded = onboard;
        const account = onboard.account || {};
        const witnesses = onboard.witnesses || [];
        const watchers = onboard.watchers || [];
        res.accountAid = account.accountAid || "";
        res.accountStatus = account.status || "";
        res.accountOnboardedAt = account.onboardedAt || "";
        const witness = witnesses[0] || {};
        const watcher = watchers[0] || {};
        res.witnessEid = witness.eid || "";
        res.witnessUrl = witness.url || "";
        res.watcherEid = watcher.eid || "";
        res.watcherUrl = watcher.url || "";
        step("kf.onboarding.start (onboarded)", res.accountStatus === "onboarded", {
            accountAid: res.accountAid,
            witnessEid: res.witnessEid,
            watcherEid: res.watcherEid,
        });

        // Hosted public locations must be the HTTPS TLS fronts.
        const witLocOk = res.witnessUrl === WIT_LOC;
        const watLocOk = res.watcherUrl === WAT_LOC;
        step("witness hosted URL is HTTPS :5633", witLocOk, { witnessUrl: res.witnessUrl });
        step("watcher hosted URL is HTTPS :7633", watLocOk, { watcherUrl: res.watcherUrl });

        // ---- functional queries through product APIs ----
        const wits = await bridge.request("kf.account.witnesses.list", { vaultId: vault.id }, 90_000, "live witnesses.list");
        res.witnessesList = wits.witnesses || [];
        step("witnesses.list", Array.isArray(res.witnessesList) && res.witnessesList.length > 0, res.witnessesList[0]);

        const wats = await bridge.request("kf.account.watchers.list", { vaultId: vault.id }, 90_000, "live watchers.list");
        res.watchersList = wats.watchers || [];
        step("watchers.list", Array.isArray(res.watchersList) && res.watchersList.length > 0, res.watchersList[0]);

        if (res.watcherEid) {
            const watStatus = await bridge.request(
                "kf.account.watchers.status",
                { vaultId: vault.id, watcherEid: res.watcherEid },
                90_000,
                "live watchers.status",
            );
            res.watcherStatus = watStatus.watcher || {};
            step("watchers.status", !!(res.watcherStatus && res.watcherStatus.eid), res.watcherStatus);
        }

        // ---- DIRECT watcher USE is produced by NORMAL product onboarding ----
        // kf.onboarding.start already performed the controller-signed KERI ksn
        // query over :7633 and persisted the verified milestone. The driver only
        // OBSERVES the product-produced result; it must not be the component that
        // creates the milestone (DRIVER_QUERY_REQUIRED_FOR_SUCCESS = NO).
        const wq = (onboard && onboard.watcherQuery) || {};
        res.watcherQuery = wq;
        if (wq && wq.replySaid) {
            const queryOk = !!(
                wq.replySaid &&
                wq.protocolMajor === 2 &&
                wq.controller === res.accountAid &&
                wq.sn === "1"
            );
            res.queryOk = queryOk;
            step("watcher query verified during product onboarding (KERI v2, sn=1)", queryOk, wq);
        } else {
            const productError =
                (onboard && (onboard.watcherQueryError || (onboard.account || {}).watcherQueryError)) ||
                "product onboarding did not produce a verified watcher query";
            res.watcherQueryError = String(productError).slice(0, 1200);
            res.queryOk = false;
            step("watcher query verified during product onboarding (KERI v2, sn=1)", false, res.watcherQueryError);
        }

        // ---- normalized domain view model (UI boundary): kf.services.overview ----
        const overview = await bridge.request(
            "kf.services.overview",
            { vaultId: vault.id },
            60_000,
            "live services.overview",
        );
        res.services = overview.services || {};
        const svc = res.services;
        const overviewOk = !!(
            svc.witness &&
            svc.witness.directStatus === "connected" &&
            svc.witness.oobiVerified === true &&
            svc.witness.registered === true &&
            svc.witness.receiptVerified === true &&
            svc.watcher &&
            svc.watcher.directStatus === "connected" &&
            svc.watcher.oobiVerified === true &&
            svc.watcher.introduced === true &&
            svc.watcher.queryVerified === true &&
            svc.watcher.observedSn === 1
        );
        res.overviewOk = overviewOk;
        step("services.overview DIRECT (witness+wacher connected)", overviewOk, svc);

        // ---- product close before any teardown ----
        await bridge.request("vaults.close", { vaultId: vault.id }, 60_000, "live vaults.close");
        step("vaults.close", true);
        res.passcode = passcode;
        bridge.destroy();
        res.workerTerminated = true;
        res.ok =
            res.accountStatus === "onboarded" &&
            !!res.accountAid &&
            !!res.witnessEid &&
            !!res.watcherEid &&
            witLocOk &&
            watLocOk &&
            res.queryOk === true &&
            res.overviewOk === true;
        return res;
    } catch (err) {
        res.error = String((err && err.message) || err).slice(0, 1500);
        res.ok = false;
        try {
            bridge.destroy();
        } catch {
            // ignore
        }
        return res;
    }
}

async function __liveReopen(state) {
    // Reopen the SAME live vault in a fresh worker. Test TOML enables only the
    // read-only __test.oobi.p8 inspect probe (product lifecycle unchanged).
    const res = liveResult();
    const step = (name, ok, detail) => {
        res.steps.push(`${ok ? "OK" : "FAIL"} ${name}`);
        // eslint-disable-next-line no-console
        console.log(`[live-drive] REOPEN STEP ${ok ? "OK" : "FAIL"} ${name}${detail ? " :: " + JSON.stringify(detail) : ""}`);
        setStatus(`REOPEN_${ok ? "OK" : "FAIL"}_${name}`);
    };
    const { workerUrl, testConfigUrl } = bootUrls();
    const bridge = createRuntimeBridge({ workerUrl, configUrl: testConfigUrl });
    try {
        const opened = await bridge.request("vaults.open", { vaultId: state.vaultId, passcode: state.passcode }, 120_000, "reopen vaults.open");
        res.storageName = opened.vault.storageName;
        res.createdAt = opened.vault.createdAt;
        step("vaults.open existing (same vault)", res.storageName === state.storageName);

        const ids = await bridge.request("__test.oobi.p8", { cmd: "identities" }, 30_000, "reopen identities");
        res.identities = ids.identifiers || [];
        const acct = (res.identities || []).find((x) => x.aid === state.accountAid);
        res.accountIdentityPersisted = !!acct;
        step("account identity persisted (same AID)", res.accountIdentityPersisted === true);

        const iw = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: state.witnessEid }, 30_000, "reopen witness inspect");
        const iwa = await bridge.request("__test.oobi.p8", { cmd: "inspect", aid: state.watcherEid }, 30_000, "reopen watcher inspect");
        res.afterReopen = { witness: iw, watcher: iwa };
        const witOk = !!(iw.persisted_kel && iw.persisted_state && iw.kever_reconstructed && iw.kever_usable && iw.loc_url === WIT_LOC && iw.controller_role === true);
        const watOk = !!(iwa.persisted_kel && iwa.persisted_state && iwa.kever_reconstructed && iwa.kever_usable && iwa.loc_url === WAT_LOC && iwa.controller_role === true);
        step("witness kever/loc/role reconstructed", witOk, iw);
        step("watcher kever/loc/role reconstructed", watOk, iwa);

        const counts = await bridge.request("__test.oobi.p8", { cmd: "oobi_counts" }, 30_000, "reopen oobi_counts");
        res.oobiCounts = counts;
        step("no pending oobi resolution (coobi empty)", counts.coobi === 0);

        await bridge.request("vaults.close", { vaultId: state.vaultId }, 60_000, "reopen vaults.close");
        step("reopen vaults.close", true);
        bridge.destroy();
        res.ok = res.accountIdentityPersisted === true && witOk && watOk && counts.coobi === 0;
        res.witnessPASS = witOk;
        res.watcherPASS = watOk;
        return res;
    } catch (err) {
        res.error = String((err && err.message) || err).slice(0, 1500);
        res.ok = false;
        try {
            bridge.destroy();
        } catch {
            // ignore
        }
        return res;
    }
}

window.__liveRun = __liveRun;
window.__liveReopen = __liveReopen;
