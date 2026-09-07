import { kfWitnessesHref, type Route } from "./router.js";
import { renderNotFoundPage, type PageRecord } from "./page-feedback.js";
import { loadRouteData } from "./page-loader.js";
import { renderIdentifierDetailPage } from "../features/identifiers/identifier-detail-page.js";
import { renderIdentifiersPage } from "../features/identifiers/identifiers-page.js";
import { renderRemoteDetailPage } from "../features/remotes/remote-detail-page.js";
import { renderRemotesPage } from "../features/remotes/remotes-page.js";
import { renderSettingsPage } from "../features/settings/settings-page.js";
import { renderUnlockPage } from "../features/vaults/unlock-page.js";
import { renderVaultPickerPage } from "../features/vaults/vault-picker-page.js";
import { renderKfIdentifiersPage } from "../providers/kerifoundation/identifiers-page.js";
import { renderWatcherOverviewPage } from "../providers/kerifoundation/watcher-overview-page.js";
import { renderWitnessOverviewPage } from "../providers/kerifoundation/witness-overview-page.js";

type RecordValue = Record<string, unknown>;
type UnlockPageProps = Parameters<typeof renderUnlockPage>[0];
type IdentifiersPageProps = Parameters<typeof renderIdentifiersPage>[0];
type IdentifierDetailProps = Parameters<typeof renderIdentifierDetailPage>[0];
type RemotesPageProps = Parameters<typeof renderRemotesPage>[0];
type RemoteDetailProps = Parameters<typeof renderRemoteDetailPage>[0];
type SettingsPageProps = Parameters<typeof renderSettingsPage>[0];
type KfIdentifiersPageProps = Parameters<typeof renderKfIdentifiersPage>[0];
type WitnessOverviewProps = Parameters<typeof renderWitnessOverviewPage>[0];
type WatcherOverviewProps = Parameters<typeof renderWatcherOverviewPage>[0];

interface VaultRecord {
    id: string;
    alias: string;
    storageName?: string;
    createdAt: string;
    identifierCount?: number;
    remoteCount?: number;
    locked?: boolean;
}

interface RuntimeBridgeLike {
    request<T extends RecordValue = RecordValue>(
        method: string,
        params?: Record<string, unknown>,
        timeoutMs?: number,
    ): Promise<T>;
}

interface StateSnapshot {
    remoteFilter: string;
    lastCoreRoutes: Record<string, string>;
    vaults: VaultRecord[];
}

interface PageActions {
    createIdentifier(alias: string): Promise<void>;
    loadKfBootstrap(bootUrl?: string): Promise<WitnessOverviewProps["bootstrapState"]>;
    openVault(vaultId: string, passcode?: string): Promise<RecordValue>;
    refreshKfWatcherStatuses(watcherEids?: string[]): Promise<void>;
    resolveRemoteOobi(url: string, alias: string): Promise<void>;
    setRemoteFilter(filter: string): void;
    startKfOnboarding(request: {
        bootUrl: string;
        alias: string;
        witnessProfileCode: string;
        accountAid?: string;
    }): Promise<void>;
    updateRemote(aid: string, patch: Record<string, unknown>): Promise<void>;
}

interface PageFactoryContext {
    actions: PageActions;
    bridge: RuntimeBridgeLike;
    currentState(): StateSnapshot;
    findVault(vaultId?: string): Record<string, unknown> | null;
    isUnlocked(vaultId: string): boolean;
    route: Route;
    showCreateVaultDialog(): void;
}

export interface LoadedPageResult {
    page?: PageRecord;
    vault: Record<string, unknown> | null;
    redirectHref?: string;
}

function assumeType<T>(value: unknown): T {
    return value as T;
}

export async function loadPage({
    actions,
    bridge,
    currentState,
    findVault,
    isUnlocked,
    route,
    showCreateVaultDialog,
}: PageFactoryContext): Promise<LoadedPageResult> {
    if (route.name === "home") {
        return {
            page: renderVaultPickerPage(),
            vault: null,
        };
    }

    if (route.name === "unlock") {
        return {
            page: renderUnlockPage({
                vault: assumeType<UnlockPageProps["vault"]>(findVault(route.params.vaultId)),
                async onOpenVault(passcode: string) {
                    await actions.openVault(route.params.vaultId ?? "", passcode);
                },
            }),
            vault: null,
        };
    }

    const pageData = await loadRouteData({ route, bridge });

    if (route.name === "identifiers") {
        const vaultId = route.params.vaultId ?? "";
        return {
            page: renderIdentifiersPage({
                vault: assumeType<IdentifiersPageProps["vault"]>(findVault(vaultId)),
                identifiers: assumeType<IdentifiersPageProps["identifiers"]>(pageData.identifiers || []),
                async onCreateIdentifier(alias: string) {
                    await actions.createIdentifier(alias);
                },
            }),
            vault: findVault(vaultId),
        };
    }

    if (route.name === "identifier-detail") {
        return {
            page: renderIdentifierDetailPage({
                vault: assumeType<IdentifierDetailProps["vault"]>(findVault(route.params.vaultId)),
                identifier: assumeType<IdentifierDetailProps["identifier"]>(pageData.identifier),
            }),
            vault: findVault(route.params.vaultId),
        };
    }

    if (route.name === "remotes") {
        const vaultId = route.params.vaultId ?? "";
        return {
            page: renderRemotesPage({
                vault: assumeType<RemotesPageProps["vault"]>(findVault(vaultId)),
                remotes: assumeType<RemotesPageProps["remotes"]>(pageData.remotes || []),
                filter: currentState().remoteFilter,
                async onResolveRemote(url: string, alias: string) {
                    await actions.resolveRemoteOobi(url, alias);
                },
                async onUpdateRemote(aid: string, patch: Record<string, unknown>) {
                    await actions.updateRemote(aid, patch);
                },
                onFilterChange: actions.setRemoteFilter,
            }),
            vault: findVault(vaultId),
        };
    }

    if (route.name === "remote-detail") {
        return {
            page: renderRemoteDetailPage({
                vault: assumeType<RemoteDetailProps["vault"]>(findVault(route.params.vaultId)),
                remote: assumeType<RemoteDetailProps["remote"]>(pageData.remote),
            }),
            vault: findVault(route.params.vaultId),
        };
    }

    if (route.name === "settings") {
        return {
            page: renderSettingsPage({
                vault: assumeType<SettingsPageProps["vault"]>(findVault(route.params.vaultId)),
                settings: assumeType<SettingsPageProps["settings"]>(pageData.settings),
            }),
            vault: findVault(route.params.vaultId),
        };
    }

    if (route.name === "kf-home") {
        const vaultId = route.params.vaultId ?? "";
        return {
            redirectHref: kfWitnessesHref(vaultId),
            vault: findVault(vaultId),
        };
    }

    if (route.name === "kf-identifiers") {
        const vaultId = route.params.vaultId ?? "";
        return {
            page: renderKfIdentifiersPage({
                bootstrapState: assumeType<KfIdentifiersPageProps["bootstrapState"]>(pageData.bootstrapState),
                identifiers: assumeType<KfIdentifiersPageProps["identifiers"]>(pageData.identifiers || []),
            }),
            vault: findVault(vaultId),
        };
    }

    if (route.name === "kf-witnesses") {
        const vaultId = route.params.vaultId ?? "";
        return {
            page: renderWitnessOverviewPage({
                bootstrapState: assumeType<WitnessOverviewProps["bootstrapState"]>(pageData.bootstrapState),
                witnesses: assumeType<WitnessOverviewProps["witnesses"]>(pageData.witnesses || []),
                witnessError: pageData.witnessError || "",
                services: assumeType<WitnessOverviewProps["services"]>(pageData.services),
                onLoadBootstrap: actions.loadKfBootstrap,
                async onStartOnboarding(request) {
                    await actions.startKfOnboarding(request);
                },
            }),
            vault: findVault(vaultId),
        };
    }

    if (route.name === "kf-watchers") {
        const vaultId = route.params.vaultId ?? "";
        const watchers = assumeType<WatcherOverviewProps["watchers"]>(pageData.watchers || []);
        return {
            page: renderWatcherOverviewPage({
                vault: assumeType<WatcherOverviewProps["vault"]>(findVault(vaultId)),
                bootstrapState: assumeType<WatcherOverviewProps["bootstrapState"]>(pageData.bootstrapState),
                watchers,
                watcherError: pageData.watcherError || "",
                services: assumeType<WatcherOverviewProps["services"]>(pageData.services),
                async onRefreshStatuses() {
                    await actions.refreshKfWatcherStatuses(watchers.map((watcher) => watcher.eid));
                },
            }),
            vault: findVault(vaultId),
        };
    }

    return {
        page: renderNotFoundPage(route.path),
        vault: null,
    };
}