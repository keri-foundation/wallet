import { createRuntimeBridge } from "../runtime/bridge.js";
import {
    identifiersHref,
    navigate,
    normalizeHash,
    parseRoute,
    type Route,
    unlockHref,
} from "./router.js";
import { renderErrorPage, renderNotFoundRoute } from "./page-feedback.js";
import { loadPage } from "./page-factory.js";
import { createSessionStore, type SessionState } from "./session.js";
import { renderShell } from "./shell.js";
import {
    createVaultDrawer,
    createDialog,
    floatingInputHtml,
    setupFloatingInputs,
} from "../shared/components.js";
import { isFixtureRoute, loadFixture } from "../fixtures/fixture-router.js";
import { renderFixtureIndexPage } from "../fixtures/fixture-index-page.js";
import { installGlobalHandlers } from "../runtime/global-handlers.js";
import { METHODS } from "../runtime/method-catalog.js";
import { postLog } from "../runtime/logger.js";
import {
    describeRuntimeOriginContract,
    isRuntimeOriginContractRequired,
    readWindowRuntimeOriginContract,
    RuntimeOriginContractError,
} from "../runtime/origin-contract.js";

type ShellProps = Parameters<typeof renderShell>[1];
type ShellVault = ShellProps["vault"];
type PageFactoryContext = Parameters<typeof loadPage>[0];
type LoadPageActions = Parameters<typeof loadPage>[0]["actions"];

interface VaultRecord {
    id: string;
    alias: string;
    storageName?: string;
    createdAt: string;
    identifierCount?: number;
    remoteCount?: number;
    locked?: boolean;
}

type VaultSummary = {
    identifierCount?: number;
    remoteCount?: number;
    [key: string]: unknown;
} | null;

interface AppSessionState extends SessionState {
    vaults: VaultRecord[];
    vaultSummary: VaultSummary;
    remoteFilter: string;
}

type AppActions = LoadPageActions & ShellProps["actions"] & {
    createVault(name: string, passcode?: string): Promise<VaultRecord>;
    refreshVaults(unlockedVaultId?: string | null, vaultSummary?: VaultSummary): Promise<VaultRecord[]>;
    toggleNav(): void;
    closeNav(): void;
};

type BootstrapState = Awaited<ReturnType<LoadPageActions["loadKfBootstrap"]>>;
type RuntimeError = { code?: string; message?: string };

function assumeType<T>(value: unknown): T {
    return value as T;
}

function errorMessage(error: unknown): string {
    if (error instanceof Error) {
        return error.message;
    }

    if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
        return error.message;
    }

    return String(error);
}

function errorCode(error: unknown): string {
    if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
        return error.code;
    }

    return "";
}

const rootNode = document.querySelector("#app-root");
if (!(rootNode instanceof HTMLElement)) {
    throw new Error("Expected #app-root host element.");
}
const root = rootNode;

function readRuntimeOriginContractOrRenderStartupError() {
    try {
        return readWindowRuntimeOriginContract();
    } catch (error) {
        const message = error instanceof RuntimeOriginContractError
            ? error.message
            : "Runtime origin contract was invalid.";
        postLog("runtime_origin_contract_invalid", {
            level: "error",
            message,
        });
        renderErrorPage({ message }).render?.(root);
        throw error;
    }
}

const runtimeOriginContract = readRuntimeOriginContractOrRenderStartupError();
if (runtimeOriginContract) {
    postLog("runtime_origin_contract_present", describeRuntimeOriginContract(runtimeOriginContract));
} else {
    if (isRuntimeOriginContractRequired(window.location)) {
        const message = "Runtime origin contract is required outside local browser development.";
        postLog("runtime_origin_contract_missing_blocked", {
            level: "error",
            message,
        });
        renderErrorPage({ message }).render?.(root);
        throw new Error(message);
    }
    postLog("runtime_origin_contract_missing", {
        level: "info",
        fallback: "browser_defaults",
    });
}

const bridge = createRuntimeBridge({
    workerUrl: runtimeOriginContract?.workerUrl ?? new URL("../runtime/wallet-worker.py", import.meta.url),
    configUrl: runtimeOriginContract?.configUrl ?? new URL("../../pyscript-ci.toml", import.meta.url),
    runtimeOriginContract,
});
const session = createSessionStore({
    vaultSummary: null,
    vaults: [],
    remoteFilter: "all",
});

let drawer: ReturnType<typeof createVaultDrawer> | null = null;
let actions: AppActions;
let vaultsLoaded = false;
let initialVaultError: RuntimeError | null = null;

function currentState(): AppSessionState {
    return assumeType<AppSessionState>(session.snapshot());
}

function requireUnlockedVaultId(): string {
    const vaultId = currentState().unlockedVaultId;
    if (!vaultId) {
        throw new Error("Open a vault before continuing.");
    }
    return vaultId;
}

function findVault(vaultId?: string): VaultRecord | null {
    return currentState().vaults.find((vault) => vault.id === vaultId) || null;
}

function isUnlocked(vaultId: string): boolean {
    return currentState().unlockedVaultId === vaultId;
}

function decorateVaults(
    vaults: VaultRecord[],
    unlockedVaultId = currentState().unlockedVaultId,
    vaultSummary = currentState().vaultSummary,
): VaultRecord[] {
    return vaults.map((vault) => {
        const isCurrent = unlockedVaultId === vault.id;
        return {
            ...vault,
            locked: !isCurrent,
            identifierCount: isCurrent ? vaultSummary?.identifierCount ?? 0 : vault.identifierCount ?? 0,
            remoteCount: isCurrent ? vaultSummary?.remoteCount ?? 0 : vault.remoteCount ?? 0,
        };
    });
}

function rememberCoreRoute(route: Route): void {
    const vaultId = route.params.vaultId;
    if (!vaultId) {
        return;
    }

    if (
        route.name === "identifiers" ||
        route.name === "identifier-detail" ||
        route.name === "remotes" ||
        route.name === "remote-detail" ||
        route.name === "settings"
    ) {
        session.rememberCoreRoute(vaultId, window.location.hash || identifiersHref(vaultId));
    }
}

function showCreateVaultDialog(): void {
    const dialog = createDialog({
        title: "Vault Initialization",
        showClose: true,
        showDivider: true,
        rootClassName: "lk-dialog-root--sheet",
        surfaceClassName: "lk-dialog--sheet",
        content: `
            <form data-create-vault-form style="display:flex;flex-direction:column;gap:16px;padding:16px 0;">
                ${floatingInputHtml({ label: "Name", name: "name" })}
                ${floatingInputHtml({ label: "Passcode", name: "passcode", password: true })}
                <p class="muted">
                    Use this passcode to open the vault.
                </p>
                <p class="status-line" data-create-vault-status></p>
            </form>
        `,
        buttons: `
            <button class="button button--secondary" type="button" data-dialog-cancel>Cancel</button>
            <button class="button button--primary" type="button" data-dialog-submit>Create</button>
        `,
        showOverlay: true,
    });

    dialog.show();
    setupFloatingInputs(dialog.el);

    const formNode = dialog.el.querySelector("[data-create-vault-form]");
    const statusLineNode = dialog.el.querySelector("[data-create-vault-status]");
    const submitBtnNode = dialog.el.querySelector("[data-dialog-submit]");
    const cancelBtnNode = dialog.el.querySelector("[data-dialog-cancel]");

    if (!(formNode instanceof HTMLFormElement)) {
        throw new Error("Create vault form is missing.");
    }
    if (!(statusLineNode instanceof HTMLElement)) {
        throw new Error("Create vault status line is missing.");
    }
    if (!(submitBtnNode instanceof HTMLButtonElement) || !(cancelBtnNode instanceof HTMLButtonElement)) {
        throw new Error("Create vault buttons are missing.");
    }

    const form = formNode;
    const statusLine = statusLineNode;
    const submitBtn = submitBtnNode;
    const cancelBtn = cancelBtnNode;

    cancelBtn.addEventListener("click", () => dialog.close());

    async function submit(): Promise<void> {
        const formData = new FormData(form);
        submitBtn.disabled = true;
        cancelBtn.disabled = true;
        statusLine.textContent = "";
        submitBtn.textContent = "Creating...";
        statusLine.textContent = "Creating vault...";

        try {
            await actions.createVault(
                String(formData.get("name") || ""),
                String(formData.get("passcode") || ""),
            );
            dialog.close();
        } catch (error) {
            submitBtn.disabled = false;
            cancelBtn.disabled = false;
            submitBtn.textContent = "Create";
            statusLine.textContent = errorMessage(error) || "Vault creation failed.";
        }
    }

    submitBtn.addEventListener("click", () => {
        void submit();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        void submit();
    });
}

function initDrawer(vaults: VaultRecord[]): void {
    drawer = createVaultDrawer({
        vaults,
        onVaultClick(vault: VaultRecord) {
            drawer?.close();
            if (isUnlocked(vault.id)) {
                navigate(currentState().lastCoreRoutes[vault.id] || identifiersHref(vault.id));
                return;
            }
            navigate(unlockHref(vault.id));
        },
        onNewVault() {
            showCreateVaultDialog();
        },
    });
}

actions = {
    async refreshVaults(
        unlockedVaultId = currentState().unlockedVaultId,
        vaultSummary = currentState().vaultSummary,
    ): Promise<VaultRecord[]> {
        const { vaults } = await bridge.request<{ vaults: VaultRecord[] }>(METHODS.vaultsList);
        const decorated = decorateVaults(vaults, unlockedVaultId, vaultSummary);
        session.patch({ vaults: decorated });
        drawer?.refresh(decorated);
        vaultsLoaded = true;
        initialVaultError = null;
        return decorated;
    },

    async openVault(vaultId: string, passcode = ""): Promise<Record<string, unknown>> {
        const { vault } = await bridge.request<{ vault: Record<string, unknown> }>(METHODS.vaultsOpen, { vaultId, passcode });
        session.patch({
            unlockedVaultId: vaultId,
            vaultSummary: assumeType<VaultSummary>(vault),
            mobileNavOpen: false,
        });
        await actions.refreshVaults(vaultId, assumeType<VaultSummary>(vault));
        drawer?.close();
        navigate(currentState().lastCoreRoutes[vaultId] || identifiersHref(vaultId));
        return vault;
    },

    async createVault(name: string, passcode = ""): Promise<VaultRecord> {
        const currentVaultId = currentState().unlockedVaultId;
        const { vault } = await bridge.request<{ vault: VaultRecord }>(METHODS.vaultsCreate, { name, passcode });

        if (currentVaultId) {
            await bridge.request(METHODS.vaultsClose, { vaultId: currentVaultId }).catch(() => {});
        }

        session.patch({
            unlockedVaultId: null,
            vaultSummary: null,
            mobileNavOpen: false,
        });
        await actions.refreshVaults(null, null);
        navigate(unlockHref(vault.id));
        return vault;
    },

    async lockVault(vaultId: string): Promise<void> {
        if (isUnlocked(vaultId)) {
            await bridge.request(METHODS.vaultsClose, { vaultId }).catch(() => {});
        }

        session.patch({
            unlockedVaultId: null,
            vaultSummary: null,
            mobileNavOpen: false,
        });
        await actions.refreshVaults(null, null).catch(() => null);
        navigate(unlockHref(vaultId));
    },

    async createIdentifier(alias: string): Promise<void> {
        const vaultId = requireUnlockedVaultId();
        await bridge.request(METHODS.identifiersCreate, { vaultId, alias });
        const { vault } = await bridge.request<{ vault: VaultSummary }>(METHODS.vaultsSummary, { vaultId });
        session.patch({ vaultSummary: vault });
        await actions.refreshVaults(vaultId, vault);
        await render();
    },

    async resolveRemoteOobi(url: string, alias: string): Promise<void> {
        const vaultId = requireUnlockedVaultId();
        await bridge.request(METHODS.remotesResolveOobi, { vaultId, url, alias });
        const { vault } = await bridge.request<{ vault: VaultSummary }>(METHODS.vaultsSummary, { vaultId });
        session.patch({ vaultSummary: vault });
        await actions.refreshVaults(vaultId, vault);
        await render();
    },

    async updateRemote(aid: string, patch: Record<string, unknown>): Promise<void> {
        const vaultId = requireUnlockedVaultId();
        await bridge.request(METHODS.remotesUpdate, { vaultId, aid, patch });
        await render();
    },

    async loadKfBootstrap(bootUrl = ""): Promise<BootstrapState> {
        const vaultId = requireUnlockedVaultId();
        return assumeType<BootstrapState>(await bridge.request(METHODS.kfBootstrapGet, { vaultId, bootUrl }));
    },

    async startKfOnboarding({ bootUrl, alias, witnessProfileCode, accountAid = "" }): Promise<void> {
        const vaultId = requireUnlockedVaultId();
        await bridge.request(METHODS.kfOnboardingStart, {
            vaultId,
            bootUrl,
            alias,
            witnessProfileCode,
            accountAid,
        });
        await render();
    },

    async refreshKfWatcherStatuses(watcherEids: string[] = []): Promise<void> {
        const vaultId = requireUnlockedVaultId();
        for (const watcherEid of watcherEids) {
            await bridge.request(METHODS.kfAccountWatchersStatus, {
                vaultId,
                watcherEid,
            });
        }
        await render();
    },

    setRemoteFilter(filter: string): void {
        session.patch({ remoteFilter: filter });
        void render();
    },

    toggleNav(): void {
        session.patch({
            mobileNavOpen: !currentState().mobileNavOpen,
        });
        void render();
    },

    closeNav(): void {
        if (!currentState().mobileNavOpen) {
            return;
        }
        session.patch({ mobileNavOpen: false });
        void render();
    },

    async toggleDrawer(): Promise<void> {
        if (!drawer) {
            return;
        }
        if (drawer.isOpen) {
            drawer.close();
        } else {
            drawer.open();
            try {
                await actions.refreshVaults(currentState().unlockedVaultId, currentState().vaultSummary);
            } catch (error) {
                postLog("vault_refresh_failed", {
                    level: "warning",
                    code: errorCode(error),
                    message: errorMessage(error),
                });
            }
        }
    },
};

/** Incremented on every render(); stale renders bail so tab taps do not stack concurrent bridge calls. */
let renderGeneration = 0;

async function render(): Promise<void> {
    const thisGeneration = ++renderGeneration;
    const path = normalizeHash();

    if (path === "/_fixtures" || path === "/_fixtures/") {
        const indexRoute = { name: "fixture-index", shellMode: "home", navMode: "none", path, params: {} };
        renderShell(root, {
            route: indexRoute,
            page: renderFixtureIndexPage(),
            state: currentState(),
            vault: null,
            actions,
        });
        return;
    }

    if (isFixtureRoute(path)) {
        const fixture = loadFixture(path);
        if (fixture) {
            renderShell(root, {
                route: fixture.route,
                page: fixture.page,
                state: currentState(),
                vault: assumeType<ShellVault>(fixture.vault),
                actions,
            });
        } else {
            const fallbackRoute = { name: "not-found", shellMode: "home", navMode: "none", path, params: {} };
            renderNotFoundRoute({ root, route: fallbackRoute, state: currentState(), vault: null, actions });
        }
        return;
    }

    const route = parseRoute();
    const state = currentState();
    const vault = route.requiresVault ? findVault(route.params.vaultId) : null;

    if (route.name !== "home" && route.requiresVault && !vault) {
        if (!vaultsLoaded) {
            renderShell(root, {
                route: { ...route, shellMode: "home", navMode: "none" },
                page: initialVaultError ? renderErrorPage(initialVaultError) : {
                    title: "Loading Vault",
                    html: '<section class="placeholder-card"><p class="muted" role="status" aria-live="polite">Loading vault...</p></section>',
                },
                state,
                vault: null,
                actions,
            });
            return;
        }
        renderNotFoundRoute({ root, route, state, vault: null, actions });
        return;
    }

    const vaultId = route.params.vaultId;
    if (route.name === "unlock" && vaultId && isUnlocked(vaultId)) {
        navigate(currentState().lastCoreRoutes[vaultId] || identifiersHref(vaultId));
        return;
    }

    if (route.requiresUnlock && vaultId && !isUnlocked(vaultId)) {
        navigate(unlockHref(vaultId));
        return;
    }

    rememberCoreRoute(route);

    try {
        if (thisGeneration !== renderGeneration) {
            return;
        }
        const result = await loadPage({
            route,
            bridge,
            currentState,
            findVault(vaultId?: string) {
                return assumeType<Record<string, unknown> | null>(findVault(vaultId));
            },
            isUnlocked,
            showCreateVaultDialog,
            actions,
        });
        if (thisGeneration !== renderGeneration) {
            return;
        }
        if (result.redirectHref) {
            navigate(result.redirectHref);
            return;
        }
        renderShell(root, {
            route,
            page: result.page!,
            state: currentState(),
            vault: assumeType<ShellVault>(result.vault),
            actions,
        });
    } catch (error) {
        if (thisGeneration !== renderGeneration) {
            return;
        }
        postLog("render_error", {
            level: "error",
            code: errorCode(error),
            message: errorMessage(error),
            route: route.name,
            path: route.path,
        });
        const code = errorCode(error);
        if (code === "NOT_FOUND") {
            renderNotFoundRoute({ root, route, state: currentState(), vault: assumeType<ShellVault>(vault), actions });
            return;
        }
        if ((code === "LOCKED" || code === "TIMEOUT") && route.params.vaultId) {
            session.patch({
                unlockedVaultId: null,
                vaultSummary: null,
                mobileNavOpen: false,
            });
            await actions.refreshVaults(null, null).catch(() => null);
            if (thisGeneration !== renderGeneration) {
                return;
            }
            navigate(unlockHref(route.params.vaultId));
            return;
        }

        renderShell(root, {
            route: route.shellMode ? route : { ...route, shellMode: "home" },
            page: renderErrorPage(assumeType<RuntimeError>(error)),
            state: currentState(),
            vault: assumeType<ShellVault>(vault),
            actions,
        });
    }
}

window.addEventListener("hashchange", () => {
    postLog("route_change", { path: normalizeHash() });
    session.patch({ mobileNavOpen: false });
    void render();
});

window.addEventListener("beforeunload", () => {
    bridge.destroy();
});

async function bootstrap(): Promise<void> {
    installGlobalHandlers();
    initDrawer(currentState().vaults);
    await render();

    try {
        await actions.refreshVaults();
        await render();
    } catch (error) {
        postLog("initial_vault_refresh_failed", {
            level: "warning",
            code: errorCode(error),
            message: errorMessage(error),
        });
        initialVaultError = { message: errorMessage(error) };
        await render();
    }
}

void bootstrap();
