import assert from 'node:assert/strict';
import { test } from 'node:test';

import { runtimePackageBaseUrl } from '../dist/runtime/app/runtime/runtime-package-base.js';
import { fetchRuntimeConfig } from '../dist/runtime/app/runtime/runtime-config.js';


const CASES = [
    {
        name: 'browser source mount',
        config: '/fortweb/pyscript-ci.toml',
        page: 'http://127.0.0.1:4173/fortweb/app/',
        expected: 'http://127.0.0.1:4173/fortweb/',
    },
    {
        name: 'Android asset origin',
        config: 'https://appassets.androidplatform.net/pyscript-ci.toml',
        page: 'https://appassets.androidplatform.net/app/index.html',
        expected: 'https://appassets.androidplatform.net/',
    },
    {
        name: 'iOS app origin',
        config: 'app://local/pyscript-ci.toml',
        page: 'app://local/app/index.html',
        expected: 'app://local/',
    },
    {
        name: 'nonce loopback origin',
        config: 'http://127.0.0.1:43123/_fortios/aB_0123456789-CdE/pyscript-ci.toml',
        page: 'http://127.0.0.1:43123/_fortios/aB_0123456789-CdE/app/index.html',
        expected: 'http://127.0.0.1:43123/_fortios/aB_0123456789-CdE/',
    },
];

for (const row of CASES) {
    test(`production package base follows the config directory for ${row.name}`, () => {
        assert.equal(runtimePackageBaseUrl(row.config, row.page), row.expected);
    });
}

test('production package base rejects unsafe config URLs', () => {
    const page = 'https://appassets.androidplatform.net/app/index.html';
    for (const config of [
        'ftp://example.test/pyscript-ci.toml',
        'https://user:secret@appassets.androidplatform.net/pyscript-ci.toml',
        'https://appassets.androidplatform.net/pyscript-ci.toml?debug=1',
        'https://appassets.androidplatform.net/pyscript-ci.toml#debug',
        'https://appassets.androidplatform.net/runtime/',
        'https://appassets.androidplatform.net/runtime/%2e%2e/pyscript-ci.toml',
        'https://appassets.androidplatform.net/bad\npath/pyscript-ci.toml',
        'https://appassets.androidplatform.net/bad\tpath/pyscript-ci.toml',
        'https://example.test/pyscript-ci.toml',
        'https://appassets.androidplatform.net:444/pyscript-ci.toml',
        'app://other/pyscript-ci.toml',
    ]) {
        assert.throws(() => runtimePackageBaseUrl(config, page), /Invalid runtime config URL/);
    }
});

test('invalid config URLs fail before the production config fetch', async () => {
    let fetchCount = 0;
    const fetcher = async () => {
        fetchCount += 1;
        throw new Error('fetch must not run');
    };
    await assert.rejects(
        fetchRuntimeConfig(
            'https://example.test/pyscript-ci.toml',
            'https://appassets.androidplatform.net/app/index.html',
            fetcher,
        ),
        /Invalid runtime config URL/,
    );
    assert.equal(fetchCount, 0);
});
