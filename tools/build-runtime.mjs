import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import {
    lstat,
    mkdir,
    open,
    readdir,
    rename,
    rm,
} from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_DIR = path.resolve(__dirname, '..');
const DIST_DIR = path.join(PROJECT_DIR, 'dist');
const CANONICAL_OUTPUT = path.join(DIST_DIR, 'runtime');
const SCRATCH_ROOT = path.join(DIST_DIR, '.runtime-builds');
const SCRATCH_MARKER = path.join(SCRATCH_ROOT, '.fortweb-runtime-owned');
const SCRATCH_MARKER_BYTES = 'fortweb.runtime-builds.v1\n';
const TYPESCRIPT_CLI = path.join(PROJECT_DIR, 'node_modules/typescript/bin/tsc');
const EXPECTED_RUNTIME = Object.freeze({
    pyodide: '314.0.5',
    python: '3.14.2',
    emscripten: '5.0.3',
    abi: 'pyemscripten_2026_0_wasm32',
});
const CORE_FILES = Object.freeze([
    'pyodide.mjs',
    'pyodide.asm.mjs',
    'pyodide.asm.wasm',
    'python_stdlib.zip',
    'pyodide-lock.json',
]);
const DEPENDENCY_EXCLUSIONS = Object.freeze([
    Object.freeze({ owner: 'hio', requirement: 'lmdb>=1.7.5' }),
    Object.freeze({ owner: 'keri', requirement: 'lmdb==2.1.1' }),
]);
const ALLOWED_FAULTS = new Set([
    '',
    'before-backup',
    'after-backup',
    'during-promotion',
    'after-promotion',
]);
const ALLOWED_OPERATION_FAULTS = new Set(['', 'backup-cleanup', 'backup-restore']);
const ALLOWED_APP_SUFFIXES = new Set([
    '.css', '.html', '.ico', '.jpeg', '.jpg', '.js', '.png', '.py', '.svg', '.ttf', '.webp', '.woff', '.woff2',
]);
const FORBIDDEN_APP_DIRECTORIES = new Set([
    '.git', '.tmp', 'artifacts', 'build', 'dist', 'logs', 'node_modules', 'tmp', 'vendor', 'wheels',
]);
const IGNORED_APP_DIRECTORIES = new Set(['__pycache__']);

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

function compareCodePoints(left, right) {
    const leftPoints = Array.from(left, (character) => character.codePointAt(0));
    const rightPoints = Array.from(right, (character) => character.codePointAt(0));
    const length = Math.min(leftPoints.length, rightPoints.length);
    for (let index = 0; index < length; index += 1) {
        if (leftPoints[index] !== rightPoints[index]) {
            return leftPoints[index] - rightPoints[index];
        }
    }
    return leftPoints.length - rightPoints.length;
}

function sortJson(value) {
    if (Array.isArray(value)) {
        return value.map(sortJson);
    }
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.keys(value)
                .sort(compareCodePoints)
                .map((key) => [key, sortJson(value[key])]),
        );
    }
    return value;
}

function canonicalJson(value) {
    return `${JSON.stringify(sortJson(value))}\n`;
}

function canonicalName(value) {
    return value.replace(/[-_.]+/g, '-').toLowerCase();
}

function isStrictDescendant(parent, candidate) {
    const relative = path.relative(path.resolve(parent), path.resolve(candidate));
    return relative !== ''
        && relative !== '..'
        && !relative.startsWith(`..${path.sep}`)
        && !path.isAbsolute(relative);
}

function safeRelativePosix(value, label) {
    if (typeof value !== 'string' || value.length === 0 || value.includes('\\') || value.includes('%')) {
        throw new Error(`${label} must be a non-empty relative POSIX path`);
    }
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || value.startsWith('/')) {
        throw new Error(`${label} must not be absolute or use a URL scheme: ${value}`);
    }
    const parts = value.split('/');
    if (parts.some((part) => part === '' || part === '.' || part === '..' || /[\u0000-\u001f\u007f]/.test(part))) {
        throw new Error(`${label} contains an unsafe path component: ${value}`);
    }
    return value;
}

async function exists(candidate) {
    try {
        await lstat(candidate);
        return true;
    } catch (error) {
        if (error?.code === 'ENOENT') {
            return false;
        }
        throw error;
    }
}

async function assertNoSymlinkComponents(root, candidate) {
    const resolvedRoot = path.resolve(root);
    const resolvedCandidate = path.resolve(candidate);
    if (resolvedCandidate !== resolvedRoot && !isStrictDescendant(resolvedRoot, resolvedCandidate)) {
        throw new Error(`path escapes its trusted root: ${candidate}`);
    }
    const relative = path.relative(resolvedRoot, resolvedCandidate);
    let current = resolvedRoot;
    const rootStat = await lstat(current);
    if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) {
        throw new Error(`trusted root is not a real directory: ${root}`);
    }
    if (!relative) {
        return;
    }
    for (const component of relative.split(path.sep)) {
        current = path.join(current, component);
        const currentStat = await lstat(current);
        if (currentStat.isSymbolicLink()) {
            throw new Error(`symlink path component is not allowed: ${current}`);
        }
    }
}

function sameFileState(left, right) {
    return left.dev === right.dev
        && left.ino === right.ino
        && left.mode === right.mode
        && left.size === right.size
        && left.mtimeNs === right.mtimeNs
        && left.ctimeNs === right.ctimeNs;
}

async function readStableRegularFile(source, trustedRoot = PROJECT_DIR) {
    await assertNoSymlinkComponents(trustedRoot, source);
    const handle = await open(source, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const before = await handle.stat({ bigint: true });
        if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n) {
            throw new Error(`input must be one unaliased regular file: ${source}`);
        }
        const bytes = await handle.readFile();
        const after = await handle.stat({ bigint: true });
        const pathAfter = await lstat(source, { bigint: true });
        if (
            !sameFileState(before, after)
            || !sameFileState(after, pathAfter)
            || pathAfter.isSymbolicLink()
            || BigInt(bytes.length) !== after.size
        ) {
            throw new Error(`input changed while it was read: ${source}`);
        }
        return bytes;
    } finally {
        await handle.close();
    }
}

async function writeNewFile(target, bytes) {
    await mkdir(path.dirname(target), { recursive: true });
    const handle = await open(target, 'wx', 0o644);
    try {
        await handle.writeFile(bytes);
    } finally {
        await handle.close();
    }
}

async function copyStableFile(source, target, expected = null) {
    const bytes = await readStableRegularFile(source);
    if (expected) {
        if (expected.bytes !== undefined && bytes.length !== expected.bytes) {
            throw new Error(`input byte count mismatch for ${source}`);
        }
        if (expected.sha256 !== undefined && sha256(bytes) !== expected.sha256) {
            throw new Error(`input SHA-256 mismatch for ${source}`);
        }
    }
    await writeNewFile(target, bytes);
    return { bytes: bytes.length, sha256: sha256(bytes) };
}

async function listRegularFiles(root) {
    const files = [];
    async function walk(directory, prefix = '') {
        const entries = await readdir(directory, { withFileTypes: true });
        entries.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)));
        for (const entry of entries) {
            const full = path.join(directory, entry.name);
            const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
            if (entry.isSymbolicLink()) {
                throw new Error(`symlink is not allowed: ${relative}`);
            }
            if (entry.isDirectory()) {
                await walk(full, relative);
                continue;
            }
            if (!entry.isFile()) {
                throw new Error(`non-regular output entry is not allowed: ${relative}`);
            }
            const fileStat = await lstat(full, { bigint: true });
            if (fileStat.nlink !== 1n) {
                throw new Error(`hard-link alias is not allowed: ${relative}`);
            }
            files.push(relative);
        }
    }
    await walk(root);
    files.sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right)));
    return files;
}

async function copyTree(sourceRoot, targetRoot, expectedPaths, relativePrefix) {
    const entries = await readdir(sourceRoot, { withFileTypes: true });
    entries.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)));
    for (const entry of entries) {
        const source = path.join(sourceRoot, entry.name);
        const target = path.join(targetRoot, entry.name);
        const relative = relativePrefix ? `${relativePrefix}/${entry.name}` : entry.name;
        if (entry.isSymbolicLink()) {
            throw new Error(`symlink input is not allowed: ${source}`);
        }
        if (entry.isDirectory()) {
            await copyTree(source, target, expectedPaths, relative);
            continue;
        }
        if (!entry.isFile()) {
            throw new Error(`non-regular input is not allowed: ${source}`);
        }
        await copyStableFile(source, target);
        expectedPaths.add(relative);
    }
}

function parseArgs(argv) {
    const values = {
        '--out-dir': '',
        '--source-manifest': process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST ?? '',
        '--source-manifest-sha256': process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256 ?? '',
    };
    const seen = new Set();
    for (let index = 2; index < argv.length; index += 2) {
        const argument = argv[index];
        const value = argv[index + 1];
        if (!(argument in values) || seen.has(argument) || !value || value.startsWith('-')) {
            throw new Error(`unknown, repeated, or missing argument: ${argument}`);
        }
        seen.add(argument);
        values[argument] = value;
    }
    return {
        outDir: values['--out-dir'],
        sourceManifest: values['--source-manifest'],
        sourceManifestSha256: values['--source-manifest-sha256'],
    };
}

async function ensureDistDirectory() {
    if (!(await exists(DIST_DIR))) {
        await assertNoSymlinkComponents(PROJECT_DIR, PROJECT_DIR);
        await mkdir(DIST_DIR, { recursive: false });
    }
    await assertNoSymlinkComponents(PROJECT_DIR, DIST_DIR);
    const metadata = await lstat(DIST_DIR);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
        throw new Error(`dist must be a real in-repository directory: ${DIST_DIR}`);
    }
}

async function requireExistingDirectory(candidate, trustedRoot) {
    await assertNoSymlinkComponents(trustedRoot, candidate);
    const metadata = await lstat(candidate);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
        throw new Error(`existing runtime output must be a real directory: ${candidate}`);
    }
}

async function ensureScratchRoot() {
    await ensureDistDirectory();
    if (!(await exists(SCRATCH_ROOT))) {
        await mkdir(SCRATCH_ROOT);
        await writeNewFile(SCRATCH_MARKER, Buffer.from(SCRATCH_MARKER_BYTES));
        return;
    }
    await assertNoSymlinkComponents(DIST_DIR, SCRATCH_ROOT);
    const scratchStat = await lstat(SCRATCH_ROOT);
    if (!scratchStat.isDirectory() || scratchStat.isSymbolicLink()) {
        throw new Error(`runtime scratch root must be a real directory: ${SCRATCH_ROOT}`);
    }
    const marker = await readStableRegularFile(SCRATCH_MARKER, SCRATCH_ROOT);
    if (marker.toString('utf8') !== SCRATCH_MARKER_BYTES) {
        throw new Error(`runtime scratch root is not builder-owned: ${SCRATCH_ROOT}`);
    }
}

async function resolveOutputDir(rawOutput) {
    const candidate = rawOutput
        ? path.resolve(PROJECT_DIR, rawOutput)
        : CANONICAL_OUTPUT;
    if (candidate === CANONICAL_OUTPUT) {
        await ensureDistDirectory();
        if (await exists(candidate)) {
            await requireExistingDirectory(candidate, DIST_DIR);
        }
        return candidate;
    }
    if (path.dirname(candidate) !== SCRATCH_ROOT || !/^[a-z0-9][a-z0-9_-]*$/.test(path.basename(candidate))) {
        throw new Error('--out-dir must be dist/runtime or one named direct child of dist/.runtime-builds');
    }
    await ensureScratchRoot();
    if (await exists(candidate)) {
        await requireExistingDirectory(candidate, SCRATCH_ROOT);
    }
    return candidate;
}

async function loadRuntimeInputs(sourceManifest, expectedSha256) {
    if (!/^[0-9a-f]{64}$/.test(expectedSha256)) {
        throw new Error("--source-manifest-sha256 or FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256 is required");
    }
    if (!sourceManifest) {
        throw new Error('--source-manifest or FORTWEB_RUNTIME_SOURCE_MANIFEST is required');
    }
    const manifestPath = path.resolve(PROJECT_DIR, sourceManifest);
    if (!isStrictDescendant(PROJECT_DIR, manifestPath)) {
        throw new Error('runtime source manifest must remain inside the repository');
    }
    const manifestBytes = await readStableRegularFile(manifestPath);
    if (sha256(manifestBytes) !== expectedSha256) {
        throw new Error('runtime input manifest SHA-256 mismatch');
    }
    const manifest = JSON.parse(manifestBytes.toString('utf8'));
    return { manifest, manifestPath, manifestSha256: expectedSha256 };
}

function sourceFileIndex(manifest) {
    if (!Array.isArray(manifest.files)) {
        throw new Error('source manifest files must be an array');
    }
    const index = new Map();
    for (const row of manifest.files) {
        const relative = safeRelativePosix(row?.path, 'source manifest file');
        if (index.has(relative)) {
            throw new Error(`duplicate source manifest file: ${relative}`);
        }
        if (!Number.isSafeInteger(row.bytes) || row.bytes < 0 || !/^[0-9a-f]{64}$/.test(row.sha256 ?? '')) {
            throw new Error(`invalid source manifest file identity: ${relative}`);
        }
        index.set(relative, row);
    }
    return index;
}

function projectRuntimeClosure(manifest, manifestSha256) {
    if (manifest?.schema !== 1 || !/^[0-9a-f]{64}$/.test(manifestSha256)) {
        throw new Error('unsupported source manifest identity');
    }
    const runtime = manifest.runtime;
    if (
        !runtime
        || Object.entries(EXPECTED_RUNTIME).some(([key, value]) => runtime[key] !== value)
        || JSON.stringify(runtime.core_files) !== JSON.stringify(CORE_FILES)
    ) {
        throw new Error('source manifest runtime identity mismatch');
    }
    if (!Array.isArray(manifest.wheels) || manifest.wheels.length !== 34) {
        throw new Error('source manifest must select exactly 34 wheels');
    }
    if (
        !Array.isArray(manifest.install_order)
        || manifest.install_order.length !== 34
        || new Set(manifest.install_order).size !== 34
    ) {
        throw new Error('source manifest install order must contain 34 unique filenames');
    }

    const sourceFiles = sourceFileIndex(manifest);
    const wheelByFilename = new Map();
    const normalizedNames = new Set();
    for (const sourceWheel of manifest.wheels) {
        const filename = sourceWheel?.filename;
        const name = sourceWheel?.name;
        const normalizedName = sourceWheel?.normalized_name;
        if (
            typeof filename !== 'string'
            || path.posix.basename(filename) !== filename
            || typeof name !== 'string'
            || canonicalName(name) !== normalizedName
            || normalizedNames.has(normalizedName)
            || typeof sourceWheel.version !== 'string'
            || !sourceWheel.version
            || !Number.isSafeInteger(sourceWheel.bytes)
            || sourceWheel.bytes < 0
            || !/^[0-9a-f]{64}$/.test(sourceWheel.sha256 ?? '')
        ) {
            throw new Error(`invalid source wheel row: ${filename ?? '<missing>'}`);
        }
        normalizedNames.add(normalizedName);
        wheelByFilename.set(filename, sourceWheel);
    }
    if (
        wheelByFilename.size !== 34
        || manifest.install_order.some((filename) => !wheelByFilename.has(filename))
    ) {
        throw new Error('source manifest install order is not an exact wheel permutation');
    }

    const files = [];
    const inputCopies = [];
    for (const filename of CORE_FILES) {
        const sourcePath = `runtime/${filename}`;
        const targetPath = `vendor/pyodide/314.0.5/${filename}`;
        const row = sourceFiles.get(sourcePath);
        if (!row) {
            throw new Error(`source manifest is missing exact core path ${sourcePath}`);
        }
        const projected = { path: targetPath, bytes: row.bytes, sha256: row.sha256 };
        files.push(projected);
        inputCopies.push({ sourcePath, targetPath, bytes: row.bytes, sha256: row.sha256 });
    }

    const wheels = [];
    for (const filename of manifest.install_order) {
        const sourceWheel = wheelByFilename.get(filename);
        const sourcePath = `wheelhouse/${filename}`;
        const targetPath = `wheels/${filename}`;
        const fileRow = sourceFiles.get(sourcePath);
        if (
            !fileRow
            || fileRow.bytes !== sourceWheel.bytes
            || fileRow.sha256 !== sourceWheel.sha256
        ) {
            throw new Error(`source manifest wheel path does not match its selected row: ${sourcePath}`);
        }
        files.push({ path: targetPath, bytes: fileRow.bytes, sha256: fileRow.sha256 });
        inputCopies.push({ sourcePath, targetPath, bytes: fileRow.bytes, sha256: fileRow.sha256 });
        wheels.push({
            filename,
            name: sourceWheel.name,
            normalized_name: sourceWheel.normalized_name,
            version: sourceWheel.version,
            sha256: sourceWheel.sha256,
            bytes: sourceWheel.bytes,
        });
    }

    const closure = {
        schema: 1,
        source_manifest_sha256: manifestSha256,
        runtime: {
            ...EXPECTED_RUNTIME,
            core_files: [...CORE_FILES],
        },
        files,
        wheels,
        install_order: [...manifest.install_order],
        dependency_exclusions: DEPENDENCY_EXCLUSIONS.map((row) => ({ ...row })),
    };
    const closureBytes = Buffer.from(canonicalJson(closure));
    return { closure, closureBytes, closureSha256: sha256(closureBytes), inputCopies };
}

function runCommand(command, args, cwd) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, { cwd, stdio: 'inherit' });
        child.on('error', reject);
        child.on('exit', (code) => {
            if (code === 0) {
                resolve();
                return;
            }
            reject(new Error(`${command} ${args.join(' ')} failed with exit code ${code ?? 'unknown'}`));
        });
    });
}

async function copyApplicationFiles(staging, expectedPaths) {
    async function walk(relativeDirectory) {
        const sourceDirectory = path.join(PROJECT_DIR, relativeDirectory);
        const entries = await readdir(sourceDirectory, { withFileTypes: true });
        entries.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)));
        const siblingNames = new Set(entries.map((entry) => entry.name));
        for (const entry of entries) {
            const relative = path.posix.join(relativeDirectory.split(path.sep).join('/'), entry.name);
            const source = path.join(PROJECT_DIR, ...relative.split('/'));
            if (entry.isSymbolicLink()) {
                throw new Error(`symlink application input is not allowed: ${relative}`);
            }
            if (entry.isDirectory()) {
                if (IGNORED_APP_DIRECTORIES.has(entry.name)) {
                    continue;
                }
                if (FORBIDDEN_APP_DIRECTORIES.has(entry.name)) {
                    throw new Error(`forbidden application input directory: ${relative}`);
                }
                await walk(relative);
                continue;
            }
            if (!entry.isFile()) {
                throw new Error(`non-regular application input is not allowed: ${relative}`);
            }
            if (entry.name.endsWith('.ts')) {
                continue;
            }
            if (entry.name.endsWith('.js') && siblingNames.has(`${entry.name.slice(0, -3)}.ts`)) {
                continue;
            }
            if (!ALLOWED_APP_SUFFIXES.has(path.extname(entry.name).toLowerCase())) {
                throw new Error(`forbidden application input file type: ${relative}`);
            }
            await copyStableFile(
                source,
                path.join(staging, ...relative.split('/')),
            );
            expectedPaths.add(relative);
        }
    }
    await walk('app');
}

async function collectCompiledPaths(staging, expectedPaths) {
    for (const relative of await listRegularFiles(path.join(staging, 'app'))) {
        expectedPaths.add(`app/${relative}`);
    }
}

async function verifyOutput(staging, expectedPaths, closureSha256) {
    const actualPaths = await listRegularFiles(staging);
    const expected = [...expectedPaths].sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right)));
    if (JSON.stringify(actualPaths) !== JSON.stringify(expected)) {
        const actualSet = new Set(actualPaths);
        const expectedSet = new Set(expected);
        throw new Error(`runtime output closure mismatch: missing=${expected.filter((item) => !actualSet.has(item))}, extra=${actualPaths.filter((item) => !expectedSet.has(item))}`);
    }
    const config = (await readStableRegularFile(path.join(staging, 'pyscript-ci.toml'), staging)).toString('utf8');
    if (!config.includes('interpreter = "./vendor/pyodide/314.0.5/pyodide.mjs"')) {
        throw new Error('runtime config interpreter is not package-root relative');
    }
    if (!config.includes('manifest = "./runtime-closure.json"')) {
        throw new Error('runtime config manifest is not package-root relative');
    }
    if (!config.includes(`sha256 = "${closureSha256}"`)) {
        throw new Error(`runtime config closure SHA-256 is not ${closureSha256}`);
    }
}

async function buildStagingTree(staging, sourceManifest, sourceManifestSha256) {
    const { manifest, manifestPath, manifestSha256 } = await loadRuntimeInputs(sourceManifest, sourceManifestSha256);
    const projected = projectRuntimeClosure(manifest, manifestSha256);

    await mkdir(staging, { recursive: false });
    await runCommand(
        process.execPath,
        [TYPESCRIPT_CLI, '--project', 'tsconfig.build.json', '--outDir', staging],
        PROJECT_DIR,
    );

    const expectedPaths = new Set();
    await collectCompiledPaths(staging, expectedPaths);
    await copyApplicationFiles(staging, expectedPaths);

    const config = (await readStableRegularFile(path.join(PROJECT_DIR, 'pyscript-ci.toml'))).toString('utf8');
    if (config.split('__RUNTIME_CLOSURE_SHA256__').length !== 2) {
        throw new Error('runtime config must contain exactly one closure digest placeholder');
    }
    await writeNewFile(path.join(staging, 'pyscript-ci.toml'),
        Buffer.from(config.replace('__RUNTIME_CLOSURE_SHA256__', projected.closureSha256)));
    expectedPaths.add('pyscript-ci.toml');

    await writeNewFile(path.join(staging, 'runtime-closure.json'), projected.closureBytes);
    expectedPaths.add('runtime-closure.json');

    await copyTree(
        path.join(PROJECT_DIR, 'vendor/pyscript/2025.11.2'),
        path.join(staging, 'vendor/pyscript/2025.11.2'),
        expectedPaths,
        'vendor/pyscript/2025.11.2',
    );

    const manifestRoot = path.dirname(manifestPath);
    for (const row of projected.inputCopies) {
        const source = path.join(manifestRoot, ...row.sourcePath.split('/'));
        const target = path.join(staging, ...row.targetPath.split('/'));
        await copyStableFile(source, target, { bytes: row.bytes, sha256: row.sha256 });
        expectedPaths.add(row.targetPath);
    }

    await verifyOutput(staging, expectedPaths, projected.closureSha256);
    return projected;
}

async function removeInternalPath(candidate, parent, prefix) {
    if (!isStrictDescendant(parent, candidate) || !path.basename(candidate).startsWith(prefix)) {
        throw new Error(`refusing to remove unowned internal path: ${candidate}`);
    }
    if (await exists(candidate)) {
        await assertNoSymlinkComponents(parent, candidate);
        await rm(candidate, { recursive: true, force: false });
    }
}

async function promote(staging, target, backup, fault, operationFault) {
    let backedUp = false;
    let committed = false;
    try {
        if (fault === 'before-backup') {
            throw new Error('injected failure before backup move');
        }
        if (await exists(target)) {
            await rename(target, backup);
            backedUp = true;
        }
        if (fault === 'after-backup') {
            throw new Error('injected failure after backup move');
        }
        if (fault === 'during-promotion') {
            throw new Error('injected failure during promotion');
        }
        await rename(staging, target);
        committed = true;
        if (fault === 'after-promotion') {
            throw new Error('injected failure after promotion');
        }
        if (backedUp) {
            if (operationFault === 'backup-cleanup') {
                await rm(path.join(backup, 'app/index.html'), { force: false });
                throw new Error('injected partial backup cleanup failure');
            }
            await rm(backup, { recursive: true, force: false });
            backedUp = false;
        }
    } catch (error) {
        if (!committed && backedUp && await exists(backup)) {
            if (operationFault === 'backup-restore') {
                throw new AggregateError([error], 'injected backup restore failure; complete backup retained');
            }
            await rename(backup, target);
            backedUp = false;
        }
        throw error;
    }
}

async function main() {
    const { outDir, sourceManifest, sourceManifestSha256 } = parseArgs(process.argv);
    const output = await resolveOutputDir(outDir);
    const fault = process.env.FORTWEB_RUNTIME_BUILD_FAULT ?? '';
    if (!ALLOWED_FAULTS.has(fault)) {
        throw new Error(`unsupported FORTWEB_RUNTIME_BUILD_FAULT value: ${fault}`);
    }
    const operationFault = process.env.FORTWEB_RUNTIME_OPERATION_FAULT ?? '';
    if (!ALLOWED_OPERATION_FAULTS.has(operationFault)) {
        throw new Error(`unsupported FORTWEB_RUNTIME_OPERATION_FAULT value: ${operationFault}`);
    }

    const token = `${process.pid}-${randomUUID().replaceAll('-', '')}`;
    const internalParent = path.dirname(output);
    const base = path.basename(output);
    const staging = path.join(internalParent, `.${base}.staging-${token}`);
    const backup = path.join(internalParent, `.${base}.backup-${token}`);
    const stagingPrefix = `.${base}.staging-`;
    if (await exists(staging) || await exists(backup)) {
        throw new Error('runtime internal path collision');
    }

    let projected;
    try {
        projected = await buildStagingTree(staging, sourceManifest, sourceManifestSha256);
        await promote(staging, output, backup, fault, operationFault);
    } finally {
        await removeInternalPath(staging, internalParent, stagingPrefix);
    }

    process.stdout.write(
        `[build-runtime] emitted ${path.relative(PROJECT_DIR, output)} with closure ${projected.closureSha256}.\n`,
    );
}

main().catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
});
