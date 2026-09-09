import { expect, test, type Page, type Request } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";


type NativeMessage = { type: string; timestamp?: string; message: string };
type RuntimeClosure = {
    schema: number;
    runtime: {
        pyodide: string;
        python: string;
        emscripten: string;
        abi: string;
        core_files: string[];
    };
    files: Array<{ path: string; sha256: string; bytes: number }>;
    wheels: Array<{
        filename: string;
        normalized_name: string;
        version: string;
        sha256: string;
        bytes: number;
    }>;
    install_order: string[];
    dependency_exclusions: Array<{ owner: string; requirement: string }>;
};
type CanonicalInventory = {
    schema: number;
    files: Array<{ path: string; sha256: string; bytes: number }>;
    aggregate_sha256: string;
};
type PackageReport = {
    manifest_url: string;
    manifest_sha256: string;
    runtime: RuntimeClosure["runtime"];
    actual_runtime: {
        pyodide: string;
        python: string;
        platform: string;
        sysconfig_platform: string;
        tags: string[];
    };
    install_order: string[];
    installed_distributions: Record<string, { name: string; version: string; requires: string[] }>;
    dependency_edges: Array<{ owner: string; requirement: string; selected: string }>;
    exclusions: Array<{ owner: string; requirement: string }>;
    module_paths: Record<string, string>;
    forbidden_imports: string[];
    closure_sha256: string;
};
type RequestRow = {
    sequence: number;
    method: string;
    resource_type: string;
    url: string;
};
type ResponseRow = {
    sequence: number;
    request_sequence: number;
    method: string;
    url: string;
    status: number;
    bytes: number;
    sha256: string;
};

const RUNTIME_ROOT = path.resolve(process.env["FORTWEB_RUNTIME_DIR"] ?? "dist/runtime");
const CANONICAL_INVENTORY_PATH = process.env["FORTWEB_CANONICAL_INVENTORY"];
if (!CANONICAL_INVENTORY_PATH) {
    throw new Error("FORTWEB_CANONICAL_INVENTORY is required");
}
const CANONICAL_INVENTORY = JSON.parse(
    readFileSync(CANONICAL_INVENTORY_PATH, "utf8"),
) as CanonicalInventory;
if (CANONICAL_INVENTORY.schema !== 1 || !Array.isArray(CANONICAL_INVENTORY.files)) {
    throw new Error("Runtime canonical inventory is invalid");
}
const CANONICAL_FILES = new Map(CANONICAL_INVENTORY.files.map((row) => [`/fortweb/${row.path}`, row]));
const CLOSURE_BYTES = readFileSync(path.join(RUNTIME_ROOT, "runtime-closure.json"));
const CLOSURE = JSON.parse(CLOSURE_BYTES.toString("utf8")) as RuntimeClosure;
const CLOSURE_SHA256 = createHash("sha256").update(CLOSURE_BYTES).digest("hex");
const CONFIG_TEXT = readFileSync(path.join(RUNTIME_ROOT, "pyscript-ci.toml"), "utf8");
const EXPECTED_DISTRIBUTIONS = Object.fromEntries(
    CLOSURE.wheels.map(({ normalized_name, version }) => [normalized_name, version]),
);
const EXPECTED_CLOSURE_FILES = new Map(CLOSURE.files.map((row) => [`/fortweb/${row.path}`, row]));
const CLOSURE_INVENTORY_ROW = CANONICAL_FILES.get("/fortweb/runtime-closure.json");
if (
    !CLOSURE_INVENTORY_ROW
    || CLOSURE_INVENTORY_ROW.bytes !== CLOSURE_BYTES.byteLength
    || CLOSURE_INVENTORY_ROW.sha256 !== CLOSURE_SHA256
) {
    throw new Error("Runtime closure does not match the canonical inventory");
}

function requireEvidence(): {
    artifactDirectory: string;
    envelope: { schema: number; run_id: string; source_identity_sha256: string };
} {
    const artifactDirectory = process.env["FORTWEB_CANARY_ARTIFACT_DIR"] ?? "";
    const runId = process.env["FORTWEB_RUN_ID"] ?? "";
    const sourceIdentity = process.env["FORTWEB_SOURCE_IDENTITY"] ?? "";
    if (!artifactDirectory || !runId || !sourceIdentity) {
        throw new Error("Runtime canary evidence requires artifact directory, run ID, and source identity");
    }
    return {
        artifactDirectory: path.resolve(artifactDirectory),
        envelope: {
            schema: 1,
            command_identity: "playwright:runtime-isolated-canary",
            run_id: runId,
            source_identity_sha256: createHash("sha256").update(readFileSync(sourceIdentity)).digest("hex"),
        },
    };
}

function decodeClosure(messages: NativeMessage[]): { index: number; report: PackageReport } {
    const matches = messages.flatMap((payload, index) => {
        if (!payload.message.includes("event=worker_package_closure")) {
            return [];
        }
        const encoded = payload.message.match(/report_b64="([A-Za-z0-9+/=]+)"/)?.[1];
        expect(encoded, payload.message).toBeTruthy();
        return [{
            index,
            report: JSON.parse(Buffer.from(encoded!, "base64").toString("utf8")) as PackageReport,
        }];
    });
    expect(matches).toHaveLength(1);
    return matches[0];
}

function captureNetwork(page: Page): {
    requests: RequestRow[];
    responses: ResponseRow[];
    finish(): Promise<void>;
} {
    const requests: RequestRow[] = [];
    const responses: ResponseRow[] = [];
    const failures: Array<{ request_sequence: number; url: string; error_text: string }> = [];
    const requestSequences = new Map<Request, number>();
    const tasks: Promise<void>[] = [];
    let responseSequence = 0;
    page.on("request", (request) => {
        const sequence = requests.length + 1;
        requestSequences.set(request, sequence);
        requests.push({
            sequence,
            method: request.method(),
            resource_type: request.resourceType(),
            url: request.url(),
        });
    });
    page.on("requestfailed", (request) => {
        const requestSequence = requestSequences.get(request);
        if (requestSequence === undefined) {
            throw new Error(`failed canary request has no captured sequence: ${request.url()}`);
        }
        failures.push({
            request_sequence: requestSequence,
            url: request.url(),
            error_text: request.failure()?.errorText ?? "",
        });
    });
    page.on("response", (response) => {
        const requestSequence = requestSequences.get(response.request());
        if (requestSequence === undefined) {
            throw new Error(`canary response has no captured sequence: ${response.url()}`);
        }
        const sequence = ++responseSequence;
        tasks.push((async () => {
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
        requests,
        responses,
        async finish() {
            const deadline = Date.now() + 15_000;
            while (true) {
                await Promise.all([...tasks]);
                const httpRequests = requests.filter(
                    ({ url }) => ["http:", "https:"].includes(new URL(url).protocol),
                );
                const responseCounts = new Map<number, number>();
                for (const { request_sequence: sequence, url } of responses) {
                    if (!["http:", "https:"].includes(new URL(url).protocol)) {
                        continue;
                    }
                    responseCounts.set(sequence, (responseCounts.get(sequence) ?? 0) + 1);
                }
                const httpFailures = failures.filter(
                    ({ url }) => ["http:", "https:"].includes(new URL(url).protocol),
                );
                if (
                    httpFailures.length === 0
                    && httpRequests.every(({ sequence }) => responseCounts.get(sequence) === 1)
                    && responses.filter(
                        ({ url }) => ["http:", "https:"].includes(new URL(url).protocol),
                    ).length === httpRequests.length
                ) {
                    break;
                }
                if (Date.now() >= deadline) {
                    throw new Error(
                        `canary HTTP requests did not reach one response: ${JSON.stringify({ httpRequests, responses, httpFailures })}`,
                    );
                }
                await new Promise((resolve) => setTimeout(resolve, 25));
            }
            responses.sort((left, right) => left.sequence - right.sequence);
        },
    };
}

function writeJson(
    directory: string,
    filename: string,
    envelope: Record<string, unknown>,
    payload: Record<string, unknown>,
): void {
    mkdirSync(directory, { recursive: true });
    writeFileSync(path.join(directory, filename), JSON.stringify({ ...envelope, ...payload }, null, 2) + "\n");
}

test("Runtime boots the production worker from only the canonical runtime tree", async ({ page, baseURL }) => {
    test.setTimeout(900_000);
    expect(baseURL).toBeTruthy();
    const { artifactDirectory, envelope } = requireEvidence();
    const nativeMessages: NativeMessage[] = [];
    const consoleRows: Array<{ type: string; text: string }> = [];
    const pageErrors: string[] = [];
    const network = captureNetwork(page);
    let runtimeReport: Record<string, unknown> = { ok: false, error: "canary did not finish" };
    let sourceReport: Record<string, unknown> = { ok: false, error: "canary did not finish" };

    await page.addInitScript(() => {
        const scope = window as typeof window & { __runtimeCanaryMessages?: NativeMessage[] };
        scope.__runtimeCanaryMessages = [];
        window.webkit = {
            messageHandlers: {
                bridge: {
                    postMessage(payload: NativeMessage) {
                        scope.__runtimeCanaryMessages!.push(JSON.parse(JSON.stringify(payload)) as NativeMessage);
                    },
                },
            },
        };
    });
    page.on("console", (message) => consoleRows.push({ type: message.type(), text: message.text() }));
    page.on("pageerror", (error) => pageErrors.push(error.stack ?? error.message));

    try {
        await page.goto(new URL("/fortweb/app/index.html", baseURL).href);
        await page.waitForFunction(() => (
            (window as typeof window & { __runtimeCanaryMessages?: NativeMessage[] })
                .__runtimeCanaryMessages?.some(({ message }) => message.includes("event=worker_preload_complete"))
        ), undefined, { timeout: 840_000 });
        await page.waitForFunction(() => (
            (window as typeof window & { __runtimeCanaryMessages?: NativeMessage[] })
                .__runtimeCanaryMessages?.some(({ message }) => (
                    message.includes("event=request_end")
                    && message.includes('method="vaults.list"')
                    && message.includes('outcome="ok"')
                ))
        ), undefined, { timeout: 60_000 });
        nativeMessages.push(...await page.evaluate(() => (
            (window as typeof window & { __runtimeCanaryMessages?: NativeMessage[] })
                .__runtimeCanaryMessages ?? []
        )));
        await page.waitForLoadState("networkidle");
        await network.finish();

        const closureMessage = decodeClosure(nativeMessages);
        const readyIndexes = nativeMessages.flatMap(({ message }, index) => (
            message.includes("event=worker_preload_complete") ? [index] : []
        ));
        const successfulRequests = nativeMessages.flatMap(({ message }, index) => (
            message.includes("event=request_end") && message.includes('outcome="ok"')
                ? [{ index, message }]
                : []
        ));
        expect(readyIndexes).toHaveLength(1);
        expect(successfulRequests.length).toBeGreaterThan(0);
        expect(successfulRequests[0].message).toContain('method="vaults.list"');
        expect(closureMessage.index).toBeLessThan(readyIndexes[0]);
        expect(readyIndexes[0]).toBeLessThan(successfulRequests[0].index);

        const report = closureMessage.report;
        expect(report.manifest_url).toBe(new URL("/fortweb/runtime-closure.json", baseURL).href);
        expect(report.manifest_sha256).toBe(CLOSURE_SHA256);
        expect(report.runtime).toEqual(CLOSURE.runtime);
        expect(report.actual_runtime).toEqual(expect.objectContaining({
            pyodide: "314.0.5",
            python: "3.14.2",
            platform: "emscripten",
            sysconfig_platform: "emscripten-5.0.3-wasm32",
        }));
        expect(report.actual_runtime.tags).toContain("cp314-cp314-pyemscripten_2026_0_wasm32");
        expect(report.install_order).toEqual(CLOSURE.install_order);
        expect(Object.fromEntries(
            Object.entries(report.installed_distributions).map(([name, row]) => [name, row.version]),
        )).toEqual(EXPECTED_DISTRIBUTIONS);
        expect(report.dependency_edges).toHaveLength(43);
        expect(report.exclusions).toEqual(CLOSURE.dependency_exclusions);
        expect(report.forbidden_imports).toEqual([]);
        expect(Object.values(report.module_paths).every((modulePath) => modulePath.includes("/site-packages/"))).toBe(true);

        expect(CONFIG_TEXT).toContain(`sha256 = "${CLOSURE_SHA256}"`);
        const origin = new URL(baseURL!).origin;
        expect(network.requests.every(({ method }) => method === "GET")).toBe(true);
        expect(network.requests.every(({ url }) => {
            const parsed = new URL(url);
            return parsed.origin === origin && (
                parsed.protocol === "blob:"
                || (parsed.protocol === "http:" && parsed.pathname.startsWith("/fortweb/"))
            );
        })).toBe(true);
        expect(network.requests.some(({ url }) => /\.codex|0\.29\.3|cp313-cp313|pyodide_2025_0|hio_web|keri_web|pychloride/.test(url))).toBe(false);

        const manifestUrl = new URL("/fortweb/runtime-closure.json", baseURL).href;
        expect(network.requests.filter(({ url }) => url === manifestUrl)).toHaveLength(1);
        const expectedCoreUrls = new Set(CLOSURE.runtime.core_files.map(
            (filename) => new URL(`/fortweb/vendor/pyodide/314.0.5/${filename}`, baseURL).href,
        ));
        const runtimeArtifactRequests = network.requests.filter(({ url }) => (
            new URL(url).pathname.startsWith("/fortweb/vendor/pyodide/314.0.5/")
        ));
        const coreRequests = runtimeArtifactRequests.filter(({ url }) => expectedCoreUrls.has(url));
        expect(coreRequests).toHaveLength(5);
        expect(runtimeArtifactRequests).toHaveLength(5);
        expect(new Set(coreRequests.map(({ url }) => url))).toEqual(expectedCoreUrls);
        const expectedWheelUrls = CLOSURE.install_order.map(
            (filename) => new URL(`/fortweb/wheels/${filename}`, baseURL).href,
        );
        const wheelRequests = network.requests.filter(({ url }) => url.endsWith(".whl"));
        expect(wheelRequests.map(({ url }) => url)).toEqual(expectedWheelUrls);

        const responseByUrl = new Map(network.responses.map((row) => [row.url, row]));
        expect(network.responses.filter(({ url }) => url === manifestUrl)).toEqual([
            expect.objectContaining({
                status: 200,
                bytes: CLOSURE_BYTES.byteLength,
                sha256: CLOSURE_SHA256,
            }),
        ]);
        for (const [pathname, expected] of EXPECTED_CLOSURE_FILES) {
            const url = new URL(pathname, baseURL).href;
            const response = responseByUrl.get(url);
            expect(network.responses.filter((row) => row.url === url)).toHaveLength(1);
            expect(response, url).toEqual(expect.objectContaining({
                status: 200,
                bytes: expected.bytes,
                sha256: expected.sha256,
            }));
        }
        for (const response of network.responses) {
            const parsed = new URL(response.url);
            if (parsed.protocol === "blob:") {
                continue;
            }
            const expected = CANONICAL_FILES.get(parsed.pathname);
            expect(expected, parsed.pathname).toBeTruthy();
            expect(response).toEqual(expect.objectContaining({
                status: 200,
                bytes: expected!.bytes,
                sha256: expected!.sha256,
            }));
        }
        expect(consoleRows.filter(({ type }) => type === "error").map(({ text }) => text)).toEqual([
            expect.stringMatching(/^\/lib\/python3\.14\/site-packages\/hio\/help\/doming\.py:364: SyntaxWarning:/),
            "  _update(\\*pa, \\*\\*kwa): update attributes using dict like update syntax",
            expect.stringMatching(/^\/lib\/python3\.14\/site-packages\/hio\/help\/doming\.py:634: SyntaxWarning:/),
            "  _update(\\*pa, \\*\\*kwa): update attributes using dict like update syntax",
        ]);
        expect(pageErrors).toEqual([]);

        const forbiddenProbes = [];
        for (const pathname of [
            "/fortweb/app/",
            "/fortweb/.codex/secret",
            "/_runtime-test/python/run_webbaser_lifecycle.py",
            "/fortweb/no-source-fallback",
        ]) {
            const response = await fetch(new URL(pathname, baseURL!));
            forbiddenProbes.push({ pathname, status: response.status, body: await response.text() });
        }
        expect(forbiddenProbes.every(({ status }) => status === 404)).toBe(true);

        runtimeReport = {
            ok: true,
            test_count: 1,
            closure_index: closureMessage.index,
            readiness_index: readyIndexes[0],
            first_successful_rpc_index: successfulRequests[0].index,
            first_successful_rpc: successfulRequests[0].message,
            report,
        };
        sourceReport = {
            ok: true,
            runtime_root: "dist/runtime",
            closure_sha256: CLOSURE_SHA256,
            canonical_aggregate_sha256: CANONICAL_INVENTORY.aggregate_sha256,
            served_responses: network.responses.filter(({ url }) => new URL(url).protocol === "http:"),
            blob_responses: network.responses.filter(({ url }) => new URL(url).protocol === "blob:"),
            forbidden_probes: forbiddenProbes,
        };
    } finally {
        if (nativeMessages.length === 0) {
            nativeMessages.push(...await page.evaluate(() => (
                (window as typeof window & { __runtimeCanaryMessages?: NativeMessage[] })
                    .__runtimeCanaryMessages ?? []
            )).catch(() => []));
        }
        await network.finish().catch(() => {});
        writeJson(artifactDirectory, "console.json", envelope, {
            console: consoleRows,
            page_errors: pageErrors,
            native_messages: nativeMessages,
        });
        writeJson(artifactDirectory, "requests.json", envelope, {
            requests: network.requests,
            responses: network.responses,
        });
        writeJson(artifactDirectory, "runtime-report.json", envelope, runtimeReport);
        writeJson(artifactDirectory, "source-report.json", envelope, sourceReport);
    }
});
