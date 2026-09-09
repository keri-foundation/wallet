/**
 * Shared UI components mirroring locksmith's Qt toolkit.
 *
 * Each component returns a { html, setup(root) } object or a controller API,
 * following the same pattern as fortweb page modules.
 */

import { escapeHtml } from "./dom.js";

interface DialogOptions {
    title?: string;
    titleIcon?: string;
    showClose?: boolean;
    showDivider?: boolean;
    content?: string;
    buttons?: string;
    showOverlay?: boolean;
    rootClassName?: string;
    surfaceClassName?: string;
}

interface DialogController {
    el: HTMLDivElement;
    show(): void;
    close(): void;
}

interface VaultDrawerRecord {
    id: string;
    alias: string;
    locked?: boolean;
    identifierCount?: number;
    remoteCount?: number;
}

interface VaultDrawerOptions<Vault extends VaultDrawerRecord> {
    vaults: Vault[];
    onVaultClick(vault: Vault): void;
    onNewVault?(): void;
}

interface VaultDrawerController<Vault extends VaultDrawerRecord> {
    el: HTMLDivElement;
    open(): void;
    close(): void;
    readonly isOpen: boolean;
    refresh(vaults: Vault[]): void;
}

interface FloatingInputOptions {
    label: string;
    name: string;
    type?: string;
    password?: boolean;
}

interface TableColumn {
    key: string;
    label: string;
    width?: string;
    searchKey?: string;
    html?: boolean;
}

interface TableAction {
    key: string;
    label: string;
    icon?: string;
}

interface PaginatedTableOptions<Row> {
    icon?: string;
    title?: string;
    titleTag?: "h1" | "h2";
    titleMetaHtml?: string;
    collapseHeadingOnMobile?: boolean;
    searchPlaceholder?: string;
    addButtonText?: string;
    columns?: TableColumn[];
    rows: Row[];
    rowActions?: TableAction[];
    itemsPerPage?: number;
    emptyTitle?: string;
    emptyText?: string;
    onAdd?(): void;
    onAction?(row: Row, actionKey: string): void;
}

interface PaginatedTableController {
    html: string;
    setup(root: ParentNode | null): void;
}

interface MenuButtonOptions {
    icon: string;
    label: string;
    href: string;
    active?: boolean;
    disabled?: boolean;
}

let floatingInputCounter = 0;

export function createDialog(opts: DialogOptions): DialogController {
    const {
        title = "",
        titleIcon = "",
        showClose = true,
        showDivider = true,
        content = "",
        buttons = "",
        showOverlay = false,
        rootClassName = "",
        surfaceClassName = "",
    } = opts;

    const el = document.createElement("div");
    el.className = [
        "lk-dialog-root",
        showOverlay ? "lk-dialog-root--modal" : "lk-dialog-root--floating",
        rootClassName,
    ].filter(Boolean).join(" ");
    el.innerHTML = `
        ${showOverlay ? '<div class="lk-dialog-overlay"></div>' : ""}
        <div class="lk-dialog ${surfaceClassName}" role="dialog" aria-modal="${showOverlay ? "true" : "false"}">
            <div class="lk-dialog__container">
                ${title || titleIcon || showClose
            ? `<div class="lk-dialog__header">
                            ${titleIcon ? `<img class="lk-dialog__icon" src="${titleIcon}" alt="">` : ""}
                            <span class="lk-dialog__title" data-dialog-title></span>
                            <span class="lk-dialog__spacer"></span>
                            ${showClose ? '<button class="lk-dialog__close" aria-label="Close"><img src="./assets/icons/close.svg" alt=""></button>' : ""}
                        </div>`
            : ""
        }
                ${showDivider ? '<div class="lk-dialog__divider"></div>' : ""}
                <div class="lk-dialog__content">${content}</div>
                ${buttons ? `<div class="lk-dialog__buttons">${buttons}</div>` : ""}
            </div>
        </div>
    `;

    const titleEl = el.querySelector("[data-dialog-title]");
    if (titleEl instanceof HTMLElement) {
        titleEl.textContent = title;
    }

    function show(): void {
        document.body.appendChild(el);
        el.offsetHeight;
        el.classList.add("is-visible");
    }

    function close(): void {
        el.remove();
    }

    el.querySelector(".lk-dialog__close")?.addEventListener("click", close);
    el.querySelector(".lk-dialog-overlay")?.addEventListener("click", close);

    return { el, show, close };
}

export function createVaultDrawer<Vault extends VaultDrawerRecord>(opts: VaultDrawerOptions<Vault>): VaultDrawerController<Vault> {
    const { onVaultClick, onNewVault } = opts;
    let vaults = [...opts.vaults];

    const el = document.createElement("div");
    el.className = "lk-drawer-root";
    el.innerHTML = `
        <div class="lk-drawer-overlay"></div>
        <aside class="lk-drawer" aria-label="Vault switcher" role="dialog" aria-modal="true">
            <div class="lk-drawer__header">
                <div class="lk-drawer__heading">
                    <img src="./assets/brand/SymbolLogo.svg" alt="" width="36" height="36">
                    <div>
                        <span class="lk-drawer__title">Vaults</span>
                        <p class="lk-drawer__subtitle">Switch vaults or start a new one.</p>
                    </div>
                </div>
                <button class="lk-drawer__close" type="button" aria-label="Close vault switcher">
                    <img src="./assets/icons/close.svg" alt="" width="18" height="18">
                </button>
            </div>
            <div class="lk-drawer__divider"></div>
            <button class="lk-drawer__new-vault" type="button">
                <img src="./assets/icons/add.svg" alt="" width="30" height="30">
                <span>Initialize New Vault</span>
            </button>
            <ul class="lk-drawer__list" role="list"></ul>
        </aside>
    `;

    const list = el.querySelector(".lk-drawer__list");
    if (!(list instanceof HTMLUListElement)) {
        throw new Error("Vault drawer list is missing.");
    }

    let isOpen = false;
    let closeTimer: ReturnType<typeof setTimeout> | undefined;

    function renderList(nextVaults: Vault[]): void {
        list.replaceChildren(
            ...nextVaults.map((vault) => {
                const isCurrent = vault.locked === false;
                const item = document.createElement("li");
                item.className = `lk-drawer__item ${isCurrent ? "is-active" : ""}`.trim();
                item.dataset.vaultId = vault.id;
                item.setAttribute("aria-current", isCurrent ? "page" : "false");
                item.setAttribute("role", "button");
                item.tabIndex = 0;

                const icon = document.createElement("img");
                icon.src = "./assets/icons/vault.png";
                icon.alt = "";
                icon.width = 36;
                icon.height = 36;

                const copy = document.createElement("span");
                copy.className = "lk-drawer__item-copy";

                const heading = document.createElement("span");
                heading.className = "lk-drawer__item-heading";

                const label = document.createElement("span");
                label.className = "lk-drawer__item-title";
                label.textContent = vault.alias;

                heading.append(label);

                if (isCurrent) {
                    const status = document.createElement("span");
                    status.className = "lk-drawer__item-status";
                    status.textContent = "Current";
                    heading.append(status);
                }

                const meta = document.createElement("span");
                meta.className = "lk-drawer__item-meta";
                meta.textContent = `${vault.identifierCount ?? 0} identifiers · ${vault.remoteCount ?? 0} remotes`;

                copy.append(heading, meta);
                item.append(icon, copy);
                return item;
            }),
        );
    }

    function open(): void {
        if (isOpen) {
            return;
        }
        if (closeTimer !== undefined) {
            clearTimeout(closeTimer);
            closeTimer = undefined;
        }
        isOpen = true;
        document.body.appendChild(el);
        el.offsetHeight;
        el.classList.add("is-open");
    }

    function close(): void {
        if (!isOpen) {
            return;
        }
        isOpen = false;
        el.classList.remove("is-open");
        closeTimer = setTimeout(() => {
            el.remove();
            closeTimer = undefined;
        }, 300);
    }

    function refresh(nextVaults: Vault[]): void {
        vaults = [...nextVaults];
        renderList(vaults);
    }

    const overlay = el.querySelector(".lk-drawer-overlay");
    const closeButton = el.querySelector(".lk-drawer__close");
    const newVaultButton = el.querySelector(".lk-drawer__new-vault");

    if (!(overlay instanceof HTMLElement) || !(closeButton instanceof HTMLButtonElement) || !(newVaultButton instanceof HTMLButtonElement)) {
        throw new Error("Vault drawer controls are missing.");
    }

    overlay.addEventListener("click", close);
    closeButton.addEventListener("click", close);
    newVaultButton.addEventListener("click", () => {
        close();
        onNewVault?.();
    });

    list.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const item = target.closest(".lk-drawer__item");
        if (!(item instanceof HTMLElement)) {
            return;
        }
        const vault = vaults.find((entry) => entry.id === item.dataset.vaultId);
        if (vault) {
            onVaultClick(vault);
        }
    });
    list.addEventListener("keydown", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const item = target.closest(".lk-drawer__item");
        if (!(item instanceof HTMLElement) || (event.key !== "Enter" && event.key !== " ")) {
            return;
        }
        event.preventDefault();
        const vault = vaults.find((entry) => entry.id === item.dataset.vaultId);
        if (vault) {
            onVaultClick(vault);
        }
    });

    renderList(vaults);

    return { el, open, close, refresh, get isOpen() { return isOpen; } };
}

export function floatingInputHtml({ label, name, type = "text", password = false }: FloatingInputOptions): string {
    const inputType = password ? "password" : type;
    const inputId = `lk-field-${name}-${floatingInputCounter++}`;
    return `
        <div class="lk-float-field">
            <input class="lk-float-field__input" type="${inputType}" name="${escapeHtml(name)}" id="${escapeHtml(inputId)}" placeholder=" " autocomplete="off">
            <label class="lk-float-field__label" for="${escapeHtml(inputId)}">${escapeHtml(label)}</label>
            ${password ? `<button type="button" class="lk-float-field__toggle" data-toggle-password="${escapeHtml(name)}" aria-label="Toggle password visibility"><img src="./assets/icons/browse.svg" alt="" width="20" height="20"></button>` : ""}
        </div>
    `;
}

export function setupFloatingInputs(root: ParentNode | null): void {
    if (!root) {
        return;
    }

    root.querySelectorAll("[data-toggle-password]").forEach((button) => {
        if (!(button instanceof HTMLElement)) {
            return;
        }

        button.addEventListener("click", () => {
            const name = button.dataset.togglePassword;
            if (!name) {
                return;
            }

            const input = root.querySelector(`input[name="${name}"]`);
            if (!(input instanceof HTMLInputElement)) {
                return;
            }

            input.type = input.type === "password" ? "text" : "password";
        });
    });
}

export function renderPaginatedTable<Row>(opts: PaginatedTableOptions<Row>): PaginatedTableController {
    const {
        icon = "",
        title = "",
        titleTag = "h2",
        titleMetaHtml = "",
        collapseHeadingOnMobile = false,
        searchPlaceholder = "Search...",
        addButtonText = "",
        columns = [],
        rows,
        rowActions = [],
        itemsPerPage = 10,
        emptyTitle = "No Records Yet",
        emptyText = "Nothing has been stored for this view yet.",
    } = opts;

    const hasActions = rowActions.length > 0;
    const allColumns = hasActions ? [...columns, { key: "_actions", label: "Actions", width: "100px" }] : columns;
    const safeTitleTag = titleTag === "h1" ? "h1" : "h2";
    const sectionClassName = collapseHeadingOnMobile ? "lk-table-section lk-table-section--collapse-heading-mobile" : "lk-table-section";

    const html = `
        <section class="${sectionClassName}">
            <div class="lk-table-shell">
                <div class="lk-table-header">
                    <div class="lk-table-header__left">
                        ${icon ? `<img class="lk-table-header__icon" src="${icon}" alt="" width="32" height="32">` : ""}
                        <div class="lk-table-header__heading">
                            <${safeTitleTag} class="lk-table-header__title">${escapeHtml(title)}</${safeTitleTag}>
                            ${titleMetaHtml ? `<div class="lk-table-header__meta">${titleMetaHtml}</div>` : ""}
                        </div>
                    </div>
                    <div class="lk-table-header__right">
                        <div class="lk-search-bar">
                            <img class="lk-search-bar__icon" src="./assets/icons/search.svg" alt="" width="18" height="18">
                            <input type="text" class="lk-search-bar__input" placeholder="${escapeHtml(searchPlaceholder)}" data-table-search>
                            <button type="button" class="lk-search-bar__clear" data-table-search-clear aria-label="Clear search" hidden><img src="./assets/icons/close.svg" alt="" width="14" height="14"></button>
                        </div>
                        ${addButtonText
            ? `<button class="button button--primary lk-table-header__action" type="button" data-table-add><img src="./assets/icons/add.svg" alt="" width="18" height="18" style="filter:brightness(0) invert(1);"> ${escapeHtml(addButtonText)}</button>`
            : ""
        }
                    </div>
                </div>
                <div class="lk-table-wrap">
                    <table class="lk-table">
                        <thead>
                            <tr>
                                ${allColumns.map((column) => `<th${column.width ? ` style="width:${column.width}"` : ""} data-sort-col="${column.key}">${escapeHtml(column.label)}</th>`).join("")}
                            </tr>
                        </thead>
                        <tbody data-table-body></tbody>
                    </table>
                </div>
                <div class="lk-table-stack" data-table-stack></div>
                <div class="lk-table-footer">
                    <span class="lk-table-footer__count" data-table-count></span>
                    <div class="lk-table-footer__pagination" data-table-pagination></div>
                </div>
            </div>
        </section>
    `;

    function setup(root: ParentNode | null): void {
        if (!(root instanceof Element)) {
            return;
        }

        const searchInput = root.querySelector("[data-table-search]");
        const searchClear = root.querySelector("[data-table-search-clear]");
        const tbody = root.querySelector("[data-table-body]");
        const stack = root.querySelector("[data-table-stack]");
        const countEl = root.querySelector("[data-table-count]");
        const paginationEl = root.querySelector("[data-table-pagination]");
        const titleColumn = columns[0] || null;

        if (
            !(searchInput instanceof HTMLInputElement) ||
            !(searchClear instanceof HTMLButtonElement) ||
            !(tbody instanceof HTMLElement) ||
            !(stack instanceof HTMLElement) ||
            !(countEl instanceof HTMLElement) ||
            !(paginationEl instanceof HTMLElement)
        ) {
            return;
        }

        function rowValue(row: Row, key: string): unknown {
            return (row as Record<string, unknown>)[key];
        }

        function displayValue(row: Row, column: TableColumn): string {
            const value = rowValue(row, column.key) ?? "";
            if (column.html) {
                return escapeHtml(String(rowValue(row, column.searchKey || column.key) ?? ""));
            }
            return escapeHtml(String(value));
        }

        function renderCard(row: Row, rowIdx: number): string {
            const titleValue = titleColumn
                ? escapeHtml(String(rowValue(row, titleColumn.searchKey || titleColumn.key) ?? ""))
                : "Record";
            const detailFields = columns.slice(1).map((column) => `
                <div class="detail-item ${column.key === "lastEventDigest" ? "detail-item--span" : ""}">
                    <dt>${escapeHtml(column.label)}</dt>
                    <dd class="${column.key === "prefix" || column.key === "lastEventDigest" ? "mono" : ""}">${displayValue(row, column)}</dd>
                </div>
            `).join("");
            const actionsHtml = rowActions.length
                ? `<div class="stack-card__actions">
                    ${rowActions.map((action) => `
                        <button class="button button--ghost stack-card__action-button" type="button" data-action-key="${action.key}" data-row-idx="${rowIdx}">
                            ${action.icon ? `<img src="${action.icon}" alt="" width="16" height="16">` : ""}
                            <span>${escapeHtml(action.label)}</span>
                        </button>
                    `).join("")}
                </div>`
                : "";

            return `
                <article class="stack-card">
                    <div class="stack-card__header">
                        <div class="stack-card__title-block">
                            <span class="stack-card__eyebrow">${escapeHtml(titleColumn?.label || "Record")}</span>
                            <h3 class="stack-card__title">${titleValue}</h3>
                        </div>
                    </div>
                    <dl class="detail-grid stack-card__details">
                        ${detailFields}
                    </dl>
                    ${actionsHtml}
                </article>
            `;
        }

        let filteredRows = [...rows];
        let currentPage = 1;

        function filterRows(query: string): void {
            const normalizedQuery = query.trim().toLowerCase();
            if (!normalizedQuery) {
                filteredRows = [...rows];
            } else {
                filteredRows = rows.filter((row) => {
                    return columns.some((column) => {
                        const value = rowValue(row, column.searchKey || column.key);
                        return value != null && String(value).toLowerCase().includes(normalizedQuery);
                    });
                });
            }
            currentPage = 1;
            render();
        }

        function render(): void {
            const totalPages = Math.max(1, Math.ceil(filteredRows.length / itemsPerPage));
            if (currentPage > totalPages) {
                currentPage = totalPages;
            }

            const start = (currentPage - 1) * itemsPerPage;
            const pageRows = filteredRows.slice(start, start + itemsPerPage);

            if (!pageRows.length) {
                tbody.innerHTML = `
                    <tr class="lk-table__empty-row">
                        <td class="lk-table__empty-cell" colspan="${allColumns.length}">
                            <div class="empty-state empty-state--table">
                                <h2>${escapeHtml(emptyTitle)}</h2>
                                <p>${escapeHtml(emptyText)}</p>
                            </div>
                        </td>
                    </tr>
                `;
                stack.innerHTML = `
                    <div class="empty-state empty-state--table empty-state--stack">
                        <h2>${escapeHtml(emptyTitle)}</h2>
                        <p>${escapeHtml(emptyText)}</p>
                    </div>
                `;
                countEl.textContent = `${filteredRows.length} item${filteredRows.length === 1 ? "" : "s"}`;
                paginationEl.innerHTML = '<span class="lk-page-label">Page 1 of 1</span>';
                return;
            }

            tbody.innerHTML = pageRows
                .map((row, idx) => {
                    const cells = columns
                        .map((column) => {
                            const value = rowValue(row, column.key) ?? "";
                            const mono = column.key === "prefix" ? ' class="mono"' : "";
                            const content = column.html ? String(value) : escapeHtml(String(value));
                            return `<td${mono}>${content}</td>`;
                        })
                        .join("");

                    const actionCell = hasActions
                        ? `<td class="lk-table__actions-cell">
                            <button class="lk-skewer-btn" data-skewer-idx="${start + idx}" aria-label="Actions" aria-haspopup="menu" aria-expanded="false">
                                <img src="./assets/icons/more_vert.svg" alt="" width="20" height="20">
                            </button>
                            <div class="lk-skewer-menu" data-skewer-menu="${start + idx}">
                                ${rowActions.map((action) => `<button class="lk-skewer-menu__item" data-action-key="${action.key}" data-row-idx="${start + idx}">${action.icon ? `<img src="${action.icon}" alt="" width="18" height="18">` : ""} ${escapeHtml(action.label)}</button>`).join("")}
                            </div>
                        </td>`
                        : "";

                    return `<tr class="lk-table__row">${cells}${actionCell}</tr>`;
                })
                .join("");

            stack.innerHTML = pageRows
                .map((row, idx) => renderCard(row, start + idx))
                .join("");

            countEl.textContent = `${filteredRows.length} item${filteredRows.length === 1 ? "" : "s"}`;

            paginationEl.innerHTML = `
                <button class="lk-page-btn" data-page-action="first" ${currentPage <= 1 ? "disabled" : ""}><img src="./assets/icons/first_page.svg" alt="First" width="18" height="18"></button>
                <button class="lk-page-btn" data-page-action="prev" ${currentPage <= 1 ? "disabled" : ""}><img src="./assets/icons/chevron_left.svg" alt="Previous" width="18" height="18"></button>
                <span class="lk-page-label">Page ${currentPage} of ${totalPages}</span>
                <button class="lk-page-btn" data-page-action="next" ${currentPage >= totalPages ? "disabled" : ""}><img src="./assets/icons/chevron_right.svg" alt="Next" width="18" height="18"></button>
                <button class="lk-page-btn" data-page-action="last" ${currentPage >= totalPages ? "disabled" : ""}><img src="./assets/icons/last_page.svg" alt="Last" width="18" height="18"></button>
            `;
        }

        const syncSearchChrome = (): void => {
            searchClear.hidden = !Boolean(searchInput.value.trim());
        };

        searchInput.addEventListener("input", () => {
            syncSearchChrome();
            filterRows(searchInput.value);
        });
        searchClear.addEventListener("click", () => {
            searchInput.value = "";
            syncSearchChrome();
            filterRows("");
        });

        paginationEl.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }

            const button = target.closest("[data-page-action]");
            if (!(button instanceof HTMLButtonElement) || button.disabled) {
                return;
            }

            const totalPages = Math.max(1, Math.ceil(filteredRows.length / itemsPerPage));
            switch (button.dataset.pageAction) {
                case "first":
                    currentPage = 1;
                    break;
                case "prev":
                    currentPage = Math.max(1, currentPage - 1);
                    break;
                case "next":
                    currentPage = Math.min(totalPages, currentPage + 1);
                    break;
                case "last":
                    currentPage = totalPages;
                    break;
                default:
                    return;
            }
            render();
        });

        root.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }

            const skewerButton = target.closest(".lk-skewer-btn");
            if (skewerButton instanceof HTMLButtonElement) {
                const idx = skewerButton.dataset.skewerIdx;
                const menu = idx ? root.querySelector(`[data-skewer-menu="${idx}"]`) : null;
                root.querySelectorAll(".lk-skewer-menu.is-open").forEach((node) => {
                    if (node !== menu && node instanceof HTMLElement) {
                        node.classList.remove("is-open");
                    }
                });
                root.querySelectorAll(".lk-skewer-btn[aria-expanded='true']").forEach((node) => {
                    if (node !== skewerButton && node instanceof HTMLElement) {
                        node.setAttribute("aria-expanded", "false");
                    }
                });
                if (menu instanceof HTMLElement) {
                    menu.classList.toggle("is-open");
                    skewerButton.setAttribute("aria-expanded", menu.classList.contains("is-open") ? "true" : "false");
                }
                return;
            }

            const actionButton = target.closest(".lk-skewer-menu__item");
            if (actionButton instanceof HTMLButtonElement) {
                const rowIdx = Number.parseInt(actionButton.dataset.rowIdx ?? "", 10);
                const actionKey = actionButton.dataset.actionKey;
                if (Number.isNaN(rowIdx) || !actionKey) {
                    return;
                }

                const menu = actionButton.closest(".lk-skewer-menu");
                if (menu instanceof HTMLElement) {
                    menu.classList.remove("is-open");
                }
                const trigger = menu?.previousElementSibling;
                if (trigger instanceof HTMLElement && trigger.classList.contains("lk-skewer-btn")) {
                    trigger.setAttribute("aria-expanded", "false");
                }

                const row = filteredRows[rowIdx];
                if (row) {
                    opts.onAction?.(row, actionKey);
                }
                return;
            }

            const cardActionButton = target.closest(".stack-card__action-button");
            if (cardActionButton instanceof HTMLButtonElement) {
                const rowIdx = Number.parseInt(cardActionButton.dataset.rowIdx ?? "", 10);
                const actionKey = cardActionButton.dataset.actionKey;
                if (Number.isNaN(rowIdx) || !actionKey) {
                    return;
                }

                const row = filteredRows[rowIdx];
                if (row) {
                    opts.onAction?.(row, actionKey);
                }
                return;
            }

            root.querySelectorAll(".lk-skewer-menu.is-open").forEach((node) => {
                if (node instanceof HTMLElement) {
                    node.classList.remove("is-open");
                }
            });
            root.querySelectorAll(".lk-skewer-btn[aria-expanded='true']").forEach((node) => {
                if (node instanceof HTMLElement) {
                    node.setAttribute("aria-expanded", "false");
                }
            });
        });

        root.querySelector("[data-table-add]")?.addEventListener("click", () => opts.onAdd?.());

        syncSearchChrome();
        render();
    }

    return { html, setup };
}

export function menuButtonHtml({ icon, label, href, active = false, disabled = false }: MenuButtonOptions): string {
    if (disabled) {
        return `
            <span class="lk-menu-btn" aria-disabled="true">
                <img src="${icon}" alt="" width="32" height="32">
                <span class="lk-menu-btn__label">${escapeHtml(label)}</span>
            </span>
        `;
    }

    return `
        <a class="lk-menu-btn ${active ? "is-active" : ""}" href="${href}" aria-current="${active ? "page" : "false"}">
            <img src="${icon}" alt="" width="32" height="32">
            <span class="lk-menu-btn__label">${escapeHtml(label)}</span>
        </a>
    `;
}
