import assert from 'node:assert/strict';
import test from 'node:test';

import {
    canonicalJson,
    generateManifest,
    validateFileRows,
    validatePackagePath,
} from './runtime-package-manifest.mjs';
import { serializeRuntimeRequirements } from './generate-runtime-requirements.mjs';

test('canonical JSON sorts object keys and retains array order', () => {
    assert.equal(canonicalJson({ z: 1, a: [{ y: 2, x: 1 }] }), '{"a":[{"x":1,"y":2}],"z":1}\n');
});

test('runtime requirements match the frozen consumer bytes', () => {
    const value = serializeRuntimeRequirements();
    assert.equal(Buffer.byteLength(value), 1581);
});

test('file rows require strict byte order and case-fold uniqueness', () => {
    const digest = '0'.repeat(64);
    assert.deepEqual([...validateFileRows([
        { path: 'a', sha256: digest, bytes: 0 },
        { path: 'b', sha256: digest, bytes: 1 },
    ], 2)], ['a', 'b']);
    assert.throws(() => validateFileRows([
        { path: 'A', sha256: digest, bytes: 0 },
        { path: 'a', sha256: digest, bytes: 0 },
    ], 2), /Duplicate or case-fold/);
});

test('manifest rows reserve metadata names case-insensitively', () => {
    const rows = Array.from({ length: 160 }, (_, index) => ({
        bytes: 0,
        path: `payload/${String(index).padStart(3, '0')}`,
        sha256: '0'.repeat(64),
    }));
    rows[0].path = 'Manifest.json';
    rows.sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path)));
    assert.throws(() => validateFileRows(rows, 160), /Reserved package metadata/);
});

test('package paths reject traversal, percent escapes, and empty components', () => {
    for (const value of ['../x', '/x', 'a\\b', 'a/%2e%2e/b', 'a//b', 'a/./b', 'a/../b']) {
        assert.throws(() => validatePackagePath(value), /path/i, value);
    }
});

test('manifest source commit must match provenance', () => {
    const files = Array.from({ length: 160 }, (_, index) => ({
        bytes: 0,
        path: `payload/${String(index).padStart(3, '0')}`,
        sha256: '0'.repeat(64),
    }));
    files[0].path = 'app/index.html';
    files[1].path = 'contracts/runtime-requirements.json';
    files.sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path)));
    const provenance = { source: { fortweb_commit_sha: '1'.repeat(40) } };
    assert.throws(
        () => generateManifest({ files, provenance, fortwebCommitSha: '2'.repeat(40) }),
        /does not match provenance/,
    );
});
