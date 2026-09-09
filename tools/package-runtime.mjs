import { constants } from 'node:fs';
import { execFile } from 'node:child_process';
import { lstat, mkdir, mkdtemp, open, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { createDeterministicZip } from './deterministic-zip.mjs';
import { serializeReleaseMetadata } from './generate-release-metadata.mjs';
import { serializeRuntimeRequirements } from './generate-runtime-requirements.mjs';
import {
    canonicalJson, comparePathBytes, generateManifest, REQUIREMENTS_PATH, sha256,
    validateFileRows, validatePackagePath, ZIP_BASENAME,
} from './runtime-package-manifest.mjs';
import { verifyProduct } from './verify-runtime-package.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const execute = promisify(execFile);
const SOURCE_PATHS = ['app', 'vendor', 'scripts', 'tools', 'ci', 'contracts', '.github',
    'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.build.json', 'pyscript-ci.toml'];

async function stableRead(root, relative) {
    validatePackagePath(relative, { allowMetadata: true });
    let current = path.parse(path.resolve(root)).root;
    for (const part of path.relative(current, path.resolve(root, relative)).split(path.sep)) {
        current = path.join(current, part);
        if ((await lstat(current)).isSymbolicLink()) throw new Error(`symlink input: ${current}`);
    }
    const handle = await open(current, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const before = await handle.stat({ bigint: true });
        if (!before.isFile() || before.nlink !== 1n) throw new Error(`input must be one regular file: ${current}`);
        const data = await handle.readFile();
        const after = await handle.stat({ bigint: true });
        const final = await lstat(current, { bigint: true });
        for (const key of ['dev', 'ino', 'mode', 'nlink', 'size', 'mtimeNs', 'ctimeNs']) {
            if (before[key] !== after[key] || after[key] !== final[key]) throw new Error(`input changed: ${current}`);
        }
        if (BigInt(data.length) !== after.size) throw new Error(`input size changed: ${current}`);
        return data;
    } finally {
        await handle.close();
    }
}

async function inventoryPaths(root, prefix = '') {
    const entries = await readdir(path.join(root, prefix), { withFileTypes: true });
    const paths = [];
    for (const entry of entries) {
        const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isSymbolicLink()) throw new Error(`symlink runtime entry: ${relative}`);
        if (entry.isDirectory()) paths.push(...await inventoryPaths(root, relative));
        else if (entry.isFile()) paths.push(relative);
        else throw new Error(`non-regular runtime entry: ${relative}`);
    }
    return paths.sort(comparePathBytes);
}

export async function readRuntimePayloads(root, rows) {
    validateFileRows(rows);
    const paths = await inventoryPaths(root);
    if (JSON.stringify(paths) !== JSON.stringify(rows.map((row) => row.path))) {
        throw new Error('runtime file set differs from the verified inventory');
    }
    const payloads = new Map();
    for (const row of rows) {
        const data = await stableRead(root, row.path);
        if (data.length !== row.bytes || sha256(data) !== row.sha256) throw new Error(`runtime byte mismatch: ${row.path}`);
        payloads.set(row.path, data);
    }
    return payloads;
}

async function git(...args) {
    return (await execute('git', ['-C', REPO, ...args], { maxBuffer: 8 * 1024 * 1024 })).stdout.trim();
}

async function captureSource() {
    const head = await git('rev-parse', 'HEAD');
    const names = (await git('ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', ...SOURCE_PATHS))
        .split('\0').filter(Boolean);
    const files = [];
    const deleted = [];
    for (const relative of [...new Set(names)].sort(comparePathBytes)) {
        let data;
        try { data = await stableRead(REPO, relative); } catch (error) {
            if (error.code === 'ENOENT') { deleted.push(relative); continue; }
            throw error;
        }
        files.push({ path: relative, bytes: data.length, sha256: sha256(data) });
    }
    return { schema: 'fortweb.source-identity.v1', head, files, deleted };
}

function parseArgs(argv) {
    const values = {
        '--runtime-dir': path.join(REPO, 'dist/runtime'),
        '--source-manifest': process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST ?? '',
        '--source-manifest-sha256': process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256 ?? '',
        '--python': process.env.FORTWEB_PYTHON ?? 'python3',
        '--output-dir': '', '--ref': '',
    };
    const seen = new Set();
    for (let index = 0; index < argv.length; index += 2) {
        const key = argv[index];
        const value = argv[index + 1];
        if (!(key in values) || seen.has(key) || !value || value.startsWith('--')) throw new Error(`invalid package argument: ${key}`);
        values[key] = value;
        seen.add(key);
    }
    if (!values['--source-manifest'] || !values['--output-dir'] || !/^[0-9a-f]{64}$/.test(values['--source-manifest-sha256'])) {
        throw new Error('package requires --source-manifest, --source-manifest-sha256, and --output-dir');
    }
    return values;
}

async function main() {
    const args = parseArgs(process.argv.slice(2));
    const sourceManifestPath = path.resolve(args['--source-manifest']);
    const sourceBytes = await stableRead(path.dirname(sourceManifestPath), path.basename(sourceManifestPath));
    if (sha256(sourceBytes) !== args['--source-manifest-sha256']) throw new Error('source manifest SHA-256 mismatch');
    const sourceManifest = JSON.parse(sourceBytes);
    const declared = sourceManifest.package_provenance;
    if (!declared?.baseline || !declared?.packages || !declared?.toolchain || !declared?.consumers) {
        throw new Error('source manifest must contain the declared package provenance');
    }
    for (const [name, item] of Object.entries(declared.packages)) {
        if (!item.wheel_filename) continue;
        const wheel = sourceManifest.wheels.find((row) => row.filename === item.wheel_filename);
        if (!wheel || wheel.sha256 !== item.wheel_sha256 || wheel.version !== item.version) {
            throw new Error(`package provenance does not match the source wheel: ${name}`);
        }
    }
    const source = await captureSource();
    const sourceIdentity = Buffer.from(canonicalJson(source));
    const ref = args['--ref'] || await git('symbolic-ref', 'HEAD');
    const runtimeDir = path.resolve(args['--runtime-dir']);
    const scratch = await mkdtemp(path.join(await realpath(os.tmpdir()), 'fortweb-package-'));
    try {
        const inventoryPath = path.join(scratch, 'inventory.json');
        await execute(args['--python'], [path.join(REPO, 'scripts/verify_runtime_tree.py'),
            '--runtime-dir', runtimeDir, '--source-manifest', sourceManifestPath,
            '--source-manifest-sha256', args['--source-manifest-sha256'], '--inventory-output', inventoryPath],
        { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, maxBuffer: 8 * 1024 * 1024 });
        const inventoryBytes = await stableRead(scratch, 'inventory.json');
        const inventory = JSON.parse(inventoryBytes);
        const payloads = await readRuntimePayloads(runtimeDir, inventory.files);
        const requirements = Buffer.from(serializeRuntimeRequirements());
        const rows = [...inventory.files, { path: REQUIREMENTS_PATH, bytes: requirements.length, sha256: sha256(requirements) }]
            .sort((left, right) => comparePathBytes(left.path, right.path));
        const node = await stableRead(path.dirname(await realpath(process.execPath)), path.basename(await realpath(process.execPath)));
        const toolchain = { ...declared.toolchain, node_version: process.versions.node, node_sha256: sha256(node), node_bytes: node.length };
        const inputs = { source_manifest_sha256: sha256(sourceBytes), source_identity_sha256: sha256(sourceIdentity),
            runtime_inventory_sha256: sha256(inventoryBytes), toolchain };
        const provenance = {
            schema: 'fortweb.runtime-package-provenance.v2',
            source: { fortweb_commit_sha: source.head, source_identity_sha256: sha256(sourceIdentity) },
            consumers: declared.consumers, packages: declared.packages, toolchain,
            packaging: { profile: 'fortweb.deterministic-zip.v1', package_inputs_sha256: sha256(canonicalJson(inputs)),
                source_files: source.files.filter((row) => row.path.startsWith('scripts/') || row.path.startsWith('tools/')) },
            runtime: {
                baseline_runtime_source_identity_sha256: declared.baseline.runtime_source_identity_sha256,
                baseline_runtime_final_verification_sha256: declared.baseline.runtime_final_verification_sha256,
                baseline_runtime_canonical_inventory_sha256: declared.baseline.runtime_canonical_inventory_sha256,
                baseline_runtime_tree_aggregate_sha256: declared.baseline.runtime_tree_aggregate_sha256,
                current_runtime_inventory_sha256: sha256(inventoryBytes),
                current_runtime_tree_aggregate_sha256: inventory.aggregate_sha256,
                runtime_closure_sha256: sha256(payloads.get('runtime-closure.json')),
                wheelhouse_manifest_sha256: sha256(sourceBytes),
            },
        };
        const manifest = Buffer.from(canonicalJson(generateManifest({ files: rows, provenance, fortwebCommitSha: source.head })));
        const zip = createDeterministicZip([
            ...[...payloads].map(([name, data]) => ({ name: `fortweb-runtime/${name}`, data })),
            { name: `fortweb-runtime/${REQUIREMENTS_PATH}`, data: requirements },
            { name: 'fortweb-runtime/manifest.json', data: manifest },
            { name: 'fortweb-runtime/checksums.sha256', data: Buffer.from(`${sha256(manifest)}  manifest.json\n`) },
        ]);
        if (canonicalJson(await captureSource()) !== sourceIdentity.toString('utf8')) throw new Error('source changed while packaging');
        if (!sourceBytes.equals(await stableRead(path.dirname(sourceManifestPath), path.basename(sourceManifestPath)))) {
            throw new Error('source manifest changed while packaging');
        }
        const output = path.resolve(args['--output-dir']);
        await mkdir(output, { recursive: false });
        const zipDigest = sha256(zip);
        await writeFile(path.join(output, ZIP_BASENAME), zip, { flag: 'wx' });
        await writeFile(path.join(output, `${ZIP_BASENAME}.sha256`), `${zipDigest}  ${ZIP_BASENAME}\n`, { flag: 'wx' });
        await writeFile(path.join(output, 'fortweb-release.json'), serializeReleaseMetadata({
            artifactSha256: zipDigest, artifactBytes: zip.length, fortwebCommitSha: source.head, ref,
        }), { flag: 'wx' });
        process.stdout.write(`${JSON.stringify(await verifyProduct(output))}\n`);
    } finally {
        await rm(scratch, { recursive: true, force: true });
    }
}

if (path.resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) {
    main().catch((error) => {
        process.stderr.write(`package-runtime: ${error.message}\n`);
        process.exitCode = 1;
    });
}
