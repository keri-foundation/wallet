import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import {
    copyFileSync,
    existsSync,
    lstatSync,
    mkdirSync,
    mkdtempSync,
    readFileSync,
    readdirSync,
    rmSync,
    rmdirSync,
    symlinkSync,
    unlinkSync,
    writeFileSync,
} from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BUILDER = path.join(PROJECT_DIR, 'tools/build-runtime.mjs');
const SCRATCH = path.join(PROJECT_DIR, 'dist/.runtime-builds');
const SCRATCH_MARKER = path.join(SCRATCH, '.fortweb-runtime-owned');
const SCRATCH_MARKER_BYTES = 'fortweb.runtime-builds.v1\n';
const SENTINEL_ROOT = path.join(PROJECT_DIR, 'dist/important-user-output');
const SENTINEL = path.join(SENTINEL_ROOT, 'sentinel.txt');
const VALID_TARGET = path.join(SCRATCH, `safe-output-${process.pid}`);
const SYMLINK_TARGET = path.join(SCRATCH, `unsafe-link-${process.pid}`);
const DANGLING_SYMLINK_TARGET = path.join(SCRATCH, `dangling-link-${process.pid}`);
const FILE_TARGET = path.join(SCRATCH, `unsafe-file-${process.pid}`);
const VERIFIER_LINK = path.join(SCRATCH, `verifier-link-${process.pid}`);
const FORBIDDEN_SOURCE = path.join(PROJECT_DIR, `app/.runtime-forbidden-${process.pid}.log`);
const FORBIDDEN_DIRECTORY = path.join(PROJECT_DIR, `app/.tmp`);
let ownsSentinel = false;
let ownsForbiddenSource = false;
let ownsForbiddenDirectory = false;
let ownsScratch = false;

function runBuilder(args, env = {}) {
    return spawnSync(process.execPath, [BUILDER, ...args], {
        cwd: PROJECT_DIR,
        encoding: 'utf8',
        env: { ...process.env, ...env },
    });
}

function removeExact(candidate) {
    const resolved = path.resolve(candidate);
    assert.ok(
        resolved === SENTINEL_ROOT
            || path.dirname(resolved) === SCRATCH,
        `test cleanup path is outside its exact boundary: ${candidate}`,
    );
    rmSync(resolved, { recursive: true, force: true });
}

assert.equal(existsSync(SENTINEL_ROOT), false, `refusing to reuse ${SENTINEL_ROOT}`);
assert.equal(existsSync(FORBIDDEN_SOURCE), false, `refusing to reuse ${FORBIDDEN_SOURCE}`);
assert.equal(existsSync(FORBIDDEN_DIRECTORY), false, `refusing to reuse ${FORBIDDEN_DIRECTORY}`);
if (!existsSync(SCRATCH)) {
    mkdirSync(SCRATCH);
    writeFileSync(SCRATCH_MARKER, SCRATCH_MARKER_BYTES, { flag: 'wx' });
    ownsScratch = true;
} else {
    assert.equal(readFileSync(SCRATCH_MARKER, 'utf8'), SCRATCH_MARKER_BYTES);
}
mkdirSync(SENTINEL_ROOT);
writeFileSync(SENTINEL, 'keep me\n', { flag: 'wx' });
ownsSentinel = true;

after(() => {
    removeExact(VALID_TARGET);
    removeExact(SYMLINK_TARGET);
    removeExact(DANGLING_SYMLINK_TARGET);
    removeExact(FILE_TARGET);
    removeExact(VERIFIER_LINK);
    if (ownsForbiddenSource) {
        assert.equal(readFileSync(FORBIDDEN_SOURCE, 'utf8'), 'not a runtime asset\n');
        unlinkSync(FORBIDDEN_SOURCE);
        ownsForbiddenSource = false;
    }
    if (ownsForbiddenDirectory) {
        assert.deepEqual(readdirSync(FORBIDDEN_DIRECTORY), ['payload.py']);
        unlinkSync(path.join(FORBIDDEN_DIRECTORY, 'payload.py'));
        rmdirSync(FORBIDDEN_DIRECTORY);
        ownsForbiddenDirectory = false;
    }
    if (ownsSentinel) {
        assert.deepEqual(readdirSync(SENTINEL_ROOT), ['sentinel.txt']);
        assert.equal(readFileSync(SENTINEL, 'utf8'), 'keep me\n');
        unlinkSync(SENTINEL);
        rmdirSync(SENTINEL_ROOT);
        ownsSentinel = false;
    }
    if (ownsScratch) {
        assert.deepEqual(readdirSync(SCRATCH), ['.fortweb-runtime-owned']);
        unlinkSync(SCRATCH_MARKER);
        rmdirSync(SCRATCH);
        ownsScratch = false;
    }
});

test('builder accepts only canonical output or one owned scratch child', () => {
    const invalid = [
        ['--out-dir', '.'],
        ['--out-dir', 'dist'],
        ['--out-dir', 'app'],
        ['--out-dir', 'dist/runtime/child'],
        ['--out-dir', 'dist/.runtime-builds'],
        ['--out-dir', 'dist/.runtime-builds/nested/child'],
        ['--out-dir', '../outside'],
        ['--out-dir', '/tmp/fortweb-unsafe-output'],
        ['--out-dir', ''],
        ['--out-dir', '', '--out-dir', 'dist/runtime'],
        ['--out-dir', 'dist/runtime', '--out-dir', 'dist/runtime'],
        ['--unknown'],
    ];
    for (const args of invalid) {
        const result = runBuilder(args);
        assert.notEqual(result.status, 0, `unsafe arguments unexpectedly succeeded: ${args.join(' ')}`);
        assert.equal(readFileSync(SENTINEL, 'utf8'), 'keep me\n');
    }
});

test('builder rejects a symlinked dist parent in an isolated repository', () => {
    const isolated = mkdtempSync(path.join(tmpdir(), 'fortweb-runtime-dist-link-'));
    const outside = path.join(isolated, 'outside');
    const tools = path.join(isolated, 'tools');
    mkdirSync(outside);
    mkdirSync(tools);
    copyFileSync(BUILDER, path.join(tools, 'build-runtime.mjs'));
    symlinkSync(outside, path.join(isolated, 'dist'), 'dir');
    try {
        const result = spawnSync(process.execPath, [path.join(tools, 'build-runtime.mjs')], {
            cwd: isolated,
            encoding: 'utf8',
        });
        assert.notEqual(result.status, 0);
        assert.deepEqual(readdirSync(outside), []);
    } finally {
        rmSync(isolated, { recursive: true, force: true });
    }
});

test('builder rejects a non-directory dist parent in an isolated repository', () => {
    const isolated = mkdtempSync(path.join(tmpdir(), 'fortweb-runtime-dist-file-'));
    const tools = path.join(isolated, 'tools');
    mkdirSync(tools);
    copyFileSync(BUILDER, path.join(tools, 'build-runtime.mjs'));
    writeFileSync(path.join(isolated, 'dist'), 'not a directory\n', { flag: 'wx' });
    try {
        const result = spawnSync(process.execPath, [path.join(tools, 'build-runtime.mjs')], {
            cwd: isolated,
            encoding: 'utf8',
        });
        assert.notEqual(result.status, 0);
        assert.equal(readFileSync(path.join(isolated, 'dist'), 'utf8'), 'not a directory\n');
    } finally {
        rmSync(isolated, { recursive: true, force: true });
    }
});

test('builder rejects a symlink output target without touching its destination', () => {
    removeExact(SYMLINK_TARGET);
    symlinkSync(SENTINEL_ROOT, SYMLINK_TARGET, 'dir');
    const result = runBuilder(['--out-dir', path.relative(PROJECT_DIR, SYMLINK_TARGET)]);
    assert.notEqual(result.status, 0);
    assert.ok(lstatSync(SYMLINK_TARGET).isSymbolicLink());
    assert.equal(readFileSync(SENTINEL, 'utf8'), 'keep me\n');
});

test('builder rejects a dangling symlink output target before compilation', () => {
    removeExact(DANGLING_SYMLINK_TARGET);
    symlinkSync(path.join(SCRATCH, `missing-target-${process.pid}`), DANGLING_SYMLINK_TARGET, 'dir');
    const result = runBuilder(['--out-dir', path.relative(PROJECT_DIR, DANGLING_SYMLINK_TARGET)]);
    assert.notEqual(result.status, 0);
    assert.ok(lstatSync(DANGLING_SYMLINK_TARGET).isSymbolicLink());
    assert.doesNotMatch(result.stdout, /emitted/);
});

test('builder accepts a valid owned scratch child', () => {
    removeExact(VALID_TARGET);
    const result = runBuilder(['--out-dir', path.relative(PROJECT_DIR, VALID_TARGET)]);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(existsSync(path.join(VALID_TARGET, 'runtime-closure.json')));
    assert.equal(readFileSync(SENTINEL, 'utf8'), 'keep me\n');
});

test('independent verifier rejects extra config keys and missing worker sources', () => {
    const configPath = path.join(VALID_TARGET, 'pyscript-ci.toml');
    const originalConfig = readFileSync(configPath);
    const python = path.join(PROJECT_DIR, '.venv-pyodide-314/bin/python');
    const verifier = path.join(PROJECT_DIR, 'scripts/verify_runtime_tree.py');
    try {
        writeFileSync(configPath, `version = "https://example.test/pyodide.mjs"\n${originalConfig.toString('utf8')}`);
        const result = spawnSync(python, [verifier, '--runtime-dir', VALID_TARGET], {
            cwd: PROJECT_DIR,
            encoding: 'utf8',
        });
        assert.notEqual(result.status, 0);
        assert.match(result.stderr, /copied runtime input changed|unexpected top-level keys/);
    } finally {
        writeFileSync(configPath, originalConfig);
    }

    const workerSource = path.join(VALID_TARGET, 'app/runtime/onboarding.py');
    const workerBytes = readFileSync(workerSource);
    try {
        unlinkSync(workerSource);
        const result = spawnSync(python, [verifier, '--runtime-dir', VALID_TARGET], {
            cwd: PROJECT_DIR,
            encoding: 'utf8',
        });
        assert.notEqual(result.status, 0);
    } finally {
        if (!existsSync(workerSource)) {
            writeFileSync(workerSource, workerBytes, { flag: 'wx' });
        }
    }
});

test('builder rejects an existing non-directory output target', () => {
    removeExact(FILE_TARGET);
    writeFileSync(FILE_TARGET, 'do not replace\n', { flag: 'wx' });
    const result = runBuilder(['--out-dir', path.relative(PROJECT_DIR, FILE_TARGET)]);
    assert.notEqual(result.status, 0);
    assert.equal(readFileSync(FILE_TARGET, 'utf8'), 'do not replace\n');
});

test('builder and verifier reject forbidden application payload classes', () => {
    assert.equal(existsSync(FORBIDDEN_SOURCE), false, `refusing to reuse ${FORBIDDEN_SOURCE}`);
    writeFileSync(FORBIDDEN_SOURCE, 'not a runtime asset\n', { flag: 'wx' });
    ownsForbiddenSource = true;
    try {
        const build = runBuilder(['--out-dir', path.relative(PROJECT_DIR, VALID_TARGET)]);
        assert.notEqual(build.status, 0);
        assert.match(build.stderr, /forbidden application input file type/);
        const python = path.join(PROJECT_DIR, '.venv-pyodide-314/bin/python');
        const verifier = path.join(PROJECT_DIR, 'scripts/verify_runtime_tree.py');
        const verify = spawnSync(python, [verifier, '--runtime-dir', VALID_TARGET], {
            cwd: PROJECT_DIR,
            encoding: 'utf8',
        });
        assert.notEqual(verify.status, 0);
        assert.match(verify.stderr, /forbidden application input file type/);
    } finally {
        assert.equal(readFileSync(FORBIDDEN_SOURCE, 'utf8'), 'not a runtime asset\n');
        unlinkSync(FORBIDDEN_SOURCE);
        ownsForbiddenSource = false;
    }
});

test('builder rejects forbidden application directories', () => {
    assert.equal(existsSync(FORBIDDEN_DIRECTORY), false, `refusing to reuse ${FORBIDDEN_DIRECTORY}`);
    mkdirSync(FORBIDDEN_DIRECTORY);
    ownsForbiddenDirectory = true;
    writeFileSync(path.join(FORBIDDEN_DIRECTORY, 'payload.py'), 'raise RuntimeError\n', { flag: 'wx' });
    try {
        const build = runBuilder(['--out-dir', path.relative(PROJECT_DIR, VALID_TARGET)]);
        assert.notEqual(build.status, 0);
        assert.match(build.stderr, /forbidden application input directory/);
    } finally {
        assert.deepEqual(readdirSync(FORBIDDEN_DIRECTORY), ['payload.py']);
        unlinkSync(path.join(FORBIDDEN_DIRECTORY, 'payload.py'));
        rmdirSync(FORBIDDEN_DIRECTORY);
        ownsForbiddenDirectory = false;
    }
});

test('independent verifier rejects a symlink runtime root', () => {
    removeExact(VERIFIER_LINK);
    symlinkSync(VALID_TARGET, VERIFIER_LINK, 'dir');
    const python = path.join(PROJECT_DIR, '.venv-pyodide-314/bin/python');
    const verifier = path.join(PROJECT_DIR, 'scripts/verify_runtime_tree.py');
    const result = spawnSync(python, [verifier, '--runtime-dir', VERIFIER_LINK], {
        cwd: PROJECT_DIR,
        encoding: 'utf8',
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /symlink/);
});

test('builder rejects unknown fault injection before changing a target', () => {
    const before = readFileSync(path.join(VALID_TARGET, 'runtime-closure.json'));
    const result = runBuilder(
        ['--out-dir', path.relative(PROJECT_DIR, VALID_TARGET)],
        { FORTWEB_RUNTIME_BUILD_FAULT: 'unknown' },
    );
    assert.notEqual(result.status, 0);
    assert.deepEqual(readFileSync(path.join(VALID_TARGET, 'runtime-closure.json')), before);
});
