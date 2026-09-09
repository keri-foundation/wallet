import { PyWorker, type PyWorkerHandle } from "../../vendor/pyscript/2025.11.2/core.js";
import { parse as parseToml } from "../../vendor/pyscript/2025.11.2/toml-BK2RWy-G.js";
import { createRuntimeRequest, isRuntimeResponse, type RuntimeResponse } from "./messages.js";
import { postLog, postLifecycle } from "./logger.js";
import { fetchRuntimeConfig } from "./runtime-config.js";
import {
    describeRuntimeOriginContract,
    type FortRuntimeOriginContractV1,
} from "./origin-contract.js";

const WORKER_DIAGNOSTIC_KIND = "fortweb.runtime.diagnostic";
const WORKER_RPC_PROBE = "fortweb.runtime.rpc.probe";
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const WORKER_RPC_READY_TIMEOUT_MS = 180_000;
const WORKER_RPC_RETRY_INITIAL_MS = 10;
const WORKER_RPC_RETRY_MAX_MS = 250;
const BACKGROUND_STALE_THRESHOLD_MS = 30_000;
const METHOD_TIMEOUT_MS: Record<string, number> = {
    "vaults.create": 120_000,
    "vaults.open": 90_000,
    "identifiers.create": 90_000,
    "remotes.resolveOobi": 60_000,
    "kf.onboarding.start": 120_000,
};

type RuntimeBridgeError = Error & { code?: string; cause?: unknown };

interface WorkerDiagnostic {
    event: string;
    level?: string;
    fields: Record<string, unknown>;
}

interface WorkerRawResponse {
    worker: PyWorkerHandle;
    response: unknown;
}

type RuntimeWorkerFactory = (
    workerUrl: string,
    options: {
        type: string;
        configURL: string;
        config: unknown;
        version: string;
    },
) => Promise<PyWorkerHandle>;

const createPyWorker = PyWorker as unknown as RuntimeWorkerFactory;

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
    let promisedWorker: PyWorkerHandle | null = null;
    let rpcReadyWorker: PyWorkerHandle | null = null;
    let workerPromise: Promise<PyWorkerHandle> | null = null;
    let workerGeneration = 0;
    let hiddenSince = 0;
    let destroyed = false;
    const terminatedWorkers = new WeakSet<PyWorkerHandle>();

    function terminateWorker(worker: PyWorkerHandle): void {
        if (terminatedWorkers.has(worker)) {
            return;
        }
        terminatedWorkers.add(worker);
        worker.terminate?.();
    }

    function createWorkerPromise(): Promise<PyWorkerHandle> {
        workerGeneration += 1;
        const promise = (async () => {
            postLifecycle("boot");

            try {
                const workerUrlString = workerUrl.toString();
                const configUrlString = configUrl.toString();
                const { packageBase, response } = await fetchRuntimeConfig(
                    configUrlString,
                    window.location.href,
                );
                if (!response.ok) {
                    throw new Error(`Unable to load runtime config from ${configUrlString}.`);
                }

                const config = parseToml(await response.text()) as Record<string, unknown>;
                const configuredVersion = config.version ?? config.interpreter;
                if (typeof configuredVersion !== "string" || configuredVersion.length === 0) {
                    throw new Error(`Runtime config ${configUrlString} does not specify an interpreter.`);
                }
                const runtimeVersion = new URL(
                    configuredVersion,
                    new URL(configUrlString, window.location.href),
                ).href;
                const packageConfig = config.fort_runtime_packages;
                if (!packageConfig || typeof packageConfig !== "object" || Array.isArray(packageConfig)) {
                    throw new Error(`Runtime config ${configUrlString} does not specify fort_runtime_packages.`);
                }
                (packageConfig as Record<string, unknown>).package_base = packageBase;
                if (runtimeOriginContract) {
                    config.fort_runtime_origin = runtimeOriginContract;
                    postLog("runtime_origin_contract_forwarded", describeRuntimeOriginContract(runtimeOriginContract));
                }

                const worker = await createPyWorker(workerUrlString, {
                    type: "pyodide",
                    configURL: configUrlString,
                    config,
                    version: runtimeVersion,
                });
                postLifecycle("ready");
                return worker;
            } catch (error) {
                postLifecycle("error", {
                    reason: getErrorMessage(error),
                });
                throw error;
            }
        })();
        promise.then(
            (worker) => {
                if (workerPromise !== promise) {
                    terminateWorker(worker);
                    return;
                }
                promisedWorker = worker;
                attachWorkerHandlers(worker);
            },
            () => {},
        );
        return promise;
    }

    workerPromise = createWorkerPromise();

    function invalidateWorker(
        reason: string,
        fields: Record<string, unknown> = {},
        expectedWorker: PyWorkerHandle | null = null,
    ): void {
        if (expectedWorker && expectedWorker !== bootedWorker && expectedWorker !== promisedWorker) {
            terminateWorker(expectedWorker);
            return;
        }

        postLog("worker_invalidation", {
            level: "warning",
            reason,
            ...fields,
        });

        const invalidatedPromise = workerPromise;
        bootedWorker = null;
        promisedWorker = null;
        rpcReadyWorker = null;
        workerPromise = null;
        workerGeneration += 1;
        void invalidatedPromise?.then(
            terminateWorker,
            () => {},
        );
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
            });
        }

        worker.onerror = (event: { message?: string } | unknown) => {
            postLifecycle("error", {
                reason: event && typeof event === "object" && "message" in event && typeof event.message === "string"
                    ? event.message
                    : String(event),
            });
            invalidateWorker("worker error event", {}, worker);
        };
        worker.onmessageerror = () => {
            postLifecycle("error", {
                reason: "worker message deserialization error",
            });
            invalidateWorker("message error event", {}, worker);
        };
    }

    async function waitForWorkerRpc(worker: PyWorkerHandle): Promise<void> {
        const deadline = performance.now() + WORKER_RPC_READY_TIMEOUT_MS;
        let lastError: unknown = null;
        let retryDelayMs = WORKER_RPC_RETRY_INITIAL_MS;

        while (performance.now() < deadline) {
            try {
                const remainingMs = Math.max(1, deadline - performance.now());
                const response = await withTimeout(
                    worker.sync.handle_request(WORKER_RPC_PROBE),
                    remainingMs,
                    "worker RPC readiness probe",
                );
                if (response === WORKER_RPC_PROBE) {
                    return;
                }
                const parsed = parseRuntimeResponse(response);
                if (
                    parsed
                    && typeof parsed === "object"
                    && "kind" in parsed
                    && parsed.kind === "fortweb.runtime.rpc.probe.error"
                ) {
                    const code = "code" in parsed && typeof parsed.code === "string"
                        ? parsed.code
                        : "RUNTIME_ERROR";
                    const message = "message" in parsed && typeof parsed.message === "string"
                        ? parsed.message
                        : "Runtime worker preload failed.";
                    throw createRuntimeBridgeError(message, code);
                }
            } catch (error) {
                if (getErrorCode(error, "")) {
                    throw error;
                }
                lastError = error;
            }
            await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs));
            retryDelayMs = Math.min(retryDelayMs * 2, WORKER_RPC_RETRY_MAX_MS);
        }

        throw createRuntimeBridgeError(
            "Runtime worker RPC did not become ready.",
            "RUNTIME_ERROR",
            lastError,
        );
    }

    async function getWorker(timeoutMs: number): Promise<PyWorkerHandle> {
        if (destroyed) {
            throw createRuntimeBridgeError("Runtime bridge was destroyed.", "RUNTIME_ERROR");
        }

        if (!bootedWorker) {
            if (!workerPromise) {
                workerPromise = createWorkerPromise();
            }
            const pendingWorker = workerPromise;
            const pendingGeneration = workerGeneration;
            try {
                const worker = await withTimeout(pendingWorker, timeoutMs, "worker boot");
                if (workerPromise !== pendingWorker || workerGeneration !== pendingGeneration) {
                    throw createRuntimeBridgeError("Runtime worker was invalidated during boot.", "RUNTIME_ERROR");
                }
                bootedWorker = worker;
            } catch (error) {
                if (workerPromise === pendingWorker) {
                    invalidateWorker("worker boot failed", {
                        reason_detail: getErrorMessage(error),
                    });
                }
                throw error;
            }
        }

        if (rpcReadyWorker !== bootedWorker) {
            try {
                await waitForWorkerRpc(bootedWorker);
            } catch (error) {
                invalidateWorker("worker RPC readiness failed", {
                    reason_detail: getErrorMessage(error),
                }, bootedWorker);
                throw error;
            }
            rpcReadyWorker = bootedWorker;
        }
        return bootedWorker;
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

    async function sendRawRequest(
        rawPayload: string,
        timeoutMs: number,
        label: string,
    ): Promise<WorkerRawResponse> {
        if (typeof rawPayload !== "string") {
            throw new Error("Runtime raw request payload must be a string.");
        }

        const worker = await getWorker(timeoutMs);
        try {
            const rawResponse = await withTimeout(worker.sync.handle_request(rawPayload), timeoutMs, label);
            return {
                worker,
                response: parseRuntimeResponse(rawResponse),
            };
        } catch (error) {
            if (getErrorCode(error, "RUNTIME_ERROR") === "TIMEOUT") {
                invalidateWorker("request timeout", { label }, worker);
            }
            throw error;
        }
    }

    async function rawRequest(
        rawPayload: string,
        timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
        label = "raw runtime request",
    ): Promise<unknown> {
        const result = await sendRawRequest(rawPayload, timeoutMs, label);
        return result.response;
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
            const { response } = await sendRawRequest(payload, effectiveTimeoutMs, method);
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
            const code = getErrorCode(firstError, "RUNTIME_ERROR");
            if (code === "TIMEOUT") {
                postLog("request_timeout", {
                    level: "warning",
                    method,
                    request_id: id,
                    timeout_ms: effectiveTimeoutMs,
                });
            }

            postLog("terminal_failure", {
                level: "error",
                method,
                request_id: id,
                code,
                message: getErrorMessage(firstError),
                duration_ms: roundDurationMs(startedAt),
            });
            throw firstError;
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
            destroyed = true;
            document.removeEventListener("visibilitychange", onVisibilityChange);
            invalidateWorker("bridge destroyed");
        },
    };
}
