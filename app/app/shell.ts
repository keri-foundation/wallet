import {
    homeHref,
    identifiersHref,
    kfHomeHref,
    kfIdentifiersHref,
    remotesHref,
    kfWitnessesHref,
    kfWatchersHref,
    settingsHref,
} from "./router.js";
import { menuButtonHtml } from "../shared/components.js";

interface RouteRecord {
    name: string;
    shellMode?: string;
    navMode?: string;
}

interface PageRecord {
    title: string;
    html?: string;
    render?(container: HTMLElement): void;
    setup?(root: HTMLElement): void;
}

interface VaultRecord {
    id: string;
    alias: string;
}

interface ShellActions {
    toggleNav?(): void;
    closeNav?(): void;
    toggleDrawer?(): void;
    lockVault(vaultId: string): Promise<void> | void;
}

interface ShellState {
    mobileNavOpen?: boolean;
    lastCoreRoutes?: Record<string, string>;
}

interface ShellProps {
    route: RouteRecord;
    page: PageRecord;
    state: ShellState;
    vault: VaultRecord | null;
    actions: ShellActions;
}

interface MenuLinkConfig {
    icon: string;
    label: string;
    href: string;
    active?: boolean;
    disabled?: boolean;
}

function isCoreActive(routeName: string, target: string): boolean {
    if (target === "identifiers") {
        return routeName === "identifiers" || routeName === "identifier-detail";
    }
    if (target === "remotes") {
        return routeName === "remotes" || routeName === "remote-detail";
    }
    return routeName === target;
}

function coreSidebarNav(route: RouteRecord, vaultId: string): string {
    const links: MenuLinkConfig[] = [
        {
            label: "Identifiers",
            icon: "./assets/icons/identifiers.png",
            href: identifiersHref(vaultId),
            active: isCoreActive(route.name, "identifiers"),
        },
        {
            label: "Remote Identifiers",
            icon: "./assets/icons/remoteIds.png",
            href: remotesHref(vaultId),
            active: isCoreActive(route.name, "remotes"),
        },
        {
            label: "Group Identifiers",
            icon: "./assets/icons/group.svg",
            href: "#",
            disabled: true,
        },
        {
            label: "Credentials",
            icon: "./assets/icons/badge.svg",
            href: "#",
            disabled: true,
        },
        {
            label: "Settings",
            icon: "./assets/icons/settings.png",
            href: settingsHref(vaultId),
            active: isCoreActive(route.name, "settings"),
        },
    ];

    return `
        <div class="lk-sidebar-shell" data-nav-shell>
            <nav class="lk-sidebar" aria-label="Vault navigation">
                <div class="lk-sidebar__nav">
                    ${links.map((link) => menuButtonHtml(link)).join("")}
                </div>
                <div class="lk-sidebar__plugin-entry">
                    <div class="lk-sidebar__divider lk-sidebar__divider--plugin"></div>
                    ${menuButtonHtml({
                        label: "KERI Foundation",
                        icon: "./assets/brand/SymbolLogo.svg",
                        href: kfHomeHref(vaultId),
                    })}
                </div>
            </nav>
        </div>
    `;
}

function pluginSidebarNav(route: RouteRecord, vaultId: string, backHref: string): string {
    const links: MenuLinkConfig[] = [
        {
            label: "Identifiers",
            icon: "./assets/icons/identifiers.png",
            href: kfIdentifiersHref(vaultId),
            active: route.name === "kf-identifiers",
        },
        {
            label: "Witnesses",
            icon: "./assets/icons/witness1.svg",
            href: kfWitnessesHref(vaultId),
            active: route.name === "kf-witnesses",
        },
        {
            label: "Watchers",
            icon: "./assets/icons/watcher.svg",
            href: kfWatchersHref(vaultId),
            active: route.name === "kf-watchers",
        },
    ];

    return `
        <div class="lk-sidebar-shell" data-nav-shell>
            <nav class="lk-sidebar" aria-label="KERI Foundation navigation">
                <a class="lk-sidebar__back" href="${backHref}">
                    <img src="./assets/icons/arrow_back.svg" alt="" width="24" height="24">
                    <span>Back</span>
                </a>
                <a
                    class="lk-sidebar__plugin-brand-link"
                    href="${kfHomeHref(vaultId)}"
                    aria-current="${route.name === "kf-home" ? "page" : "false"}"
                >
                    <img src="./assets/brand/SymbolLogo.svg" alt="" width="80" height="80">
                    <span class="lk-sidebar__plugin-brand-label">KERI Foundation</span>
                </a>
                <div class="lk-sidebar__nav lk-sidebar__nav--plugin">
                    ${links.map((link) => menuButtonHtml(link)).join("")}
                </div>
            </nav>
        </div>
    `;
}

function sidebarNav(route: RouteRecord, vaultId: string, state: ShellState): string {
    if (route.navMode === "plugin") {
        return pluginSidebarNav(
            route,
            vaultId,
            state?.lastCoreRoutes?.[vaultId] || identifiersHref(vaultId),
        );
    }

    return coreSidebarNav(route, vaultId);
}

export function renderShell(root: HTMLElement, { route, page, state, vault, actions }: ShellProps): void {
    const isVaultShell = route.shellMode === "vault" && Boolean(vault);
    const mobileNavOpen = Boolean(state.mobileNavOpen);
    const sidebarMarkup = isVaultShell && vault ? sidebarNav(route, vault.id, state) : "";

    root.innerHTML = `
        <div class="shell">
            <header class="topbar">
                <div class="topbar__leading">
                    ${isVaultShell
                        ? `
                            <button class="icon-button topbar__menu" data-action="toggle-nav" aria-label="Open navigation" aria-expanded="${mobileNavOpen ? "true" : "false"}">
                                <img src="./assets/icons/menu.svg" alt="">
                            </button>
                        `
                        : ""}
                    <a class="topbar__brand-link" href="${homeHref()}">
                        <img src="./assets/brand/SymbolLogo.svg" alt="">
                        <span class="topbar__title">FortWeb</span>
                    </a>
                </div>
                <div class="topbar__actions" role="toolbar" aria-label="Shell actions">
                    ${isVaultShell && vault
                        ? `
                            <button class="icon-button" type="button" aria-label="Notifications unavailable in this slice" disabled>
                                <img src="./assets/icons/notifications.svg" alt="">
                            </button>
                            <a class="icon-button" href="${settingsHref(vault.id)}" aria-label="Settings">
                                <img src="./assets/icons/settings.svg" data-hover-src="./assets/icons/settings-hover.svg" alt="">
                            </a>
                            <button class="icon-button" data-action="lock-vault" aria-label="Lock vault">
                                <img src="./assets/icons/lock.svg" data-hover-src="./assets/icons/lock-hover.svg" alt="">
                            </button>
                        `
                        : `
                            <button class="icon-button" data-action="toggle-drawer" aria-label="Vaults">
                                <img src="./assets/icons/vault-drawer.svg" data-hover-src="./assets/icons/vault-drawer-hover.svg" alt="">
                            </button>
                        `}
                </div>
            </header>
            <div class="shell__body ${isVaultShell ? "shell__body--vault" : "shell__body--home"}">
                ${isVaultShell && vault
                    ? `<button class="lk-sidebar-overlay ${mobileNavOpen ? "is-open" : ""}" type="button" data-action="close-nav" aria-label="Close navigation"></button>`
                    : ""}
                ${sidebarMarkup}
                <main class="shell__content">
                    <div class="shell__content-inner">
                        <div data-page-content></div>
                    </div>
                </main>
            </div>
        </div>
    `;

    const pageRoot = root.querySelector("[data-page-content]");
    if (!(pageRoot instanceof HTMLElement)) {
        return;
    }

    pageRoot.replaceChildren();
    if (typeof page.render === "function") {
        page.render(pageRoot);
    } else {
        pageRoot.innerHTML = page.html || "";
    }
    document.title = `${page.title} | FortWeb`;

    root.querySelectorAll("[data-action='toggle-nav']").forEach((button) => {
        button.addEventListener("click", () => actions.toggleNav?.());
    });

    root.querySelectorAll("[data-action='close-nav']").forEach((button) => {
        button.addEventListener("click", () => actions.closeNav?.());
    });

    root.querySelectorAll("[data-nav-shell]").forEach((nav) => {
        nav.classList.toggle("is-open", mobileNavOpen);
    });

    root.querySelectorAll("[data-hover-src]").forEach((node) => {
        if (!(node instanceof HTMLImageElement)) {
            return;
        }

        const defaultSrc = node.getAttribute("src");
        const hoverSrc = node.dataset.hoverSrc;
        const button = node.closest(".icon-button");
        if (!defaultSrc || !hoverSrc || !(button instanceof HTMLElement) || button.matches("[disabled]")) {
            return;
        }

        button.addEventListener("mouseenter", () => {
            node.setAttribute("src", hoverSrc);
        });
        button.addEventListener("mouseleave", () => {
            node.setAttribute("src", defaultSrc);
        });
        button.addEventListener("focus", () => {
            node.setAttribute("src", hoverSrc);
        });
        button.addEventListener("blur", () => {
            node.setAttribute("src", defaultSrc);
        });
    });

    root.querySelectorAll("[data-action='toggle-drawer']").forEach((button) => {
        button.addEventListener("click", () => actions.toggleDrawer?.());
    });

    root.querySelectorAll("[data-action='lock-vault']").forEach((button) => {
        button.addEventListener("click", async () => {
            if (!vault) {
                return;
            }

            await actions.lockVault(vault.id);
        });
    });

    page.setup?.(pageRoot);
}