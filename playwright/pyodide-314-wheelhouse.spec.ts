import { expect, test, type Page, type Worker } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";


type DistributionReport = {
    name: string;
    version: string;
    requires: string[];
    files: string[];
};

type ProbeReport = {
    ok: boolean;
    python_version: string;
    python_full: string;
    platform: string;
    sysconfig_platform: string;
    abi: string;
    tags: string[];
    installed_distributions: Record<string, DistributionReport>;
    dependency_edges: Array<Record<string, string>>;
    exclusions: Array<Record<string, string>>;
    module_paths: Record<string, string>;
    blake3: { empty_digest: string };
    msgpack: { vector: string; implementation: string };
    cbor2: { canonical_vector: string };
    pysodium: {
        distribution_version: string;
        libsodium_version: string;
        signature_verified: boolean;
        modified_message_rejected: boolean;
        argon2id: {
            outlen: number;
            password_type: string;
            salt: string;
            opslimit: number;
            memlimit: number;
            output_sha256: string;
        };
    };
    cryptography: {
        version: string;
        openssl: string;
        curves: Record<string, { iterations: number; compressed_public_key: string }>;
    };
    lmdb_absent: boolean;
    stale_files: string[];
};

type WorkerResult = {
    ok: boolean;
    report?: ProbeReport;
    error?: string;
    stack?: string;
};

type Manifest = {
    runtime: { pyodide: string; python: string; emscripten: string; abi: string; core_files: string[] };
    install_order: string[];
    wheels: Array<{ filename: string; normalized_name: string; version: string; sha256: string; bytes: number }>;
    files: Array<{ path: string; sha256: string; bytes: number }>;
};

type RequestRecord = {
    sequence: number;
    method: string;
    resourceType: string;
    url: string;
};

type ResponseRecord = {
    sequence: number;
    method: string;
    url: string;
    status: number;
    bytes: number;
    sha256: string;
};

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE_PATH = "/fortweb/_wheelhouse-test/fixture.html";
const WORKER_PATH = "/fortweb/_wheelhouse-test/worker.mjs";
const BUILD_PATH = "/fortweb/_wheelhouse-test/build/";
const WHEELHOUSE_MANIFEST_SHA256 = process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256 ?? "";
const TIMEOUT_MS = 900_000;

function requireArtifactDirectory(): string {
    const value = process.env.FORTWEB_WHEELHOUSE_ARTIFACT_DIR;
    if (!value) {
        throw new Error("FORTWEB_WHEELHOUSE_ARTIFACT_DIR is required");
    }
    return path.resolve(value);
}

function requireBuildDirectory(): string {
    const value = process.env.WHEELHOUSE_BUILD_DIR;
    if (!value) {
        throw new Error("WHEELHOUSE_BUILD_DIR is required");
    }
    const resolved = path.resolve(value);
    const relative = path.relative(REPO, resolved);
    if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
        throw new Error(`WHEELHOUSE_BUILD_DIR must remain inside the repository: ${resolved}`);
    }
    return resolved;
}

function evidenceIdentity(): { run_id: string; source_identity_sha256: string } {
    const runId = process.env.FORTWEB_RUN_ID ?? "";
    const identityPath = process.env.FORTWEB_SOURCE_IDENTITY ?? "";
    if (!runId || !identityPath) {
        throw new Error("FORTWEB_RUN_ID and FORTWEB_SOURCE_IDENTITY are required");
    }
    return {
        run_id: runId,
        source_identity_sha256: createHash("sha256").update(readFileSync(identityPath)).digest("hex"),
    };
}

async function terminateWorker(page: Page, worker: Worker): Promise<void> {
    const closed = worker.waitForEvent("close", { timeout: 30_000 });
    await page.evaluate(() => {
        const target = (window as typeof window & { wheelhouseWorker?: globalThis.Worker }).wheelhouseWorker;
        target?.terminate();
        delete (window as typeof window & { wheelhouseWorker?: globalThis.Worker }).wheelhouseWorker;
    });
    await closed;
}

test("@smoke Wheelhouse loads the exact Python 3.14 wheelhouse in a clean worker", async ({ page, context, baseURL }) => {
    test.setTimeout(TIMEOUT_MS);
    expect(baseURL).toBeTruthy();
    expect(WHEELHOUSE_MANIFEST_SHA256).toMatch(/^[0-9a-f]{64}$/);

    const artifactDirectory = requireArtifactDirectory();
    const buildDirectory = requireBuildDirectory();
    const evidence = evidenceIdentity();
    const manifestBytes = readFileSync(path.join(buildDirectory, "manifest.json"));
    expect(createHash("sha256").update(manifestBytes).digest("hex")).toBe(WHEELHOUSE_MANIFEST_SHA256);
    const manifest = JSON.parse(manifestBytes.toString("utf8")) as Manifest;
    const buildBase = new URL(BUILD_PATH, baseURL!);
    const expectedOrigin = new URL(baseURL!).origin;
    const expectedRequestUrls = new Set([
        new URL(FIXTURE_PATH, baseURL).href,
        new URL(WORKER_PATH, baseURL).href,
        new URL("manifest.json", buildBase).href,
        ...manifest.runtime.core_files.map((coreFile) => new URL(`runtime/${coreFile}`, buildBase).href),
        ...manifest.wheels.map((wheel) => new URL(`wheelhouse/${wheel.filename}`, buildBase).href),
    ]);
    const requests: RequestRecord[] = [];
    const responses: ResponseRecord[] = [];
    const responseTasks: Promise<void>[] = [];
    const blockedRequests: string[] = [];
    const consoleLines: string[] = [];
    let worker: Worker | undefined;
    let workerResult: WorkerResult = { ok: false, error: "worker did not return" };
    let responseSequence = 0;

    mkdirSync(artifactDirectory, { recursive: true });
    await context.route("**/*", async (route) => {
        const request = route.request();
        const url = request.url();
        requests.push({
            sequence: requests.length + 1,
            method: request.method(),
            resourceType: request.resourceType(),
            url,
        });
        if (url.startsWith("data:") || url.startsWith("blob:") || new URL(url).origin === expectedOrigin) {
            await route.continue();
            return;
        }
        blockedRequests.push(url);
        await route.abort("blockedbyclient");
    });
    page.on("response", (response) => {
        const sequence = ++responseSequence;
        responseTasks.push((async () => {
            const body = await response.body();
            responses.push({
                sequence,
                method: response.request().method(),
                url: response.url(),
                status: response.status(),
                bytes: body.byteLength,
                sha256: createHash("sha256").update(body).digest("hex"),
            });
        })());
    });
    page.on("console", (message) => consoleLines.push(`${message.type()}: ${message.text()}`));
    page.on("pageerror", (error) => consoleLines.push(`pageerror: ${error.stack ?? error.message}`));

    try {
        await page.goto(new URL(FIXTURE_PATH, baseURL).href);
        const observedWorker = page.waitForEvent("worker", { timeout: 30_000 });
        const resultPromise = page.evaluate(
            async ({ workerPath, buildBaseUrl }) => {
                const target = new Worker(workerPath, { type: "module" });
                (window as typeof window & { wheelhouseWorker?: globalThis.Worker }).wheelhouseWorker = target;
                return new Promise<WorkerResult>((resolve, reject) => {
                    const timer = window.setTimeout(() => reject(new Error("Wheelhouse worker timed out")), 840_000);
                    target.addEventListener("message", (event: MessageEvent<WorkerResult>) => {
                        window.clearTimeout(timer);
                        resolve(event.data);
                    }, { once: true });
                    target.addEventListener("error", (event) => {
                        window.clearTimeout(timer);
                        reject(new Error(event.message));
                    }, { once: true });
                    target.postMessage({ type: "start", buildBase: buildBaseUrl });
                });
            },
            { workerPath: WORKER_PATH, buildBaseUrl: buildBase.href },
        );
        worker = await observedWorker;
        workerResult = await resultPromise;
        expect(workerResult.ok, `${workerResult.error ?? ""}\n${workerResult.stack ?? ""}\n${consoleLines.join("\n")}`).toBe(true);
        const report = workerResult.report!;
        expect(report.ok).toBe(true);
        expect(report.python_version).toBe("3.14.2");
        expect(report.platform).toBe("emscripten");
        expect(report.abi).toBe("pyemscripten_2026_0_wasm32");
        expect(report.tags).toContain("cp314-cp314-pyemscripten_2026_0_wasm32");
        expect(report.lmdb_absent).toBe(true);
        expect(report.stale_files).toEqual([]);
        expect(report.blake3.empty_digest).toBe("af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262");
        expect(report.msgpack).toEqual({ vector: "81a16101", implementation: "msgpack.fallback" });
        expect(report.cbor2.canonical_vector).toBe("a2616102616201");
        expect(report.pysodium).toEqual(expect.objectContaining({
            distribution_version: "0.7.18",
            libsodium_version: "1.0.22",
            signature_verified: true,
            modified_message_rejected: true,
        }));
        expect(report.pysodium.argon2id).toEqual(expect.objectContaining({
            outlen: 16,
            password_type: "str",
            salt: "NHCtv3Actrddf8jC",
            opslimit: 2,
            memlimit: 67_108_864,
        }));
        expect(report.cryptography.version).toBe("50.0.0");
        expect(report.cryptography.openssl).toContain("OpenSSL 3.6.2");
        expect(report.cryptography.curves.p256).toEqual({
            iterations: 50,
            compressed_public_key: "036b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296",
        });
        expect(report.cryptography.curves.secp256k1).toEqual({
            iterations: 50,
            compressed_public_key: "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
        });
        expect(report.installed_distributions.pysodium.version).toBe("0.7.18");
        expect(report.installed_distributions.pychloride).toBeUndefined();
        for (const wheel of manifest.wheels) {
            expect(report.installed_distributions[wheel.normalized_name]?.version).toBe(wheel.version);
        }
        expect(blockedRequests).toEqual([]);
        expect(requests.every((request) => request.method === "GET")).toBe(true);
        expect(requests).toHaveLength(expectedRequestUrls.size);
        expect([...new Set(requests.map((request) => request.url))].sort()).toEqual([...expectedRequestUrls].sort());
        expect(requests.some((request) => request.url.includes("0.29.3") || request.url.includes("cp313") || request.url.includes("pyodide_2025_0") || request.url.includes("hio_web") || request.url.includes("keri_web") || request.url.includes("pychloride"))).toBe(false);
        await Promise.all(responseTasks);
        responses.sort((left, right) => left.sequence - right.sequence);
        expect(responses).toHaveLength(expectedRequestUrls.size);

        const expectedBodies = new Map<string, Buffer>();
        const acceptedBody = (relative: string): Buffer => {
            const rows = manifest.files.filter(({ path: manifestPath }) => manifestPath === relative);
            expect(rows).toHaveLength(1);
            const body = readFileSync(path.join(buildDirectory, ...relative.split("/")));
            expect(body.byteLength).toBe(rows[0].bytes);
            expect(createHash("sha256").update(body).digest("hex")).toBe(rows[0].sha256);
            return body;
        };
        expectedBodies.set(new URL(FIXTURE_PATH, baseURL).href, readFileSync(path.join(REPO, "ci/fixtures/pyodide-314-wheelhouse.html")));
        expectedBodies.set(new URL(WORKER_PATH, baseURL).href, readFileSync(path.join(REPO, "ci/fixtures/pyodide-314-wheelhouse-worker.mjs")));
        expectedBodies.set(new URL("manifest.json", buildBase).href, manifestBytes);
        for (const filename of manifest.runtime.core_files) {
            expectedBodies.set(
                new URL(`runtime/${filename}`, buildBase).href,
                acceptedBody(`runtime/${filename}`),
            );
        }
        for (const filename of manifest.install_order) {
            expectedBodies.set(
                new URL(`wheelhouse/${filename}`, buildBase).href,
                acceptedBody(`wheelhouse/${filename}`),
            );
        }
        expect(new Set(responses.map(({ url }) => url))).toEqual(new Set(expectedBodies.keys()));
        for (const [url, body] of expectedBodies) {
            expect(responses.filter((row) => row.url === url)).toEqual([
                expect.objectContaining({
                    method: "GET",
                    status: 200,
                    bytes: body.byteLength,
                    sha256: createHash("sha256").update(body).digest("hex"),
                }),
            ]);
        }
    } finally {
        if (worker) {
            await terminateWorker(page, worker).catch((error) => consoleLines.push(`terminate-error: ${String(error)}`));
        }
        await Promise.all(responseTasks).catch(() => {});
        responses.sort((left, right) => left.sequence - right.sequence);
        const envelope = {
            schema: 1,
            command_identity: "playwright:runtime-wheelhouse",
            test_count: 1,
            ...evidence,
        };
        writeFileSync(path.join(artifactDirectory, "requests.json"), JSON.stringify({
            ...envelope,
            requests,
            responses,
            blocked: blockedRequests,
        }, null, 2) + "\n");
        writeFileSync(path.join(artifactDirectory, "console.log"), `${consoleLines.join("\n")}\n`);
        writeFileSync(path.join(artifactDirectory, "probe-report.json"), JSON.stringify({
            ...envelope,
            ...workerResult,
        }, null, 2) + "\n");
        writeFileSync(
            path.join(artifactDirectory, "installed-distributions.json"),
            JSON.stringify({
                ...envelope,
                installed_distributions: workerResult.report?.installed_distributions ?? null,
                error: workerResult.error ?? null,
            }, null, 2) + "\n",
        );
    }
});
