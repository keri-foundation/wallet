/** Resolve runtime artifacts from the directory that owns the PyScript config. */
export function runtimePackageBaseUrl(configUrl: URL | string, pageUrl: URL | string): string {
    const rawConfigUrl = configUrl.toString();
    const page = new URL(pageUrl.toString());
    const resolved = new URL(rawConfigUrl, page);
    if (
        /[\u0000-\u001f\u007f]/.test(rawConfigUrl)
        || rawConfigUrl.includes("%")
        || !["app:", "http:", "https:"].includes(resolved.protocol)
        || resolved.protocol !== page.protocol
        || resolved.host !== page.host
        || resolved.username
        || resolved.password
        || resolved.search
        || resolved.hash
        || decodeURIComponent(resolved.pathname) !== resolved.pathname
        || resolved.pathname.endsWith("/")
    ) {
        throw new Error(`Invalid runtime config URL: ${resolved.href}`);
    }
    resolved.pathname = resolved.pathname.slice(0, resolved.pathname.lastIndexOf("/") + 1);
    return resolved.href;
}
