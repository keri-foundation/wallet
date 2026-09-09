import assert from 'node:assert/strict';
import { mkdir, mkdtemp, open, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createDeterministicZip } from './deterministic-zip.mjs';
import { serializeReleaseMetadata } from './generate-release-metadata.mjs';
import { serializeRuntimeRequirements } from './generate-runtime-requirements.mjs';
import {
    canonicalJson,
    generateManifest,
    REQUIREMENTS_PATH,
    sha256,
    validateProvenance,
    ZIP_BASENAME,
} from './runtime-package-manifest.mjs';
import { verifyProduct } from './verify-runtime-package.mjs';
import { readRuntimePayloads } from './package-runtime.mjs';

const digest = '1'.repeat(64);

function provenance() {
    const packageKeys = {
        hio: ['commit', 'version', 'wheel_filename', 'wheel_sha256'],
        keripy: ['commit', 'metadata_patch_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
        msgpack: ['source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
        cbor2: ['cargo_acquisition_sha256', 'cargo_lock_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
        blake3: ['cargo_acquisition_sha256', 'final_cargo_lock_sha256', 'lock_patch_sha256', 'original_cargo_lock_sha256', 'source_patch_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
        cryptography: ['cargo_acquisition_sha256', 'cargo_lock_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
        openssl: ['source_sha256', 'version'],
        pysodium: ['conversion_patch_sha256', 'libsodium_source_sha256', 'libsodium_version', 'source_archive_sha256', 'source_commit', 'source_project', 'version', 'wheel_filename', 'wheel_sha256'],
    };
    const packages = Object.fromEntries(Object.entries(packageKeys).map(([name, keys]) => [
        name,
        Object.fromEntries(keys.map((key) => [
            key,
            key.includes('sha256') ? digest : (key === 'commit' || key === 'source_commit' ? '1'.repeat(40) : 'fixed'),
        ])),
    ]));
    const runtime = Object.fromEntries([
        'baseline_runtime_source_identity_sha256', 'baseline_runtime_final_verification_sha256',
        'baseline_runtime_canonical_inventory_sha256', 'baseline_runtime_tree_aggregate_sha256',
        'current_runtime_inventory_sha256', 'current_runtime_tree_aggregate_sha256',
        'runtime_closure_sha256', 'wheelhouse_manifest_sha256',
    ].map((key) => [key, digest]));
    const toolchainKeys = [
        'wheelhouse_toolchain_sha256', 'pyodide_version', 'python_version', 'emscripten_version',
        'abi', 'pyodide_release_commit', 'pyodide_lock_sha256', 'pyodide_core_sha256',
        'xbuildenv_sha256', 'emsdk_commit', 'rust_version', 'node_version',
        'node_sha256', 'node_bytes',
    ];
    const toolchain = Object.fromEntries(toolchainKeys.map((key) => [
        key,
        key === 'node_bytes'
            ? 1
            : (key.includes('sha256')
                ? digest
                : (key.endsWith('_commit') ? '1'.repeat(40) : 'fixed')),
    ]));
    const consumer = { commit: '1'.repeat(40), files: [{ path: 'x', sha256: digest }] };
    return {
        consumers: { fort_ios: consumer, fortoid: consumer },
        packages,
        packaging: { package_inputs_sha256: digest, profile: 'fortweb.deterministic-zip.v1', source_files: [] },
        runtime,
        schema: 'fortweb.runtime-package-provenance.v2',
        source: { fortweb_commit_sha: '1'.repeat(40), source_identity_sha256: digest },
        toolchain,
    };
}

test('provenance rejects malformed toolchain identities', () => {
    const cases = [
        ['node_sha256', 'bad', /node_sha256/],
        ['pyodide_release_commit', 'bad', /pyodide_release_commit/],
        ['emsdk_commit', 'bad', /emsdk_commit/],
        ['python_version', '', /python_version/],
        ['abi', '   ', /abi/],
        ['node_bytes', 0, /node_bytes/],
        ['node_bytes', Number.MAX_SAFE_INTEGER + 1, /node_bytes/],
    ];
    for (const [key, value, pattern] of cases) {
        const candidate = provenance();
        candidate.toolchain[key] = value;
        assert.throws(() => validateProvenance(candidate), pattern, key);
    }
});

async function writeNew(filename, data) {
    const handle = await open(filename, 'wx', 0o644);
    try { await handle.writeFile(data); } finally { await handle.close(); }
}

async function fixture(root) {
    const content = new Map([['app/index.html', Buffer.from('app')]]);
    for (let index = 0; index < 3; index += 1) {
        content.set(`payload/${String(index).padStart(3, '0')}.bin`, Buffer.from([index]));
    }
    const requirements = Buffer.from(serializeRuntimeRequirements());
    content.set(REQUIREMENTS_PATH, requirements);
    const rows = [...content].map(([memberPath, data]) => ({
        bytes: data.length, path: memberPath, sha256: sha256(data),
    })).sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
    const packageProvenance = provenance();
    const fortwebCommitSha = packageProvenance.source.fortweb_commit_sha;
    const manifest = Buffer.from(canonicalJson(generateManifest({
        files: rows,
        provenance: packageProvenance,
        fortwebCommitSha,
    })));
    const checksum = Buffer.from(`${sha256(manifest)}  manifest.json\n`);
    const zip = createDeterministicZip([
        ...[...content].map(([memberPath, data]) => ({ name: `fortweb-runtime/${memberPath}`, data })),
        { name: 'fortweb-runtime/manifest.json', data: manifest },
        { name: 'fortweb-runtime/checksums.sha256', data: checksum },
    ]);
    const zipDigest = sha256(zip);
    await writeNew(path.join(root, ZIP_BASENAME), zip);
    await writeNew(path.join(root, `${ZIP_BASENAME}.sha256`), Buffer.from(`${zipDigest}  ${ZIP_BASENAME}\n`));
    await writeNew(path.join(root, 'fortweb-release.json'), Buffer.from(serializeReleaseMetadata({
        artifactSha256: zipDigest,
        artifactBytes: zip.length,
        fortwebCommitSha,
        ref: 'refs/heads/pyodide-314-runtime',
    })));
}

test('portable verifier accepts the canonical generic product and rejects a bad sidecar', async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'fortweb-runtime-package-portable.'));
    try {
        await fixture(root);
        const report = await verifyProduct(root);
        assert.equal(report.zip_entries, 7);
        await rm(path.join(root, `${ZIP_BASENAME}.sha256`));
        await writeNew(path.join(root, `${ZIP_BASENAME}.sha256`), Buffer.from('bad\n'));
        await assert.rejects(verifyProduct(root), /sidecar/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('producer reads only the complete verified runtime and rejects unsafe paths', async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'fortweb-runtime-input.'));
    try {
        await mkdir(path.join(root, 'app'));
        await writeFile(path.join(root, 'app/index.html'), 'app');
        const rows = [{ path: 'app/index.html', bytes: 3, sha256: sha256('app') }];
        // macOS exposes its temporary directory through /var -> /private/var.
        const { realpath } = await import('node:fs/promises');
        const runtime = await realpath(root);
        assert.equal((await readRuntimePayloads(runtime, rows)).get('app/index.html').toString(), 'app');
        await assert.rejects(readRuntimePayloads(runtime, [{ ...rows[0], path: '../outside' }]), /path/i);
        await writeFile(path.join(root, 'extra'), 'extra');
        await assert.rejects(readRuntimePayloads(runtime, rows), /file set/);
        await rm(path.join(root, 'extra'));
        await writeFile(path.join(root, 'app/index.html'), 'changed');
        await assert.rejects(readRuntimePayloads(runtime, rows), /byte mismatch/);
        await rm(path.join(root, 'app/index.html'));
        await symlink('../outside', path.join(root, 'app/index.html'));
        await assert.rejects(readRuntimePayloads(runtime, rows), /symlink/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
