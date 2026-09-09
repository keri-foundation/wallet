import { runtimePackageBaseUrl } from "./runtime-package-base.js";


export interface RuntimeConfigResponse {
    ok: boolean;
    status: number;
    text(): Promise<string>;
}

export type RuntimeConfigFetch = (url: string) => Promise<RuntimeConfigResponse>;

/** Validate the package origin before the first runtime-config request. */
export async function fetchRuntimeConfig(
    configUrl: URL | string,
    pageUrl: URL | string,
    fetcher: RuntimeConfigFetch = globalThis.fetch.bind(globalThis),
): Promise<{ packageBase: string; response: RuntimeConfigResponse }> {
    const configUrlString = configUrl.toString();
    const packageBase = runtimePackageBaseUrl(configUrlString, pageUrl);
    const response = await fetcher(configUrlString);
    return { packageBase, response };
}
