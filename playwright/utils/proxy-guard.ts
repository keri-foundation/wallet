/**
 * Shared live-transport invariant guard for the Fort Web DigitalOcean tests.
 *
 * Classifies an outgoing URL as a public-Fort-Web proxy use, a legitimate local
 * static/runtime asset, or a forbidden remote. The proxy-path check MUST run
 * before any localhost/allowlist shortcut: a same-origin `/_fortweb_proxy/`
 * request is still a proxy use and must fail the live test.
 */

export type UrlClass = 'proxy' | 'allowed_local' | 'forbidden_remote';

function allowedHost(hostname: string): boolean {
    return hostname === '127.0.0.1' || hostname === 'localhost';
}

/** Live-allowed remote hosts (public HTTPS Kf Boot / witness / watcher). */
export function isAllowedLiveRemote(url: string): { allowed: boolean; reason?: string } {
    const hostname = new URL(url).hostname;
    // Public Fort Web proxy path is forbidden for ANY host — check first so a
    // same-origin/localhost proxy URL cannot bypass the remote-host allowlist.
    if (url.includes('/_fortweb_proxy/')) {
        return { allowed: false, reason: 'public Fort Web proxy' };
    }
    if (hostname === 'kopn0.keri.foundation') {
        return { allowed: true };
    }
    if (hostname === '138.68.53.132') {
        const port = new URL(url).port;
        if (port === '5633' || port === '7633') {
            return { allowed: true };
        }
        return { allowed: false, reason: `plaintext native port ${port}` };
    }
    if (hostname === 'cdn.jsdelivr.net' || url.includes('cdn.jsdelivr.net')) {
        return { allowed: false, reason: 'CDN' };
    }
    if (!allowedHost(hostname)) {
        return { allowed: false, reason: `unexpected remote ${hostname}` };
    }
    return { allowed: true };
}

/** Classify any URL as proxy vs legitimate local static/runtime asset. */
export function classifyRequestUrl(url: string): UrlClass {
    if (url.includes('/_fortweb_proxy/')) {
        return 'proxy';
    }
    const hostname = new URL(url).hostname;
    if (hostname === '127.0.0.1' || hostname === 'localhost') {
        return 'allowed_local';
    }
    return isAllowedLiveRemote(url).allowed ? 'allowed_local' : 'forbidden_remote';
}
