import { remoteDetailHref } from "../../app/router.js";
import { renderPaginatedTable } from "../../shared/components.js";
import { escapeHtml } from "../../shared/dom.js";
import { createModal } from "../../ui/composites/modal.js";
import { showToast } from "../../ui/composites/toast.js";
import { fieldTextHtml } from "../../ui/primitives/field-text.js";
import { chipHtml } from "../../ui/primitives/chip.js";

interface VaultRecord {
    id: string;
}

interface RemoteRecord {
    aid: string;
    alias: string;
    prefix: string;
    sequenceNumber: number | null;
    transferable: boolean;
    transferability: string;
    rolesLabel: string;
    status: string;
    org?: string;
    note?: string;
}

interface RemoteTableRow {
    alias: string;
    aliasLink: string;
    prefix: string;
    sequenceNumber: string;
    transferability: string;
    rolesLabel: string;
    status: string;
    _raw: RemoteRecord;
}

interface RemotesPageProps {
    vault: VaultRecord;
    remotes: RemoteRecord[];
    filter: string;
    onResolveRemote(url: string, alias: string): Promise<void>;
    onUpdateRemote(aid: string, patch: Record<string, unknown>): Promise<void>;
    onFilterChange(filter: string): void;
}

function queryInput(root: ParentNode, selector: string): HTMLInputElement | null {
    return root.querySelector(selector) as HTMLInputElement | null;
}

function queryButton(root: ParentNode, selector: string): HTMLButtonElement | null {
    return root.querySelector(selector) as HTMLButtonElement | null;
}

function queryElement(root: ParentNode, selector: string): HTMLElement | null {
    return root.querySelector(selector) as HTMLElement | null;
}

function errorMessage(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
}

function createResolveRemoteDialog(onResolveRemote: RemotesPageProps["onResolveRemote"]): void {
    const modal = createModal({
        title: "Add Remote Identifier",
        body: `
            <form data-resolve-remote-form>
                ${fieldTextHtml({ id: "resolve-oobi-url", label: "OOBI URL", placeholder: "https://...", required: true })}
                ${fieldTextHtml({ id: "resolve-alias", label: "Alias", placeholder: "e.g. remote-peer" })}
                <p class="muted">Enter the OOBI URL shared by the identifier's controller or provider.</p>
                <p class="status-line" data-resolve-remote-status></p>
            </form>
        `,
        actions: [
            { label: "Cancel", tone: "ghost", dataAction: "cancel" },
            { label: "Resolve OOBI", tone: "primary", dataAction: "submit" },
        ],
    });

    modal.open();

    const root = document.querySelector("[role='dialog'][aria-label='Add Remote Identifier']");
    if (!(root instanceof HTMLElement)) {
        return;
    }

    const form = root.querySelector("[data-resolve-remote-form]");
    const statusLine = queryElement(root, "[data-resolve-remote-status]");
    const submitBtn = queryButton(root, "[data-action='submit']");
    const cancelBtn = queryButton(root, "[data-action='cancel']");

    if (!(form instanceof HTMLFormElement) || !statusLine || !submitBtn) {
        return;
    }

    cancelBtn?.addEventListener("click", () => modal.close());

    async function submit(): Promise<void> {
        const oobiUrl = queryInput(form, "#resolve-oobi-url")?.value.trim() || "";
        const alias = queryInput(form, "#resolve-alias")?.value.trim() || "";

        if (!oobiUrl) {
            statusLine.textContent = "OOBI URL is required.";
            return;
        }

        submitBtn.disabled = true;
        statusLine.textContent = "";

        try {
            await onResolveRemote(oobiUrl, alias);
            modal.close();
            showToast({ message: "Remote identifier resolved.", tone: "success" });
        } catch (error) {
            submitBtn.disabled = false;
            statusLine.textContent = errorMessage(error, "OOBI resolution failed.");
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

function createRemoteEditDialog(
    remote: RemoteRecord,
    onUpdateRemote: RemotesPageProps["onUpdateRemote"],
): void {
    const modal = createModal({
        title: `Edit ${remote.alias}`,
        body: `
            <form data-edit-remote-form>
                ${fieldTextHtml({ id: "edit-alias", label: "Alias", value: remote.alias ?? "" })}
                ${fieldTextHtml({ id: "edit-org", label: "Organization", value: remote.org ?? "" })}
                ${fieldTextHtml({ id: "edit-note", label: "Note", value: remote.note ?? "" })}
                <p class="status-line" data-edit-remote-status></p>
            </form>
        `,
        actions: [
            { label: "Cancel", tone: "ghost", dataAction: "cancel" },
            { label: "Save", tone: "primary", dataAction: "submit" },
        ],
    });

    modal.open();

    const root = Array.from(document.querySelectorAll("[role='dialog']")).find(
        (el) => el.getAttribute("aria-label") === `Edit ${remote.alias}`
    ) as HTMLElement | null;
    if (!(root instanceof HTMLElement)) {
        return;
    }

    const form = root.querySelector("[data-edit-remote-form]");
    const statusLine = queryElement(root, "[data-edit-remote-status]");
    const submitBtn = queryButton(root, "[data-action='submit']");
    const cancelBtn = queryButton(root, "[data-action='cancel']");

    if (!(form instanceof HTMLFormElement) || !statusLine || !submitBtn) {
        return;
    }

    cancelBtn?.addEventListener("click", () => modal.close());

    async function submit(): Promise<void> {
        submitBtn.disabled = true;
        statusLine.textContent = "";

        try {
            await onUpdateRemote(remote.aid, {
                alias: queryInput(form, "#edit-alias")?.value.trim() || "",
                org: queryInput(form, "#edit-org")?.value.trim() || "",
                note: queryInput(form, "#edit-note")?.value.trim() || "",
            });
            modal.close();
            showToast({ message: `Remote "${remote.alias}" updated.`, tone: "success" });
        } catch (error) {
            submitBtn.disabled = false;
            statusLine.textContent = errorMessage(error, "Remote update failed.");
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

function applyRemoteFilter(remotes: RemoteRecord[], filter: string): RemoteRecord[] {
    if (filter === "transferable") {
        return remotes.filter((remote) => remote.transferable);
    }
    if (filter === "non-transferable") {
        return remotes.filter((remote) => !remote.transferable);
    }
    return remotes;
}

export function renderRemotesPage({
    vault,
    remotes,
    filter,
    onResolveRemote,
    onUpdateRemote,
    onFilterChange,
}: RemotesPageProps) {
    const filteredRemotes = applyRemoteFilter(remotes, filter);

    const remoteRows: RemoteTableRow[] = filteredRemotes.map((remote) => ({
        alias: remote.alias,
        aliasLink: `<a href="${remoteDetailHref(vault.id, remote.aid)}">${escapeHtml(remote.alias)}</a>`,
        prefix: remote.prefix,
        sequenceNumber: remote.sequenceNumber == null ? "Not resolved" : String(remote.sequenceNumber),
        transferability: remote.transferability,
        rolesLabel: remote.rolesLabel,
        status: remote.status,
        _raw: remote,
    }));

    const filterChipsHtml = `
        <div class="lk-inline-filter" role="group" aria-label="Remote identifier filter">
            ${chipHtml({ label: "All", selected: filter === "all", dataValue: "all" })}
            ${chipHtml({ label: "Transferable", selected: filter === "transferable", dataValue: "transferable" })}
            ${chipHtml({ label: "Non-transferable", selected: filter === "non-transferable", dataValue: "non-transferable" })}
        </div>
        <p class="lk-table-header__note">Connect to a remote identifier using its OOBI URL.</p>
    `;

    const columns = [
        { key: "aliasLink", label: "Alias", width: "210px", searchKey: "alias", html: true },
        { key: "prefix", label: "Prefix", width: "320px" },
        { key: "sequenceNumber", label: "Seq No.", width: "110px" },
        { key: "transferability", label: "Type", width: "150px" },
        { key: "rolesLabel", label: "Roles", width: "180px" },
        { key: "status", label: "Status", width: "110px" },
    ] as unknown as Array<{ key: string; label: string; width?: string; searchKey?: string }>;

    const remotesTable = renderPaginatedTable({
        icon: "./assets/icons/remoteIds.png",
        title: "Remote Identifiers",
        collapseHeadingOnMobile: true,
        titleMetaHtml: filterChipsHtml,
        searchPlaceholder: "Search...",
        addButtonText: "Add Remote Identifier",
        columns,
        rows: remoteRows,
        rowActions: [
            { key: "view", label: "View", icon: "./assets/icons/browse.svg" },
            { key: "edit", label: "Edit", icon: "./assets/icons/edit.svg" },
        ],
        itemsPerPage: 10,
        emptyTitle: "No Remote Identifiers Yet",
        emptyText: "Add a remote identifier by resolving its OOBI URL.",
        onAdd() {
            createResolveRemoteDialog(onResolveRemote);
        },
        onAction(row: RemoteTableRow, actionKey: string) {
            if (actionKey === "view") {
                window.location.hash = remoteDetailHref(vault.id, row._raw.aid);
                return;
            }
            if (actionKey === "edit") {
                createRemoteEditDialog(row._raw, onUpdateRemote);
            }
        },
    });

    return {
        title: "Remote Identifiers",
        render(container: HTMLElement): void {
            container.replaceChildren();

            const page = document.createElement("section");
            page.className = "page-grid page-grid--table";

            const section = document.createElement("section");
            section.className = "section-card section-card--tight page-table-stage";

            const tableRoot = document.createElement("div");
            tableRoot.dataset.remotesTable = "true";
            tableRoot.innerHTML = remotesTable.html;

            section.append(tableRoot);
            page.append(section);
            container.append(page);
        },
        setup(root: HTMLElement): void {
            remotesTable.setup(queryElement(root, "[data-remotes-table]"));
            root.querySelectorAll("[data-value]").forEach((button) => {
                if (!(button instanceof HTMLElement)) {
                    return;
                }
                button.addEventListener("click", () => {
                    const nextFilter = button.dataset.value || "all";
                    if (nextFilter !== filter) {
                        onFilterChange(nextFilter);
                    }
                });
            });
        },
    };
}
