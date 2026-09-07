import { type Route } from "./router.js";
import { METHODS } from "../runtime/method-catalog.js";

type RecordValue = Record<string, unknown>;

interface RuntimeBridgeLike {
    request<T extends RecordValue = RecordValue>(
        method: string,
        params?: Record<string, unknown>,
        timeoutMs?: number,
    ): Promise<T>;
}

interface RouteDataLoaderContext {
    bridge: RuntimeBridgeLike;
    route: Route;
}

export interface RouteDataResult {
    bootstrapState?: RecordValue;
    identifier?: RecordValue;
    identifiers?: RecordValue[];
    remote?: RecordValue;
    remotes?: RecordValue[];
    services?: RecordValue;
    settings?: RecordValue;
    watcherError?: string;
    watchers?: RecordValue[];
    witnessError?: string;
    witnesses?: RecordValue[];
}

/** Fetch the normalized hosted-service connection view model (domain layer).
 *
 * Independent of Kf Boot account-management synchronization: it is derived from
 * cryptographically proven milestones persisted during hosted onboarding, so a
 * `/account` management 409 must never hide direct service truth.
 */
async function loadServicesOverview(
    bridge: RuntimeBridgeLike,
    vaultId: string,
): Promise<RecordValue | undefined> {
    try {
        const result = await bridge.request<{ services: RecordValue }>(METHODS.kfServicesOverview, {
            vaultId,
        });
        return result.services ?? {};
    } catch {
        return undefined;
    }
}

export async function loadRouteData({ bridge, route }: RouteDataLoaderContext): Promise<RouteDataResult> {
    if (route.name === "identifiers") {
        const vaultId = route.params.vaultId ?? "";
        return bridge.request<{ identifiers: RecordValue[] }>(METHODS.identifiersList, { vaultId });
    }

    if (route.name === "identifier-detail") {
        return bridge.request<{ identifier: RecordValue }>(METHODS.identifiersGet, {
            vaultId: route.params.vaultId,
            aid: route.params.aid,
        });
    }

    if (route.name === "remotes") {
        const vaultId = route.params.vaultId ?? "";
        return bridge.request<{ remotes: RecordValue[] }>(METHODS.remotesList, {
            vaultId,
        });
    }

    if (route.name === "remote-detail") {
        return bridge.request<{ remote: RecordValue }>(METHODS.remotesGet, {
            vaultId: route.params.vaultId,
            aid: route.params.aid,
        });
    }

    if (route.name === "settings") {
        return bridge.request<{ settings: RecordValue }>(METHODS.settingsGet, {
            vaultId: route.params.vaultId,
        });
    }

    if (route.name === "kf-identifiers") {
        const vaultId = route.params.vaultId ?? "";
        const bootstrapState = await bridge.request<RecordValue>(METHODS.kfBootstrapGet, { vaultId });
        const { identifiers } = await bridge.request<{ identifiers: RecordValue[] }>(METHODS.identifiersList, {
            vaultId,
        });
        return {
            bootstrapState,
            identifiers,
        };
    }

    if (route.name === "kf-witnesses") {
        const vaultId = route.params.vaultId ?? "";
        const bootstrapState = await bridge.request<RecordValue>(METHODS.kfBootstrapGet, { vaultId });
        let witnesses: RecordValue[] = [];
        let witnessError = "";
        if ((bootstrapState.account as RecordValue | undefined)?.status === "onboarded") {
            try {
                ({ witnesses } = await bridge.request<{ witnesses: RecordValue[] }>(
                    METHODS.kfAccountWitnessesList,
                    { vaultId },
                ));
            } catch (error) {
                witnessError = error instanceof Error ? error.message : "Failed to load hosted witness rows.";
            }
        }
        return {
            bootstrapState,
            witnesses,
            witnessError,
            services: await loadServicesOverview(bridge, vaultId),
        };
    }

    if (route.name === "kf-watchers") {
        const vaultId = route.params.vaultId ?? "";
        const bootstrapState = await bridge.request<RecordValue>(METHODS.kfBootstrapGet, { vaultId });
        let watchers: RecordValue[] = [];
        let watcherError = "";
        if ((bootstrapState.account as RecordValue | undefined)?.status === "onboarded") {
            try {
                ({ watchers } = await bridge.request<{ watchers: RecordValue[] }>(
                    METHODS.kfAccountWatchersList,
                    { vaultId },
                ));
            } catch (error) {
                watcherError = error instanceof Error ? error.message : "Failed to load hosted watcher rows.";
            }
        }
        return {
            bootstrapState,
            watchers,
            watcherError,
            services: await loadServicesOverview(bridge, vaultId),
        };
    }

    return {};
}