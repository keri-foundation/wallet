import { renderVaultPickerPage } from "../features/vaults/vault-picker-page.js";
import { renderUnlockPage } from "../features/vaults/unlock-page.js";
import { renderIdentifiersPage } from "../features/identifiers/identifiers-page.js";
import { renderIdentifierDetailPage } from "../features/identifiers/identifier-detail-page.js";
import { renderRemotesPage } from "../features/remotes/remotes-page.js";
import { renderRemoteDetailPage } from "../features/remotes/remote-detail-page.js";
import { renderSettingsPage } from "../features/settings/settings-page.js";
import { renderWitnessOverviewPage } from "../providers/kerifoundation/witness-overview-page.js";
import { renderWatcherOverviewPage } from "../providers/kerifoundation/watcher-overview-page.js";

import {
    fixtureVault,
    fixtureVaultLocked,
    fixtureVaults,
    fixtureIdentifiers,
    fixtureRemotes,
    fixtureSettings,
    fixtureBootstrapDisconnected,
    fixtureBootstrapConnected,
    fixtureBootstrapOnboarded,
    fixtureWitnesses,
    fixtureWatchers,
    FIXTURE_VAULT_ID_CONST,
} from "./data.js";

interface PageRecord {
    title: string;
    html?: string;
    render?(container: HTMLElement): void;
    setup?(root: HTMLElement): void;
}

interface RouteRecord {
    name: string;
    shellMode: string;
    navMode: string;
    path: string;
    params: Record<string, string>;
}

interface FixtureRecord {
    page: PageRecord;
    vault: object | null;
    route: RouteRecord;
}

type FixtureFactory = () => FixtureRecord;

type BootstrapState = typeof fixtureBootstrapDisconnected;

const noop = (): void => {};
const asyncNoop = async (): Promise<void> => {};
const loadBootstrapFixture =
    (snapshot: BootstrapState) =>
    async (_bootUrl: string): Promise<BootstrapState> => snapshot;

const FIXTURES: Record<string, FixtureFactory> = {
    "vaults/empty": () => ({
        page: renderVaultPickerPage(),
        vault: null,
        route: { name: "home", shellMode: "home", navMode: "none", path: "/_fixtures/vaults/empty", params: {} },
    }),

    "vaults/populated": () => ({
        page: renderVaultPickerPage(),
        vault: null,
        route: { name: "home", shellMode: "home", navMode: "none", path: "/_fixtures/vaults/populated", params: {} },
    }),

    unlock: () => ({
        page: renderUnlockPage({ vault: fixtureVaultLocked, onOpenVault: asyncNoop }),
        vault: null,
        route: { name: "unlock", shellMode: "home", navMode: "none", path: "/_fixtures/unlock", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "identifiers/empty": () => ({
        page: renderIdentifiersPage({ vault: fixtureVault, identifiers: [], onCreateIdentifier: asyncNoop }),
        vault: fixtureVault,
        route: { name: "identifiers", shellMode: "vault", navMode: "core", path: "/_fixtures/identifiers/empty", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "identifiers/populated": () => ({
        page: renderIdentifiersPage({ vault: fixtureVault, identifiers: fixtureIdentifiers, onCreateIdentifier: asyncNoop }),
        vault: fixtureVault,
        route: { name: "identifiers", shellMode: "vault", navMode: "core", path: "/_fixtures/identifiers/populated", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "identifier-detail": () => ({
        page: renderIdentifierDetailPage({ vault: fixtureVault, identifier: fixtureIdentifiers[0] }),
        vault: fixtureVault,
        route: { name: "identifier-detail", shellMode: "vault", navMode: "core", path: "/_fixtures/identifier-detail", params: { vaultId: FIXTURE_VAULT_ID_CONST, aid: fixtureIdentifiers[0].aid } },
    }),

    "remotes/empty": () => ({
        page: renderRemotesPage({ vault: fixtureVault, remotes: [], filter: "all", onResolveRemote: asyncNoop, onUpdateRemote: asyncNoop, onFilterChange: noop }),
        vault: fixtureVault,
        route: { name: "remotes", shellMode: "vault", navMode: "core", path: "/_fixtures/remotes/empty", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "remotes/populated": () => ({
        page: renderRemotesPage({ vault: fixtureVault, remotes: fixtureRemotes, filter: "all", onResolveRemote: asyncNoop, onUpdateRemote: asyncNoop, onFilterChange: noop }),
        vault: fixtureVault,
        route: { name: "remotes", shellMode: "vault", navMode: "core", path: "/_fixtures/remotes/populated", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "remote-detail": () => ({
        page: renderRemoteDetailPage({ vault: fixtureVault, remote: fixtureRemotes[0] }),
        vault: fixtureVault,
        route: { name: "remote-detail", shellMode: "vault", navMode: "core", path: "/_fixtures/remote-detail", params: { vaultId: FIXTURE_VAULT_ID_CONST, aid: fixtureRemotes[0].aid } },
    }),

    "witnesses/disconnected": () => ({
        page: renderWitnessOverviewPage({ bootstrapState: fixtureBootstrapDisconnected, witnesses: [], witnessError: "", onLoadBootstrap: loadBootstrapFixture(fixtureBootstrapDisconnected), onStartOnboarding: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-witnesses", shellMode: "vault", navMode: "plugin", path: "/_fixtures/witnesses/disconnected", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "witnesses/connected": () => ({
        page: renderWitnessOverviewPage({ bootstrapState: fixtureBootstrapConnected, witnesses: [], witnessError: "", onLoadBootstrap: loadBootstrapFixture(fixtureBootstrapConnected), onStartOnboarding: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-witnesses", shellMode: "vault", navMode: "plugin", path: "/_fixtures/witnesses/connected", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "witnesses/account": () => ({
        page: renderWitnessOverviewPage({ bootstrapState: fixtureBootstrapOnboarded, witnesses: fixtureWitnesses, witnessError: "", onLoadBootstrap: loadBootstrapFixture(fixtureBootstrapOnboarded), onStartOnboarding: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-witnesses", shellMode: "vault", navMode: "plugin", path: "/_fixtures/witnesses/account", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "witnesses/direct-connected": () => ({
        page: renderWitnessOverviewPage({
            bootstrapState: fixtureBootstrapOnboarded,
            witnesses: fixtureWitnesses,
            witnessError: "",
            services: {
                witness: {
                    eid: "EHfYtj7b4M8a8Y6N2xMqT8JgZ2YbJ8mX7e6sHjYb8c0=",
                    endpoint: "https://138.68.53.132:5633",
                    oobiVerified: true,
                    registered: true,
                    receiptVerified: true,
                    directStatus: "connected",
                    managementSyncStatus: "pending",
                },
                watcher: {
                    eid: "EBiSD01QhoaDCiaqZrz9zrgItq1uSbGSny7pqcLDMOp_=",
                    endpoint: "https://138.68.53.132:7633",
                    oobiVerified: true,
                    introduced: true,
                    queryVerified: true,
                    observedSn: 1,
                    directStatus: "connected",
                    managementSyncStatus: "pending",
                },
            },
            onLoadBootstrap: loadBootstrapFixture(fixtureBootstrapOnboarded),
            onStartOnboarding: asyncNoop,
        }),
        vault: fixtureVault,
        route: { name: "kf-witnesses", shellMode: "vault", navMode: "plugin", path: "/_fixtures/witnesses/direct-connected", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "witnesses/error": () => ({
        page: renderWitnessOverviewPage({ bootstrapState: fixtureBootstrapOnboarded, witnesses: [], witnessError: "Failed to load hosted witness rows. The boot service returned HTTP 503.", onLoadBootstrap: loadBootstrapFixture(fixtureBootstrapOnboarded), onStartOnboarding: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-witnesses", shellMode: "vault", navMode: "plugin", path: "/_fixtures/witnesses/error", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "watchers/placeholder": () => ({
        page: renderWatcherOverviewPage({ vault: fixtureVault, bootstrapState: fixtureBootstrapDisconnected, watchers: [], watcherError: "", onRefreshStatuses: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-watchers", shellMode: "vault", navMode: "plugin", path: "/_fixtures/watchers/placeholder", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    "watchers/populated": () => ({
        page: renderWatcherOverviewPage({ vault: fixtureVault, bootstrapState: fixtureBootstrapOnboarded, watchers: fixtureWatchers, watcherError: "", onRefreshStatuses: asyncNoop }),
        vault: fixtureVault,
        route: { name: "kf-watchers", shellMode: "vault", navMode: "plugin", path: "/_fixtures/watchers/populated", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),

    settings: () => ({
        page: renderSettingsPage({ vault: fixtureVault, settings: fixtureSettings }),
        vault: fixtureVault,
        route: { name: "settings", shellMode: "vault", navMode: "core", path: "/_fixtures/settings", params: { vaultId: FIXTURE_VAULT_ID_CONST } },
    }),
};

export function isFixtureRoute(path: string): boolean {
    return path.startsWith("/_fixtures/");
}

export function loadFixture(path: string): FixtureRecord | null {
    const key = path.replace(/^\/_fixtures\//, "");
    const factory = FIXTURES[key];
    return factory ? factory() : null;
}

export function listFixtureKeys(): string[] {
    return Object.keys(FIXTURES);
}