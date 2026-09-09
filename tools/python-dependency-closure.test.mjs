import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = path.join(PROJECT_DIR, '.venv-pyodide-314/bin/python');
const VERIFIER = path.join(PROJECT_DIR, 'scripts/verify_runtime_tree.py');
const RUNTIME = path.resolve(process.env.FORTWEB_RUNTIME_DIR ?? path.join(PROJECT_DIR, 'dist/runtime'));

test('independent artifact verifier proves the complete active dependency closure', () => {
    const result = spawnSync(PYTHON, [VERIFIER, '--runtime-dir', RUNTIME], {
        cwd: PROJECT_DIR,
        encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true);
    assert.equal(report.wheel_count, 34);
    assert.equal(report.dependency_edge_count, 43);
    assert.deepEqual(report.dependency_exclusions, [
        { owner: 'hio', requirement: 'lmdb>=1.7.5' },
        { owner: 'keri', requirement: 'lmdb==2.1.1' },
    ]);
});

test('packaged loader enforces exact installed distributions and typed exclusions', () => {
    const loader = readFileSync(path.join(RUNTIME, 'app/runtime/runtime_packages.py'), 'utf8');
    assert.match(loader, /installed distribution mismatch/);
    assert.match(loader, /dependency version mismatch/);
    assert.match(loader, /LMDB exclusion set mismatch/);
    assert.match(loader, /package manifest dependency exclusions mismatch/);
    assert.doesNotMatch(loader, /package-name resolution|micropip\.install/);
});
