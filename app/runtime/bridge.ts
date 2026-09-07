import { PyWorker, type PyWorkerHandle } from "../../vendor/pyscript/2025.11.2/core.js";
import { parse as parseToml } from "../../vendor/pyscript/2025.11.2/toml-BK2RWy-G.js";
import { createRuntimeRequest, isRuntimeResponse, type RuntimeResponse } from "./messages.js";
import { postLog, postLifecycle } from "./logger.js";
import {
    describeRuntimeOriginContract,
    type FortRuntimeOriginContractV1,
} from "./origin-contract.js";

const WORKER_DIAGNOSTIC_KIND = "fortweb.runtime.diagnostic";
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const WORKER_LIVENESS_TIMEOUT_MS = 3_000;
const BACKGROUND_STALE_THRESHOLD_MS = 30_000;
const PRELOAD_MAX_MS = 300_000;
const METHOD_TIMEOUT_MS: Record<string, number> = {
    "vaults.create": 120_000,
    "vaults.open": 90_000,
    "identifiers.create": 90_000,
    "remotes.resolveOobi": 60_000,
    "kf.onboarding.start": 120_000,
};

type RuntimeBridgeError = Error & { code?: string; cause?: unknown };

interface PreloadGate {
    promise: Promise<string | undefined>;
    resolve: (reason?: string) => void;
}

interface WorkerDiagnostic {
    event: string;
    level?: string;
    fields: Record<string, unknown>;
}

interface RuntimeBridgeOptions {
    workerUrl: URL | string;
    configUrl: URL | string;
    runtimeOriginContract?: FortRuntimeOriginContractV1 | null;
}

interface RuntimeBridge {
    request<T extends Record<string, unknown> = Record<string, unknown>>(
        method: string,
        params?: Record<string, unknown>,
        timeoutMs?: number,
    ): Promise<T>;
    rawRequest(rawPayload: string, timeoutMs?: number, label?: string): Promise<unknown>;
    destroy(): void;
}

function createRuntimeBridgeError(message: string, code: string, cause?: unknown): RuntimeBridgeError {
    const error = new Error(message) as RuntimeBridgeError;
    error.code = code;
    if (cause !== undefined) {
        error.cause = cause;
    }
    return error;
}

function getErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}

function getErrorCode(error: unknown, fallback: string): string {
    if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
        return error.code;
    }
    return fallback;
}

function roundDurationMs(startedAt: number): number {
    return Math.max(0, Math.round(performance.now() - startedAt));
}

function resolveTimeoutMs(method: string, timeoutMs?: number): number {
    if (Number.isFinite(timeoutMs) && (timeoutMs ?? 0) > 0) {
        return timeoutMs as number;
    }

    return METHOD_TIMEOUT_MS[method] ?? DEFAULT_REQUEST_TIMEOUT_MS;
}

function createPreloadGate(): PreloadGate {
    let settled = false;
    let resolveGate: (reason?: string) => void = () => {};
    const promise = new Promise<string | undefined>((resolve) => {
        resolveGate = (reason?: string) => {
            if (settled) {
                return;
            }
            settled = true;
            resolve(reason);
        };
    });
    return { promise, resolve: resolveGate };
}

function withTimeout<T>(promise: Promise<T> | T, timeoutMs: number, method: string): Promise<T> {
    return new Promise((resolve, reject) => {
        const timeoutId = window.setTimeout(() => {
            reject(createRuntimeBridgeError(`Runtime request timed out for ${method}.`, "TIMEOUT"));
        }, timeoutMs);

        Promise.resolve(promise).then(
            (value) => {
                clearTimeout(timeoutId);
                resolve(value);
            },
            (error) => {
                clearTimeout(timeoutId);
                reject(error);
            },
        );
    });
}

function parseRuntimeResponse(rawResponse: unknown): unknown {
    const payload =
        typeof rawResponse === "string"
            ? rawResponse
            : (rawResponse as { data?: unknown } | null | undefined)?.data ?? rawResponse;

    if (typeof payload !== "string") {
        return payload;
    }

    try {
        return JSON.parse(payload) as unknown;
    } catch (error) {
        throw createRuntimeBridgeError(
            "Runtime worker returned a malformed response.",
            "BAD_RESPONSE",
            error,
        );
    }
}

function parseWorkerDiagnostic(rawPayload: unknown): WorkerDiagnostic | null {
    const payload =
        typeof rawPayload === "string"
            ? (() => {
                  try {
                      return JSON.parse(rawPayload) as unknown;
                  } catch {
                      return null;
                  }
              })()
            : (rawPayload as { data?: unknown } | null | undefined)?.data ?? rawPayload;

    if (!payload || typeof payload !== "object") {
        return null;
    }

    const candidate = payload as Record<string, unknown>;
    if (candidate.kind !== WORKER_DIAGNOSTIC_KIND || typeof candidate.event !== "string") {
        return null;
    }

    const { kind: _kind, event, level, ...fields } = candidate;
    return {
        event,
        level: typeof level === "string" ? level : undefined,
        fields,
    };
}

export function createRuntimeBridge({ workerUrl, configUrl, runtimeOriginContract = null }: RuntimeBridgeOptions): RuntimeBridge {
    let requestCounter = 0;
    let bootedWorker: PyWorkerHandle | null = null;
    let workerPromise: Promise<PyWorkerHandle> | null = null;
    let hiddenSince = 0;
    let preloadGate = createPreloadGate();

    function resetPreloadGate(): void {
        preloadGate = createPreloadGate();
    }

    function resolvePreloadGate(reason = "resolved"): void {
        preloadGate.resolve(reason);
    }

    async function waitForPreloadReady(): Promise<void> {
        await Promise.race([
            preloadGate.promise,
            new Promise<never>((_, reject) => {
                window.setTimeout(() => {
                    reject(createRuntimeBridgeError("Worker preload timed out.", "TIMEOUT"));
                }, PRELOAD_MAX_MS);
            }),
        ]);
    }

    function createWorkerPromise(): Promise<PyWorkerHandle> {
        resetPreloadGate();
        return (async () => {
            console.time("[bridge] worker boot");
            postLifecycle("boot");

            try {
                const workerUrlString = workerUrl.toString();
                const configUrlString = configUrl.toString();
                const response = await fetch(configUrlString);
                if (!response.ok) {
                    throw new Error(`Unable to load runtime config from ${configUrlString}.`);
                }

                const config = parseToml(await response.text()) as Record<string, unknown>;
                if (runtimeOriginContract) {
                    config.fort_runtime_origin = runtimeOriginContract;
                    postLog("runtime_origin_contract_forwarded", describeRuntimeOriginContract(runtimeOriginContract));
                }

                // Hand the (already local/vendored) interpreter to the PyScript
                // worker both as the top-level `version` (the polyscript worker
                // builds its runtime handle as `<type>@<version>` and falls back
                // to its bundled jsdelivr default when version is absent) and as
                // `interpreter`. The DOM-script code path in polyscript resolves
                // interpreter/version to an absolute URL against the configURL
                // base, so we do the same here — a relative "/fortweb/..." path
                // alone is not enough to avoid the jsdelivr default engine.
                const interpreterUrl =
                    typeof config.interpreter === "string" && config.interpreter.length > 0
                        ? (config.interpreter as string)
                        : undefined;
                const interpreterVersionUrl =
                    interpreterUrl !== undefined ? new URL(interpreterUrl, configUrlString).href : undefined;

                const worker = await PyWorker(workerUrlString, {
                    type: "pyodide",
                    configURL: configUrlString,
                    config,
                    ...(interpreterUrl !== undefined ? { interpreter: interpreterUrl } : {}),
                    ...(interpreterVersionUrl !== undefined ? { version: interpreterVersionUrl } : {}),
                });
                postLifecycle("ready");
                return worker;
            } catch (error) {
                postLifecycle("error", {
                    reason: getErrorMessage(error),
                });
                resolvePreloadGate("js_boot_error");
                throw error;
            } finally {
                console.timeEnd("[bridge] worker boot");
            }
        })();
    }

    workerPromise = createWorkerPromise();

    function invalidateWorker(reason: string, fields: Record<string, unknown> = {}): void {
        postLog("worker_invalidation", {
            level: "warning",
            reason,
            ...fields,
        });

        if (bootedWorker) {
            console.warn(`[bridge] invalidating worker: ${reason}`);
            bootedWorker = null;
        }
    }

    function attachWorkerHandlers(worker: PyWorkerHandle): void {
        if (typeof worker.addEventListener === "function") {
            worker.addEventListener("message", (event) => {
                const diagnostic = parseWorkerDiagnostic(event?.data);
                if (!diagnostic) {
                    return;
                }

                postLog(diagnostic.event, {
                    level: diagnostic.level ?? "info",
                    ...diagnostic.fields,
                });
                if (
                    diagnostic.event === "worker_preload_complete" ||
                    diagnostic.event === "worker_preload_failed"
                ) {
                    resolvePreloadGate(diagnostic.event);
                }
            });
        }

        worker.onerror = (event: { message?: string } | unknown) => {
            console.error("[bridge] worker error:", event && typeof event === "object" && "message" in event ? event.message : event);
            postLifecycle("error", {
                reason: event && typeof event === "object" && "message" in event && typeof event.message === "string"
                    ? event.message
                    : String(event),
            });
            resolvePreloadGate("worker_error");
            invalidateWorker("worker error event");
        };
        worker.onmessageerror = () => {
            console.error("[bridge] worker message deserialization error");
            postLifecycle("error", {
                reason: "worker message deserialization error",
            });
            resolvePreloadGate("worker_message_error");
            invalidateWorker("message error event");
        };
    }

    workerPromise.then(attachWorkerHandlers, () => {});

    async function bootFreshWorker(timeoutMs: number): Promise<PyWorkerHandle> {
        workerPromise = createWorkerPromise();
        workerPromise.then(attachWorkerHandlers, () => {});
        bootedWorker = await withTimeout(workerPromise, timeoutMs, "worker boot");
        return bootedWorker;
    }

    async function getWorker(timeoutMs: number): Promise<PyWorkerHandle> {
        if (!bootedWorker) {
            try {
                if (!workerPromise) {
                    workerPromise = createWorkerPromise();
                    workerPromise.then(attachWorkerHandlers, () => {});
                }
                bootedWorker = await withTimeout(workerPromise, timeoutMs, "worker boot");
            } catch (error) {
                console.warn("[bridge] initial worker promise failed, booting fresh worker:", getErrorMessage(error));
                bootedWorker = await bootFreshWorker(timeoutMs);
            }
        }

        await waitForPreloadReady();
        return bootedWorker;
    }

    async function checkWorkerLiveness(worker: PyWorkerHandle): Promise<boolean> {
        const pingId = `ping-${Date.now()}`;
        const pingPayload = JSON.stringify({
            id: pingId,
            kind: "fortweb.runtime.request",
            method: "settings.get",
            params: {},
        });
        try {
            console.time("[bridge] liveness check");
            await withTimeout(worker.sync.handle_request(pingPayload), WORKER_LIVENESS_TIMEOUT_MS, "liveness check");
            console.timeEnd("[bridge] liveness check");
            return true;
        } catch {
            console.timeEnd("[bridge] liveness check");
            return false;
        }
    }

    function onVisibilityChange(): void {
        if (document.hidden) {
            hiddenSince = Date.now();
        } else if (hiddenSince > 0) {
            const elapsed = Date.now() - hiddenSince;
            hiddenSince = 0;
            if (elapsed >= BACKGROUND_STALE_THRESHOLD_MS) {
                invalidateWorker(`app was hidden for ${Math.round(elapsed / 1000)}s`);
            }
        }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);

    async function rawRequest(rawPayload: string, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, label = "raw runtime request"): Promise<unknown> {
        if (typeof rawPayload !== "string") {
            throw new Error("Runtime raw request payload must be a string.");
        }

        const worker = await getWorker(timeoutMs);
        console.time(`[bridge] ${label}`);
        const rawResponse = await withTimeout(worker.sync.handle_request(rawPayload), timeoutMs, label);
        console.timeEnd(`[bridge] ${label}`);
        return parseRuntimeResponse(rawResponse);
    }

    async function request<T extends Record<string, unknown> = Record<string, unknown>>(
        method: string,
        params: Record<string, unknown> = {},
        timeoutMs?: number,
    ): Promise<T> {
        const effectiveTimeoutMs = resolveTimeoutMs(method, timeoutMs);
        const id = `runtime-${Date.now()}-${requestCounter++}`;
        const payload = JSON.stringify(createRuntimeRequest(id, method, params));
        const startedAt = performance.now();

        postLog("request_start", {
            level: "info",
            method,
            request_id: id,
            timeout_ms: effectiveTimeoutMs,
        });

        try {
            const response = await rawRequest(payload, effectiveTimeoutMs, method);
            const result = handleResponse(response, id) as T;
            postLog("request_end", {
                level: "info",
                method,
                request_id: id,
                outcome: "ok",
                duration_ms: roundDurationMs(startedAt),
            });
            return result;
        } catch (firstError) {
            if (getErrorCode(firstError, "RUNTIME_ERROR") !== "TIMEOUT") {
                postLog("terminal_failure", {
                    level: "error",
                    method,
                    request_id: id,
                    code: getErrorCode(firstError, "RUNTIME_ERROR"),
                    message: getErrorMessage(firstError),
                    duration_ms: roundDurationMs(startedAt),
                });
                throw firstError;
            }

            console.warn(`[bridge] ${method} timed out, checking worker liveness`);
            postLog("request_timeout", {
                level: "warning",
                method,
                request_id: id,
                timeout_ms: effectiveTimeoutMs,
            });
            const worker = await getWorker(effectiveTimeoutMs);
            const alive = await checkWorkerLiveness(worker);

            if (alive) {
                postLog("terminal_failure", {
                    level: "error",
                    method,
                    request_id: id,
                    code: getErrorCode(firstError, "TIMEOUT"),
                    message: getErrorMessage(firstError),
                    duration_ms: roundDurationMs(startedAt),
                });
                throw firstError;
            }

            console.warn(`[bridge] worker is dead after timeout, booting fresh worker and retrying ${method}`);
            invalidateWorker("dead after timeout", {
                request_id: id,
                method,
            });
            await bootFreshWorker(effectiveTimeoutMs);

            const retryId = `runtime-${Date.now()}-${requestCounter++}`;
            const retryPayload = JSON.stringify(createRuntimeRequest(retryId, method, params));
            postLog("request_retry", {
                level: "warning",
                method,
                request_id: id,
                retry_request_id: retryId,
                reason: "dead_after_timeout",
            });

            try {
                const retryResponse = await rawRequest(retryPayload, effectiveTimeoutMs, method);
                const retryResult = handleResponse(retryResponse, retryId) as T;
                postLog("request_end", {
                    level: "info",
                    method,
                    request_id: retryId,
                    prior_request_id: id,
                    outcome: "ok",
                    duration_ms: roundDurationMs(startedAt),
                });
                return retryResult;
            } catch (retryError) {
                postLog("terminal_failure", {
                    level: "error",
                    method,
                    request_id: retryId,
                    prior_request_id: id,
                    code: getErrorCode(retryError, "RUNTIME_ERROR"),
                    message: getErrorMessage(retryError),
                    duration_ms: roundDurationMs(startedAt),
                });
                throw retryError;
            }
        }
    }

    function handleResponse(response: unknown, expectedId: string): Record<string, unknown> {
        if (!isRuntimeResponse(response) || response.id !== expectedId) {
            throw createRuntimeBridgeError(
                "Runtime worker returned an invalid response.",
                "BAD_RESPONSE",
            );
        }

        if (response.ok) {
            return response.result;
        }

        const runtimeError = "error" in response ? response.error : undefined;
        throw createRuntimeBridgeError(
            runtimeError?.message || "Runtime request failed.",
            runtimeError?.code || "RUNTIME_ERROR",
        );
    }

    return {
        request,
        rawRequest,
        destroy(): void {
            document.removeEventListener("visibilitychange", onVisibilityChange);
            void workerPromise?.then((worker) => {
                worker.terminate?.();
            });
        },
    };
}