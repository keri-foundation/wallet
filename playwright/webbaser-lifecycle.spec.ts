import { expect, test, type Page, type Request, type Worker } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";


type PhaseResult = {
    ok: boolean;
    phase?: string;
    worker_id: string;
    message?: string;
    traceback?: string;
    fixtures?: Record<string, unknown>;
    checks?: string[];
    runtime?: {
        worker_id: string;
        pyodide_version: string;
        python_version: string;
        platform: string;
        distributions: Record<string, { version: string; requires_python: string }>;
        module_paths: Record<string, string>;
        sys_path: string[];
        native_import_leaks: string[];
        wheel_hashes: Record<string, string>;
        package_closure: PackageClosure;
    };
};

type PackageClosure = {
    manifest_url: string;
    manifest_sha256: string;
    runtime: RuntimeManifest["runtime"];
    actual_runtime: {
        pyodide: string;
        python: string;
        platform: string;
        sysconfig_platform: string;
        tags: string[];
    };
    install_order: string[];
    wheels: Record<string, { filename: string; name: string; version: string; sha256: string; bytes: number; path: string; url: string }>;
    installed_distributions: Record<string, { name: string; version: string; requires_python: string; requires: string[] }>;
    dependency_edges: Array<{ owner: string; requirement: string; selected: string }>;
    exclusions: Array<{ owner: string; requirement: string }>;
    module_paths: Record<string, string>;
    forbidden_imports: string[];
    closure_sha256: string;
};

type RuntimeManifest = {
    schema: number;
    runtime: { pyodide: string; python: string; emscripten: string; abi: string; core_files: string[] };
    install_order: string[];
    wheels: Array<{ filename: string; normalized_name: string; version: string; sha256: string; bytes: number }>;
    files: Array<{ path: string; sha256: string; bytes: number }>;
};

type Harness = {
    boot(): Promise<{ bootCount: number }>;
    run(request: Record<string, unknown>): Promise<PhaseResult>;
    terminate(): { terminateCount: number };
    counters(): { bootCount: number; terminateCount: number; active: boolean };
};

type ProductionHarness = {
    request(
        method: string,
        params?: Record<string, unknown>,
        timeoutMs?: number,
    ): Promise<Record<string, unknown>>;
    destroy(): void;
    diagnostics(): Array<{ type: string; message: string }>;
};

type NativeMessage = { type: string; timestamp?: string; message: string };

type RequestRecord = {
    sequence: number;
    method: string;
    resource_type: string;
    url: string;
};

type ResponseRecord = {
    sequence: number;
    request_sequence: number;
    method: string;
    url: string;
    status: number;
    bytes: number;
    sha256: string;
};

type RequestFailureRecord = {
    sequence: number;
    request_sequence: number;
    method: string;
    url: string;
    error_text: string;
};

type NetworkEvidence = {
    requests: RequestRecord[];
    responses: ResponseRecord[];
    failures: RequestFailureRecord[];
};

const HARNESS_PATH = "/_runtime-test/ci/fixtures/webbaser-lifecycle/index.html";
const PRODUCTION_HARNESS_PATH = "/_runtime-test/ci/fixtures/webbaser-lifecycle/production.html";
const PACKAGE_MANIFEST_PATH = "/fortweb/runtime-closure.json";
const PHASE_TIMEOUT_MS = 360_000;
const RUNTIME_ROOT = path.resolve(process.env["FORTWEB_RUNTIME_DIR"] ?? "dist/runtime");
const MANIFEST_BYTES = readFileSync(path.join(RUNTIME_ROOT, "runtime-closure.json"));
const MANIFEST = JSON.parse(MANIFEST_BYTES.toString("utf8")) as RuntimeManifest;
const MANIFEST_SHA256 = createHash("sha256").update(MANIFEST_BYTES).digest("hex");
const EXPECTED_WHEEL_HASHES = Object.fromEntries(MANIFEST.wheels.map(({ filename, sha256 }) => [filename, sha256]));
const EXPECTED_DISTRIBUTIONS = Object.fromEntries(
    MANIFEST.wheels.map(({ normalized_name, version }) => [normalized_name, version]),
);
const EXPECTED_VAULTLESS_SETTINGS = {
    tempDatastore: false,
    storageBackend: "Browser IndexedDB via WebBaser and WebKeeper",
    keyAlgorithm: "salty",
    keyTier: "low",
    witnessProfile: "Direct",
    runtimeStatus: "Browser vault worker open over WebBaser and WebKeeper.",
};

function selectArtifactPaths(): Record<string, string> {
    const rowsFor = (filename: string, sha256?: string, bytes?: number) => MANIFEST.files.filter((row) => (
        row.path.split("/").at(-1) === filename
        && (sha256 === undefined || row.sha256 === sha256)
        && (bytes === undefined || row.bytes === bytes)
    ));
    const sharedParent = (selections: Array<{ filename: string; sha256?: string; bytes?: number }>) => {
        let parents: Set<string> | undefined;
        for (const selection of selections) {
            const current = new Set(rowsFor(selection.filename, selection.sha256, selection.bytes)
                .map((row) => path.posix.dirname(row.path)));
            parents = parents === undefined
                ? current
                : new Set([...parents].filter((parent) => current.has(parent)));
        }
        expect(parents?.size).toBe(1);
        return [...parents!][0];
    };
    const wheelParent = sharedParent(MANIFEST.wheels);
    const coreParent = sharedParent(MANIFEST.runtime.core_files.map((filename) => ({ filename })));
    const result: Record<string, string> = {};
    for (const wheel of MANIFEST.wheels) {
        const rows = rowsFor(wheel.filename, wheel.sha256, wheel.bytes)
            .filter((row) => path.posix.dirname(row.path) === wheelParent);
        expect(rows).toHaveLength(1);
        result[wheel.filename] = rows[0].path;
    }
    for (const filename of MANIFEST.runtime.core_files) {
        const rows = rowsFor(filename).filter((row) => path.posix.dirname(row.path) === coreParent);
        expect(rows).toHaveLength(1);
        result[filename] = rows[0].path;
    }
    return result;
}

const SELECTED_ARTIFACT_PATHS = selectArtifactPaths();
const FORTWEB_RUN_ID = process.env["FORTWEB_RUN_ID"] ?? "";
const FORTWEB_ARTIFACT_DIR = process.env["FORTWEB_FOCUSED_ARTIFACT_DIR"] ?? "";
const FORTWEB_SOURCE_IDENTITY_PATH = process.env["FORTWEB_SOURCE_IDENTITY"] ?? "";
const FORTWEB_FOCUSED_SERVER_LOG = process.env["FORTWEB_FOCUSED_SERVER_LOG"] ?? "";
const EVIDENCE_VALUE_COUNT = [
    FORTWEB_RUN_ID,
    FORTWEB_ARTIFACT_DIR,
    FORTWEB_SOURCE_IDENTITY_PATH,
    FORTWEB_FOCUSED_SERVER_LOG,
]
    .filter(Boolean).length;
if (EVIDENCE_VALUE_COUNT !== 0 && EVIDENCE_VALUE_COUNT !== 4) {
    throw new Error(
        "Runtime focused evidence requires run ID, artifact directory, source identity, and server log together",
    );
}
const FORTWEB_SOURCE_IDENTITY_SHA256 = FORTWEB_SOURCE_IDENTITY_PATH
    ? createHash("sha256").update(readFileSync(FORTWEB_SOURCE_IDENTITY_PATH)).digest("hex")
    : "";

function evidenceEnabled(): boolean {
    return EVIDENCE_VALUE_COUNT === 4;
}

async function waitForDelayedServerOutcomes(urls: string[]): Promise<void> {
    if (!FORTWEB_FOCUSED_SERVER_LOG) {
        await new Promise((resolve) => setTimeout(resolve, 7_500));
        return;
    }
    await expect.poll(() => {
        const rows = readFileSync(FORTWEB_FOCUSED_SERVER_LOG, "utf8")
            .split("\n")
            .filter(Boolean)
            .flatMap((line) => {
                try {
                    return [JSON.parse(line) as { event?: string; url?: string; delayed_receipt_sequence?: number }];
                } catch {
                    return [];
                }
            });
        return urls.every((url) => rows.some((row) => (
            row.event === "response"
            && row.url === url
            && typeof row.delayed_receipt_sequence === "number"
        )));
    }, { timeout: 15_000 }).toBe(true);
}

function writeEvidence(filename: string, payload: Record<string, unknown>): void {
    if (!evidenceEnabled()) {
        return;
    }
    mkdirSync(FORTWEB_ARTIFACT_DIR, { recursive: true });
    writeFileSync(path.join(FORTWEB_ARTIFACT_DIR, filename), JSON.stringify({
        schema: 1,
        command_identity: "playwright:runtime-focused-webbaser",
        test_count: 4,
        run_id: FORTWEB_RUN_ID,
        source_identity_sha256: FORTWEB_SOURCE_IDENTITY_SHA256,
        ...payload,
    }, null, 2) + "\n");
}

function shouldHashServedResponse(url: string, baseURL: string): boolean {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol)
        && parsed.origin === new URL(baseURL).origin;
}

function captureNetwork(page: Page, baseURL: string): {
    finish(allowedMissingTerminalUrls?: string[]): Promise<NetworkEvidence>;
} {
    const requests: RequestRecord[] = [];
    const responses: ResponseRecord[] = [];
    const failures: RequestFailureRecord[] = [];
    const requestSequences = new Map<Request, number>();
    const responseTasks: Promise<void>[] = [];
    let requestSequence = 0;
    let responseSequence = 0;

    page.on("request", (request) => {
        const sequence = ++requestSequence;
        requestSequences.set(request, sequence);
        requests.push({
            sequence,
            method: request.method(),
            resource_type: request.resourceType(),
            url: request.url(),
        });
    });
    const recordFailure = (request: Request, errorText: string) => {
        const requestSequence = requestSequences.get(request);
        if (requestSequence === undefined) {
            throw new Error(`failed request has no captured sequence: ${request.url()}`);
        }
        if (failures.some(({ request_sequence }) => request_sequence === requestSequence)) {
            return;
        }
        failures.push({
            sequence: failures.length + 1,
            request_sequence: requestSequence,
            method: request.method(),
            url: request.url(),
            error_text: errorText,
        });
    };
    page.on("requestfailed", (request) => {
        recordFailure(request, request.failure()?.errorText ?? "browser request failed");
    });
    page.on("response", (response) => {
        if (!shouldHashServedResponse(response.url(), baseURL)) {
            return;
        }
        const requestSequence = requestSequences.get(response.request());
        if (requestSequence === undefined) {
            throw new Error(`response has no captured request sequence: ${response.url()}`);
        }
        const sequence = ++responseSequence;
        responseTasks.push((async () => {
            if (response.status() === 304) {
                responses.push({
                    sequence,
                    request_sequence: requestSequence,
                    method: response.request().method(),
                    url: response.url(),
                    status: response.status(),
                    bytes: -1,
                    sha256: "",
                });
                return;
            }
            const body = await response.body();
            responses.push({
                sequence,
                request_sequence: requestSequence,
                method: response.request().method(),
                url: response.url(),
                status: response.status(),
                bytes: body.byteLength,
                sha256: createHash("sha256").update(body).digest("hex"),
            });
        })());
    });

    return {
        async finish(allowedMissingTerminalUrls: string[] = []) {
            const allowedMissing = new Set(allowedMissingTerminalUrls);
            const deadline = Date.now() + 15_000;
            while (true) {
                await Promise.all([...responseTasks]);
                const httpRequestSequences = requests
                    .filter(({ url }) => ["http:", "https:"].includes(new URL(url).protocol))
                    .map(({ sequence }) => sequence);
                const terminalSequences = [
                    ...responses.map(({ request_sequence }) => request_sequence),
                    ...failures
                        .filter(({ url }) => ["http:", "https:"].includes(new URL(url).protocol))
                        .map(({ request_sequence }) => request_sequence),
                ];
                const terminalCounts = new Map<number, number>();
                for (const sequence of terminalSequences) {
                    terminalCounts.set(sequence, (terminalCounts.get(sequence) ?? 0) + 1);
                }
                if (
                    httpRequestSequences.every((sequence) => {
                        const request = requests.find((row) => row.sequence === sequence);
                        const count = terminalCounts.get(sequence) ?? 0;
                        return request && allowedMissing.has(request.url)
                            ? count <= 1
                            : count === 1;
                    })
                ) {
                    break;
                }
                if (Date.now() >= deadline) {
                    const incomplete = requests
                        .filter(({ url }) => ["http:", "https:"].includes(new URL(url).protocol))
                        .filter(({ sequence }) => terminalCounts.get(sequence) !== 1)
                        .map((request) => ({
                            ...request,
                            terminal_count: terminalCounts.get(request.sequence) ?? 0,
                        }));
                    throw new Error(
                        `captured HTTP requests did not reach one terminal event: ${JSON.stringify(incomplete)}`,
                    );
                }
                await new Promise((resolve) => setTimeout(resolve, 25));
            }
            responses.sort((left, right) => left.sequence - right.sequence);
            return { requests, responses, failures };
        },
    };
}

function parseClosureMessages(messages: NativeMessage[]): Array<{ worker_id: string; report: PackageClosure }> {
    return messages.flatMap(({ message }) => {
        if (!message.includes("event=worker_package_closure")) {
            return [];
        }
        const workerMatch = message.match(/worker_id="([a-f0-9]+)"/);
        const reportMatch = message.match(/report_b64="([A-Za-z0-9+/=]+)"/);
        expect(workerMatch, message).toBeTruthy();
        expect(reportMatch, message).toBeTruthy();
        return [{
            worker_id: workerMatch![1],
            report: JSON.parse(Buffer.from(reportMatch![1], "base64").toString("utf8")) as PackageClosure,
        }];
    });
}

function assertClosure(closure: PackageClosure): void {
    expect(closure.manifest_sha256).toBe(MANIFEST_SHA256);
    expect(closure.runtime).toEqual(MANIFEST.runtime);
    expect(closure.actual_runtime).toEqual(expect.objectContaining({
        pyodide: MANIFEST.runtime.pyodide,
        python: MANIFEST.runtime.python,
        platform: "emscripten",
        sysconfig_platform: `emscripten-${MANIFEST.runtime.emscripten}-wasm32`,
    }));
    expect(closure.actual_runtime.tags).toContain(`cp314-cp314-${MANIFEST.runtime.abi}`);
    expect(closure.install_order).toEqual(MANIFEST.install_order);
    expect(Object.fromEntries(
        Object.entries(closure.installed_distributions).map(([name, row]) => [name, row.version]),
    )).toEqual(EXPECTED_DISTRIBUTIONS);
    expect(closure.exclusions).toEqual([
        { owner: "hio", requirement: "lmdb>=1.7.5" },
        { owner: "keri", requirement: "lmdb==2.1.1" },
    ]);
    expect(closure.forbidden_imports).toEqual([]);
}

function uniqueName(prefix: string): string {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function manifestFilePath(filename: string): string {
    const selected = SELECTED_ARTIFACT_PATHS[filename];
    expect(selected).toBeTruthy();
    return selected;
}

function manifestArtifactUrl(filename: string, baseURL: string): string {
    return new URL(manifestFilePath(filename), new URL(PACKAGE_MANIFEST_PATH, baseURL)).href;
}

async function bootWorker(page: Page): Promise<Worker> {
    const observed = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    await page.evaluate(async () => {
        const harness = (window as typeof window & { webbaserHarness: Harness }).webbaserHarness;
        await harness.boot();
    });
    return observed;
}

async function runPhase(page: Page, request: Record<string, unknown>): Promise<PhaseResult> {
    return page.evaluate(async (payload) => {
        const harness = (window as typeof window & { webbaserHarness: Harness }).webbaserHarness;
        return harness.run(payload);
    }, request);
}

async function terminateWorker(page: Page, worker: Worker): Promise<void> {
    const closed = worker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });
    await page.evaluate(() => {
        const harness = (window as typeof window & { webbaserHarness: Harness }).webbaserHarness;
        harness.terminate();
    });
    await closed;
}

function expectPhasePassed(result: PhaseResult, phase: string): void {
    expect(result.ok, result.traceback ?? result.message).toBe(true);
    expect(result.phase).toBe(phase);
    expect(result.worker_id).toBeTruthy();
    expect(result.runtime?.worker_id).toBe(result.worker_id);
    expect(result.runtime?.platform).toBe("emscripten");
    expect(result.runtime?.pyodide_version).toBe(MANIFEST.runtime.pyodide);
    expect(result.runtime?.python_version).toContain(MANIFEST.runtime.python);
    expect(result.runtime?.native_import_leaks).toEqual([]);
    const wheelHashes = Object.fromEntries(
        Object.entries(result.runtime?.wheel_hashes ?? {}).map(([url, digest]) => [new URL(url).pathname.split("/").at(-1), digest]),
    );
    expect(wheelHashes).toEqual(EXPECTED_WHEEL_HASHES);
    for (const name of ["hio", "keri", "pysodium"]) {
        expect(result.runtime?.distributions[name]?.version).toBe(EXPECTED_DISTRIBUTIONS[name]);
    }
    const closure = result.runtime?.package_closure;
    expect(closure?.manifest_sha256).toBe(MANIFEST_SHA256);
    expect(closure?.runtime).toEqual(MANIFEST.runtime);
    expect(closure?.actual_runtime).toEqual(expect.objectContaining({
        pyodide: MANIFEST.runtime.pyodide,
        python: MANIFEST.runtime.python,
        platform: "emscripten",
        sysconfig_platform: `emscripten-${MANIFEST.runtime.emscripten}-wasm32`,
    }));
    expect(closure?.actual_runtime.tags).toContain(`cp314-cp314-${MANIFEST.runtime.abi}`);
    expect(closure?.install_order).toEqual(MANIFEST.install_order);
    expect(Object.fromEntries(
        Object.entries(closure?.installed_distributions ?? {}).map(([name, row]) => [name, row.version]),
    )).toEqual(EXPECTED_DISTRIBUTIONS);
    expect(closure?.exclusions).toEqual([
        { owner: "hio", requirement: "lmdb>=1.7.5" },
        { owner: "keri", requirement: "lmdb==2.1.1" },
    ]);
    expect(closure?.forbidden_imports).toEqual([]);
    for (const path of Object.values(result.runtime?.module_paths ?? {})) {
        expect(path).toContain("/site-packages/");
        expect(path).not.toContain("/fortweb/");
    }
}

test("@smoke WebBaser persists and clears WebBaser state across explicit PyWorker lifetimes", async ({ page, baseURL }, testInfo) => {
    test.setTimeout(900_000);
    expect(baseURL).toBeTruthy();
    const networkRecorder = captureNetwork(page, baseURL!);

    const observedRequests: string[] = [];
    const observedResponses: Array<{ url: string; status: number }> = [];
    page.on("request", (request) => observedRequests.push(request.url()));
    page.on("response", (response) => observedResponses.push({ url: response.url(), status: response.status() }));

    await page.goto(new URL(HARNESS_PATH, baseURL).toString());
    await page.waitForFunction(() => (window as typeof window & { webbaserHarnessReady?: boolean }).webbaserHarnessReady === true);

    const names = {
        webdber: uniqueName("webbaser-webdber"),
        webbaser: uniqueName("webbaser-webbaser"),
        webkeeper: uniqueName("webbaser-webkeeper"),
        habery: uniqueName("webbaser-habery"),
        oobi: uniqueName("webbaser-oobi"),
        nested: uniqueName("webbaser-nested-v2"),
    };

    const workerA = await bootWorker(page);
    const created = await runPhase(page, { phase: "create", names });
    expectPhasePassed(created, "create");
    expect(created.checks).toEqual(expect.arrayContaining([
        "same-worker-close-reopen",
        "pending-write-awaited-close",
        "webkeeper-create-sign-rotate",
        "habery-create-rotate",
        "browserclienter-http-oobi",
        "aggregate-close-partial-failure-retry",
        "mixed-version-witness-receipt-ingestion",
        "multi-witness-receipts-from-parser",
        "nested-v2-exn-stored-from-parser",
    ]));
    expect(created.fixtures).toBeTruthy();
    await terminateWorker(page, workerA);

    const workerB = await bootWorker(page);
    const recovered = await runPhase(page, { phase: "recover", fixtures: created.fixtures });
    expectPhasePassed(recovered, "recover");
    expect(recovered.checks).toEqual(expect.arrayContaining([
        "new-worker-recovery",
        "webkeeper-sign-after-rotation",
        "habery-kel-key-state-recovery",
        "oobi-contact-recovery",
        "clear-awaited",
        "nested-v2-exn-storage-replay",
    ]));
    await terminateWorker(page, workerB);

    const workerC = await bootWorker(page);
    const cleared = await runPhase(page, { phase: "verify-clear", fixtures: created.fixtures });
    expectPhasePassed(cleared, "verify-clear");
    expect(cleared.checks).toEqual([
        "third-worker-clear-absence",
        "nested-v2-exn-clear-absence",
    ]);
    await terminateWorker(page, workerC);

    const workerIds = [created.worker_id, recovered.worker_id, cleared.worker_id];
    expect(new Set(workerIds).size).toBe(3);
    expect(workerA.url()).toBeTruthy();
    expect(workerB.url()).toBeTruthy();
    expect(workerC.url()).toBeTruthy();

    const counters = await page.evaluate(() => {
        const harness = (window as typeof window & { webbaserHarness: Harness }).webbaserHarness;
        return harness.counters();
    });
    expect(counters).toEqual({ bootCount: 3, terminateCount: 3, active: false });

    const oobiResponses = observedResponses.filter(({ url }) => url.includes(`/oobi/${encodeURIComponent("EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW")}/controller`));
    expect(oobiResponses).toContainEqual(expect.objectContaining({ status: 200 }));
    const httpRequests = observedRequests.filter((url) => url.startsWith("http://") || url.startsWith("https://"));
    const expectedOrigin = new URL(baseURL!).origin;
    const externalRequests = httpRequests.filter((url) => new URL(url).origin !== expectedOrigin);
    expect(externalRequests, JSON.stringify(externalRequests, null, 2)).toEqual([]);
    expect(observedRequests).toContain(new URL(PACKAGE_MANIFEST_PATH, baseURL).href);
    for (const wheel of MANIFEST.wheels) {
        expect(observedRequests).toContain(manifestArtifactUrl(wheel.filename, baseURL!));
    }
    expect(observedRequests.some((url) => /0\.29\.3|cp313|pyodide_2025_0|hio_web|keri_web|pychloride/.test(url))).toBe(false);

    const closures = [created, recovered, cleared].map((result) => ({
        worker_id: result.worker_id,
        report: result.runtime!.package_closure,
    }));
    for (const closure of closures) {
        assertClosure(closure.report);
    }
    const network = await networkRecorder.finish();
    writeEvidence("lifecycle-proof.json", {
        ready_worker_count: 3,
        terminated_worker_count: counters.terminateCount,
        worker_ids: workerIds,
        closures,
        phases: { created, recovered, cleared },
    });
    writeEvidence("lifecycle-requests.json", {
        ready_worker_count: 3,
        network,
    });

    await testInfo.attach("webbaser-lifecycle-proof.json", {
        body: JSON.stringify({
            worker_urls: [workerA.url(), workerB.url(), workerC.url()],
            worker_ids: workerIds,
            counters,
            created,
            recovered,
            cleared,
            oobi_responses: oobiResponses,
            http_request_count: httpRequests.length,
        }, null, 2),
        contentType: "application/json",
    });
});

test("@smoke WebBaser production bridge loads local wheels and resolves an OOBI", async ({ page, baseURL }) => {
    test.setTimeout(900_000);
    expect(baseURL).toBeTruthy();
    const networkRecorder = captureNetwork(page, baseURL!);
    const nativeMessages: NativeMessage[] = [];
    await page.exposeFunction("captureRuntimeNativeMessage", (payload: NativeMessage) => {
        nativeMessages.push(payload);
    });

    const observedRequests: string[] = [];
    const observedResponses: Array<{ url: string; status: number }> = [];
    const consoleMessages: string[] = [];
    page.on("request", (request) => observedRequests.push(request.url()));
    page.on("response", (response) => observedResponses.push({ url: response.url(), status: response.status() }));
    page.on("console", (message) => consoleMessages.push(`${message.type()}: ${message.text()}`));
    page.on("pageerror", (error) => consoleMessages.push(`pageerror: ${error.message}`));

    const workerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    await page.goto(new URL(PRODUCTION_HARNESS_PATH, baseURL).toString());
    await page.waitForFunction(() => (
        window as typeof window & { webbaserProductionHarnessReady?: boolean }
    ).webbaserProductionHarnessReady === true);

    const request = (method: string, params: Record<string, unknown> = {}, timeoutMs?: number) => page.evaluate(
        async ({ requestMethod, requestParams, requestTimeoutMs }) => {
            const harness = (
                window as typeof window & { webbaserProductionHarness: ProductionHarness }
            ).webbaserProductionHarness;
            return harness.request(requestMethod, requestParams, requestTimeoutMs);
        },
        { requestMethod: method, requestParams: params, requestTimeoutMs: timeoutMs },
    );

    const vaultName = uniqueName("webbaser-production-vault");
    const productionWorker = await workerStarted;
    const indexedDbNamesBefore = await page.evaluate(async () => (
        (await indexedDB.databases())
            .map(({ name }) => name ?? "")
            .filter(Boolean)
            .sort()
    ));
    const vaultlessSettings = await request("settings.get");
    expect(vaultlessSettings).toEqual({ settings: EXPECTED_VAULTLESS_SETTINGS });
    const indexedDbNamesAfter = await page.evaluate(async () => (
        (await indexedDB.databases())
            .map(({ name }) => name ?? "")
            .filter(Boolean)
            .sort()
    ));
    const vaultDbNamesBefore = indexedDbNamesBefore.filter((name) => name.includes("fortweb-vault"));
    const vaultDbNamesAfter = indexedDbNamesAfter.filter((name) => name.includes("fortweb-vault"));
    expect(vaultDbNamesAfter).toEqual(vaultDbNamesBefore);
    expect(vaultDbNamesAfter).toEqual([]);

    const created = await request("vaults.create", { name: vaultName, passcode: "" }).catch((error) => {
        throw new Error(`${String(error)}\n${consoleMessages.slice(-40).join("\n")}`);
    });
    const vault = created.vault as Record<string, unknown>;
    const vaultId = String(vault.id);
    expect(vaultId).toBeTruthy();
    const lockedSettingsOutcome = await page.evaluate(async (closedVaultId) => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        try {
            await harness.request("settings.get", { vaultId: closedVaultId });
            return null;
        } catch (error) {
            return {
                code: error && typeof error === "object" && "code" in error ? String(error.code) : "",
                message: error instanceof Error ? error.message : String(error),
            };
        }
    }, vaultId);
    expect(lockedSettingsOutcome).toEqual({
        code: "LOCKED",
        message: "Open a vault before calling vault operations.",
    });

    const runtimeDiagnostics = await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        return harness.diagnostics();
    });
    expect(runtimeDiagnostics).toEqual(expect.arrayContaining([
        expect.objectContaining({
            type: "log",
            message: expect.stringMatching(
                /event=runtime_origin_contract_present .*present=true .*platform="browser-dev"/,
            ),
        }),
    ]));

    await request("vaults.open", { vaultId, passcode: "" });
    const alias = uniqueName("webbaser-production-remote");
    const oobiUrl = new URL("/oobi", baseURL);
    oobiUrl.searchParams.set("name", alias);
    const resolved = await request("remotes.resolveOobi", {
        vaultId,
        url: oobiUrl.toString(),
        alias,
    });
    expect(resolved.remote).toEqual(expect.objectContaining({
        aid: "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW",
        alias,
        oobi: oobiUrl.toString(),
    }));

    await request("vaults.close", { vaultId });
    await request("vaults.open", { vaultId, passcode: "" });
    const reopened = await request("remotes.get", {
        vaultId,
        aid: "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW",
    });
    expect(reopened.remote).toEqual(expect.objectContaining({
        alias,
        oobi: oobiUrl.toString(),
    }));
    await request("vaults.close", { vaultId });

    const workerClosed = productionWorker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });
    await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        harness.destroy();
    });
    await workerClosed;

    const restartedWorkerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    await page.reload();
    await page.waitForFunction(() => (
        window as typeof window & { webbaserProductionHarnessReady?: boolean }
    ).webbaserProductionHarnessReady === true);
    const restartedWorker = await restartedWorkerStarted;

    const listed = await request("vaults.list");
    expect(listed.vaults).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: vaultId, alias: vaultName }),
    ]));
    await request("vaults.open", { vaultId, passcode: "" });
    const recovered = await request("remotes.get", {
        vaultId,
        aid: "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW",
    });
    expect(recovered.remote).toEqual(expect.objectContaining({
        alias,
        oobi: oobiUrl.toString(),
    }));
    await request("vaults.close", { vaultId });

    const invalidatedWorkerClosed = restartedWorker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });
    await page.evaluate(() => {
        const originalNow = Date.now;
        let now = originalNow();
        let hidden = true;
        Object.defineProperty(document, "hidden", {
            configurable: true,
            get: () => hidden,
        });
        Date.now = () => now;
        try {
            document.dispatchEvent(new Event("visibilitychange"));
            now += 31_000;
            hidden = false;
            document.dispatchEvent(new Event("visibilitychange"));
        } finally {
            Date.now = originalNow;
            Reflect.deleteProperty(document, "hidden");
        }
    });
    await invalidatedWorkerClosed;

    const replacementWorkerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    const listedAfterInvalidation = await request("vaults.list");
    const replacementWorker = await replacementWorkerStarted;
    expect(listedAfterInvalidation.vaults).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: vaultId, alias: vaultName }),
    ]));

    await request("vaults.open", { vaultId, passcode: "" });
    const shortTimeoutOobiUrl = new URL("/oobi", baseURL);
    shortTimeoutOobiUrl.searchParams.set("name", uniqueName("webbaser-timeout-short"));
    if (!FORTWEB_FOCUSED_SERVER_LOG) {
        await page.route(shortTimeoutOobiUrl.toString(), async (route) => {
            await new Promise((resolve) => setTimeout(resolve, 7_000));
            await route.continue().catch(() => {});
        });
    }

    const staleTimeoutOobiUrl = new URL("/oobi", baseURL);
    staleTimeoutOobiUrl.searchParams.set("name", uniqueName("webbaser-timeout-stale"));
    if (!FORTWEB_FOCUSED_SERVER_LOG) {
        await page.route(staleTimeoutOobiUrl.toString(), async (route) => {
            await new Promise((resolve) => setTimeout(resolve, 7_000));
            await route.continue().catch(() => {});
        });
    }

    await page.evaluate(
        ({ timeoutVaultId, timeoutOobiUrl }) => {
            const scope = window as typeof window & {
                webbaserProductionHarness: ProductionHarness;
                webbaserStaleTimeoutRequest?: Promise<{ code: string; message: string } | null>;
            };
            scope.webbaserStaleTimeoutRequest = scope.webbaserProductionHarness.request("remotes.resolveOobi", {
                vaultId: timeoutVaultId,
                url: timeoutOobiUrl,
                alias: "stale-timeout-probe",
            }, 5_000).then(
                () => null,
                (error) => ({
                    code: error && typeof error === "object" && "code" in error ? String(error.code) : "",
                    message: error instanceof Error ? error.message : String(error),
                }),
            );
        },
        { timeoutVaultId: vaultId, timeoutOobiUrl: staleTimeoutOobiUrl.toString() },
    );
    await expect.poll(
        () => observedRequests.includes(staleTimeoutOobiUrl.toString()),
        { timeout: 10_000 },
    ).toBe(true);

    const workersCreatedDuringTimeout: Worker[] = [];
    const trackTimeoutWorker = (worker: Worker) => workersCreatedDuringTimeout.push(worker);
    page.on("worker", trackTimeoutWorker);
    const timedOutWorkerClosed = replacementWorker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });
    const timeoutFailure = await page.evaluate(
        async ({ timeoutVaultId, timeoutOobiUrl }) => {
            const harness = (
                window as typeof window & { webbaserProductionHarness: ProductionHarness }
            ).webbaserProductionHarness;
            try {
                await harness.request("remotes.resolveOobi", {
                    vaultId: timeoutVaultId,
                    url: timeoutOobiUrl,
                    alias: "short-timeout-probe",
                }, 50);
                return null;
            } catch (error) {
                return {
                    code: error && typeof error === "object" && "code" in error ? String(error.code) : "",
                    message: error instanceof Error ? error.message : String(error),
                };
            }
        },
        { timeoutVaultId: vaultId, timeoutOobiUrl: shortTimeoutOobiUrl.toString() },
    );
    expect(timeoutFailure).toEqual({
        code: "TIMEOUT",
        message: "Runtime request timed out for remotes.resolveOobi.",
    });
    await timedOutWorkerClosed;
    page.off("worker", trackTimeoutWorker);
    expect(workersCreatedDuringTimeout).toEqual([]);

    const postTimeoutWorkerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    const listedAfterTimeout = await request("vaults.list");
    const postTimeoutWorker = await postTimeoutWorkerStarted;
    expect(listedAfterTimeout.vaults).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: vaultId, alias: vaultName }),
    ]));

    let postTimeoutWorkerClosedUnexpectedly = false;
    postTimeoutWorker.on("close", () => {
        postTimeoutWorkerClosedUnexpectedly = true;
    });
    const staleTimeoutFailure = await page.evaluate(async () => {
        const scope = window as typeof window & {
            webbaserStaleTimeoutRequest?: Promise<{ code: string; message: string } | null>;
        };
        return await scope.webbaserStaleTimeoutRequest;
    });
    expect(staleTimeoutFailure).toEqual({
        code: "TIMEOUT",
        message: "Runtime request timed out for remotes.resolveOobi.",
    });
    await waitForDelayedServerOutcomes([staleTimeoutOobiUrl.toString()]);
    expect(observedRequests.filter((url) => url === shortTimeoutOobiUrl.toString())).toHaveLength(0);
    expect(observedRequests.filter((url) => url === staleTimeoutOobiUrl.toString())).toHaveLength(1);
    expect(postTimeoutWorkerClosedUnexpectedly).toBe(false);
    expect(await request("vaults.list")).toEqual(listedAfterTimeout);
    await request("vaults.open", { vaultId, passcode: "" });
    const remoteAfterLateResponse = await request("remotes.get", {
        vaultId,
        aid: "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW",
    });
    expect(remoteAfterLateResponse.remote).toEqual(recovered.remote);
    await request("vaults.close", { vaultId });

    const postTimeoutWorkerClosed = postTimeoutWorker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });
    await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        harness.destroy();
    });
    await postTimeoutWorkerClosed;

    expect(observedRequests).toContain(new URL("/fortweb/app/runtime/wallet-worker.py", baseURL).toString());
    expect(observedRequests).toContain(new URL(PACKAGE_MANIFEST_PATH, baseURL).href);
    for (const wheel of MANIFEST.wheels) {
        expect(observedRequests).toContain(manifestArtifactUrl(wheel.filename, baseURL!));
    }
    const oobiResponses = observedResponses.filter(({ url }) => url.startsWith(oobiUrl.origin + oobiUrl.pathname));
    expect(oobiResponses).toContainEqual(expect.objectContaining({ status: 200 }));

    const expectedOrigin = new URL(baseURL!).origin;
    const externalRequests = observedRequests.filter((url) => (
        (url.startsWith("http://") || url.startsWith("https://")) && new URL(url).origin !== expectedOrigin
    ));
    expect(externalRequests, JSON.stringify(externalRequests, null, 2)).toEqual([]);
    expect(observedRequests.some((url) => /0\.29\.3|cp313|pyodide_2025_0|hio_web|keri_web|pychloride/.test(url))).toBe(false);

    const closures = parseClosureMessages(nativeMessages);
    expect(closures).toHaveLength(4);
    expect(new Set(closures.map(({ worker_id }) => worker_id)).size).toBe(4);
    for (const closure of closures) {
        assertClosure(closure.report);
    }
    expect(nativeMessages.filter(({ message }) => message.includes("event=worker_preload_complete"))).toHaveLength(4);
    const productionWorkerUrls = [productionWorker, restartedWorker, replacementWorker, postTimeoutWorker]
        .map((worker) => worker.url());
    const network = await networkRecorder.finish([staleTimeoutOobiUrl.toString()]);
    writeEvidence("production-proof.json", {
        ready_worker_count: 4,
        terminated_worker_count: 4,
        worker_ids: closures.map(({ worker_id }) => worker_id),
        worker_urls: productionWorkerUrls,
        closures,
        readiness_count: 4,
        native_messages: nativeMessages,
        settings_boundary: {
            vaultless_result: vaultlessSettings,
            indexed_db_names_before: indexedDbNamesBefore,
            indexed_db_names_after: indexedDbNamesAfter,
            locked_vault_id: vaultId,
            locked_outcome: lockedSettingsOutcome,
        },
        timeout_probes: {
            short: {
                url: shortTimeoutOobiUrl.toString(),
                timeout_ms: 50,
                outcome: timeoutFailure,
                network_request_count: 0,
            },
            stale: {
                url: staleTimeoutOobiUrl.toString(),
                timeout_ms: 5_000,
                outcome: staleTimeoutFailure,
                network_request_count: 1,
            },
        },
        assertions: {
            vaultless_settings_before_vault: true,
            vaultless_settings_storage_unchanged: true,
            locked_settings_rejected: true,
            reload_recovered: true,
            visibility_replacement: true,
            timeout_replacement: true,
            stale_request_isolated: true,
            late_response_state_unchanged: true,
            timed_out_mutation_not_retried: true,
        },
    });
    writeEvidence("production-requests.json", {
        ready_worker_count: 4,
        network,
    });
});

test("@smoke WebBaser production worker rejects an invalid forwarded runtime-origin contract", async ({ page, baseURL }) => {
    test.setTimeout(120_000);
    expect(baseURL).toBeTruthy();
    const networkRecorder = captureNetwork(page, baseURL!);
    const nativeMessages: NativeMessage[] = [];
    await page.exposeFunction("captureRuntimeNativeMessage", (payload: NativeMessage) => {
        nativeMessages.push(payload);
    });

    const workerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    const fixtureUrl = new URL(PRODUCTION_HARNESS_PATH, baseURL);
    fixtureUrl.searchParams.set("invalidRuntimeContract", "1");
    await page.goto(fixtureUrl.toString());
    await page.waitForFunction(() => (
        window as typeof window & { webbaserProductionHarnessReady?: boolean }
    ).webbaserProductionHarnessReady === true);
    const worker = await workerStarted;
    const workerClosed = worker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });

    const failure = await page.evaluate(async () => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        try {
            await harness.request("vaults.list");
            return null;
        } catch (error) {
            return {
                code: error && typeof error === "object" && "code" in error ? String(error.code) : "",
                message: error instanceof Error ? error.message : String(error),
            };
        }
    });
    expect(failure).toEqual({
        code: "BAD_CONFIG",
        message: "Runtime origin contract was invalid.",
    });

    const diagnostics = await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        return harness.diagnostics();
    });
    expect(diagnostics).toEqual(expect.arrayContaining([
        expect.objectContaining({
            type: "log",
            message: expect.stringContaining("event=worker_preload_failed"),
        }),
    ]));

    await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        harness.destroy();
    });
    await workerClosed;

    expect(nativeMessages.some(({ message }) => message.includes("event=worker_preload_complete"))).toBe(false);
    expect(nativeMessages.filter(({ message }) => message.includes("event=worker_preload_failed"))).toHaveLength(1);
    const network = await networkRecorder.finish();
    const manifestUrl = new URL(PACKAGE_MANIFEST_PATH, baseURL!).href;
    const wheelUrls = new Set(MANIFEST.wheels.map(({ filename }) => manifestArtifactUrl(filename, baseURL!)));
    expect(network.requests.filter(({ url }) => url === manifestUrl || wheelUrls.has(url))).toEqual([]);
    writeEvidence("invalid-origin-proof.json", {
        failed_worker_count: 1,
        terminated_worker_count: 1,
        worker_url: worker.url(),
        failure,
        native_messages: nativeMessages,
        network,
    });
});

test("@smoke WebBaser production worker reports a package preload failure without retrying", async ({ page, baseURL }) => {
    test.setTimeout(120_000);
    expect(baseURL).toBeTruthy();
    const networkRecorder = captureNetwork(page, baseURL!);
    const nativeMessages: NativeMessage[] = [];
    await page.exposeFunction("captureRuntimeNativeMessage", (payload: NativeMessage) => {
        nativeMessages.push(payload);
    });

    const workerStarted = page.waitForEvent("worker", { timeout: PHASE_TIMEOUT_MS });
    const fixtureUrl = new URL(PRODUCTION_HARNESS_PATH, baseURL);
    fixtureUrl.searchParams.set("preloadFailure", "1");
    await page.goto(fixtureUrl.toString());
    await page.waitForFunction(() => (
        window as typeof window & { webbaserProductionHarnessReady?: boolean }
    ).webbaserProductionHarnessReady === true);
    const worker = await workerStarted;
    const workerClosed = worker.waitForEvent("close", { timeout: PHASE_TIMEOUT_MS });

    const failure = await page.evaluate(async () => {
        const startedAt = performance.now();
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        try {
            await harness.request("vaults.list");
            return null;
        } catch (error) {
            return {
                code: error && typeof error === "object" && "code" in error ? String(error.code) : "",
                elapsedMs: Math.round(performance.now() - startedAt),
                message: error instanceof Error ? error.message : String(error),
            };
        }
    });
    expect(failure).toEqual({
        code: "RUNTIME_ERROR",
        elapsedMs: expect.any(Number),
        message: "Runtime worker preload failed.",
    });
    expect(failure!.elapsedMs).toBeLessThan(30_000);

    await page.evaluate(() => {
        const harness = (
            window as typeof window & { webbaserProductionHarness: ProductionHarness }
        ).webbaserProductionHarness;
        harness.destroy();
    });
    await workerClosed;

    expect(nativeMessages.some(({ message }) => message.includes("event=worker_preload_complete"))).toBe(false);
    expect(nativeMessages.filter(({ message }) => message.includes("event=worker_preload_failed"))).toHaveLength(1);
    const network = await networkRecorder.finish();
    const manifestUrl = new URL(PACKAGE_MANIFEST_PATH, baseURL!).href;
    expect(network.requests.filter(({ url }) => url === manifestUrl)).toHaveLength(1);
    const wheelUrls = new Set(MANIFEST.wheels.map(({ filename }) => manifestArtifactUrl(filename, baseURL!)));
    expect(network.requests.filter(({ url }) => wheelUrls.has(url))).toEqual([]);
    writeEvidence("preload-failure-proof.json", {
        failure,
        native_messages: nativeMessages,
        network,
        worker_url: worker.url(),
    });
});
