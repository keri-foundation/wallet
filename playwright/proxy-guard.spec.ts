import { expect, test } from '@playwright/test';
import { classifyRequestUrl, isAllowedLiveRemote } from './utils/proxy-guard';

/**
 * Focused unit coverage for the live-transport proxy invariant.
 *
 * Regression: the live route handlers used to allow localhost BEFORE checking
 * `/_fortweb_proxy/`, so a same-origin proxy URL could evade the public-proxy
 * assertion. These tests pin the classifier so the live test genuinely proves
 * public hosted traffic was direct.
 */
test('proxy classifier: same-origin proxy is caught before localhost allow', () => {
    // Same-origin /_fortweb_proxy/ must be classified as proxy, NOT allowed.
    expect(classifyRequestUrl('http://127.0.0.1:4183/_fortweb_proxy/https://kopn0.keri.foundation/')).toBe('proxy');
    expect(classifyRequestUrl('http://localhost:4183/_fortweb_proxy/138.68.53.132:5633')).toBe('proxy');
    expect(classifyRequestUrl('https://138.68.53.132:5633/_fortweb_proxy/anything')).toBe('proxy');
    // Legitimate local static/runtime assets stay allowed.
    expect(classifyRequestUrl('http://127.0.0.1:4183/fortweb/app/')).toBe('allowed_local');
    expect(classifyRequestUrl('http://localhost:4183/fortweb/runtime/wallet-worker.py')).toBe('allowed_local');
    expect(classifyRequestUrl('http://127.0.0.1:4183/vendor/pyodide/pyodide.js')).toBe('allowed_local');
    // Live remote HTTPS endpoints are allowed; plaintext ports are not.
    expect(classifyRequestUrl('https://kopn0.keri.foundation/')).toBe('allowed_local');
    expect(classifyRequestUrl('https://138.68.53.132:5633/')).toBe('allowed_local');
    expect(classifyRequestUrl('https://138.68.53.132:7633/')).toBe('allowed_local');
    expect(classifyRequestUrl('https://138.68.53.132:5632/')).toBe('forbidden_remote');
    expect(classifyRequestUrl('https://138.68.53.132:7632/')).toBe('forbidden_remote');
    expect(classifyRequestUrl('https://cdn.jsdelivr.net/npm/x')).toBe('forbidden_remote');
    expect(classifyRequestUrl('https://evil.example.com/x')).toBe('forbidden_remote');
});

test('isAllowedLiveRemote rejects proxy on any host with a reason', () => {
    expect(isAllowedLiveRemote('http://127.0.0.1:4183/_fortweb_proxy/x')).toEqual({
        allowed: false,
        reason: 'public Fort Web proxy',
    });
    expect(isAllowedLiveRemote('https://138.68.53.132:5633/')).toEqual({ allowed: true });
    expect(isAllowedLiveRemote('https://138.68.53.132:5632/')).toEqual({
        allowed: false,
        reason: 'plaintext native port 5632',
    });
});
