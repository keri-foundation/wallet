import { createHash } from 'node:crypto';

export const PACKAGE_NAME = 'fortweb-runtime';
export const PACKAGE_VERSION = '0.0.0';
export const ZIP_BASENAME = 'fortweb-runtime-0.0.0.zip';
export const ENTRYPOINT = 'app/index.html';
export const REQUIREMENTS_PATH = 'contracts/runtime-requirements.json';
export const REQUIREMENTS_SCHEMA = 'fort.runtime-requirements.v1';

function compareCodePoints(left, right) {
    const a = Array.from(left, (value) => value.codePointAt(0));
    const b = Array.from(right, (value) => value.codePointAt(0));
    const length = Math.min(a.length, b.length);
    for (let index = 0; index < length; index += 1) {
        if (a[index] !== b[index]) {
            return a[index] - b[index];
        }
    }
    return a.length - b.length;
}

export function canonicalValue(value) {
    if (Array.isArray(value)) {
        return value.map(canonicalValue);
    }
    if (value && typeof value === 'object') {
        const output = {};
        for (const key of Object.keys(value).sort(compareCodePoints)) {
            output[key] = canonicalValue(value[key]);
        }
        return output;
    }
    return value;
}

export function canonicalJson(value) {
    return `${JSON.stringify(canonicalValue(value))}\n`;
}

export function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

export function comparePathBytes(left, right) {
    return Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'));
}

export function validatePackagePath(value, { allowMetadata = false } = {}) {
    if (typeof value !== 'string' || value.length === 0) {
        throw new Error('Package paths must be non-empty strings.');
    }
    if (!/^[\x20-\x7e]+$/.test(value)) {
        throw new Error(`Package path must use printable ASCII: ${value}`);
    }
    if (value.startsWith('/') || value.includes('\\') || value.endsWith('/')) {
        throw new Error(`Unsafe package path: ${value}`);
    }
    if (value.includes('%')) {
        throw new Error(`Package path must not contain percent escapes: ${value}`);
    }
    const parts = value.split('/');
    if (parts.some((part) => part === '' || part === '.' || part === '..')) {
        throw new Error(`Package path contains an empty, dot, or traversal component: ${value}`);
    }
    if (!allowMetadata && ['manifest.json', 'checksums.sha256'].includes(value)) {
        throw new Error(`Reserved package metadata path: ${value}`);
    }
    return value;
}

function requireExactKeys(value, keys, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`${label} must be an object.`);
    }
    const actual = Object.keys(value).sort(compareCodePoints);
    const expected = [...keys].sort(compareCodePoints);
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(`${label} has an unexpected key set.`);
    }
}

function requireDigest(value, label) {
    if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) {
        throw new Error(`${label} must be a lowercase SHA-256 digest.`);
    }
}

function requireCommit(value, label) {
    if (typeof value !== 'string' || !/^[0-9a-f]{40}$/.test(value)) {
        throw new Error(`${label} must be a lowercase 40-hex commit.`);
    }
}

function requireNonEmptyString(value, label) {
    if (typeof value !== 'string' || value.trim().length === 0) {
        throw new Error(`${label} must be a nonempty string.`);
    }
}

function requireSourceRows(rows, label) {
    if (!Array.isArray(rows)) throw new Error(`${label} must be an array.`);
    let previous;
    const paths = new Set();
    for (const [index, row] of rows.entries()) {
        requireExactKeys(row, ['path', 'sha256', 'bytes'], `${label} row ${index}`);
        validatePackagePath(row.path, { allowMetadata: true });
        requireDigest(row.sha256, `${label} row ${index} sha256`);
        if (!Number.isSafeInteger(row.bytes) || row.bytes < 0) {
            throw new Error(`${label} row ${index} bytes must be nonnegative.`);
        }
        if (paths.has(row.path) || (previous !== undefined && comparePathBytes(previous, row.path) >= 0)) {
            throw new Error(`${label} must have unique path-byte-sorted rows.`);
        }
        paths.add(row.path);
        previous = row.path;
    }
}

export function validateFileRows(rows, expectedCount) {
    if (!Array.isArray(rows) || rows.length === 0 || (expectedCount !== undefined && rows.length !== expectedCount)) {
        throw new Error('Manifest files must contain a nonempty, complete inventory.');
    }
    const paths = new Set();
    const folds = new Set();
    let previous;
    for (const [index, row] of rows.entries()) {
        requireExactKeys(row, ['path', 'sha256', 'bytes'], `Manifest file row ${index}`);
        validatePackagePath(row.path);
        requireDigest(row.sha256, `Manifest file row ${index} sha256`);
        if (!Number.isSafeInteger(row.bytes) || row.bytes < 0) {
            throw new Error(`Manifest file row ${index} bytes must be a nonnegative integer.`);
        }
        const folded = row.path.toLowerCase();
        if (['manifest.json', 'checksums.sha256'].includes(folded)) {
            throw new Error(`Reserved package metadata path: ${row.path}`);
        }
        if (paths.has(row.path) || folds.has(folded)) {
            throw new Error(`Duplicate or case-fold-colliding package path: ${row.path}`);
        }
        if (previous !== undefined && comparePathBytes(previous, row.path) >= 0) {
            throw new Error('Manifest file rows are not strictly path-byte sorted.');
        }
        paths.add(row.path);
        folds.add(folded);
        previous = row.path;
    }
    return paths;
}

export function generateManifest({ files, provenance, fortwebCommitSha }) {
    validateFileRows(files);
    requireCommit(fortwebCommitSha, 'Manifest FortWeb commit');
    const manifest = {
        contracts: { runtime_requirements: { path: REQUIREMENTS_PATH } },
        entrypoint: ENTRYPOINT,
        files,
        fortweb_commit_sha: fortwebCommitSha,
        package_name: PACKAGE_NAME,
        package_version: PACKAGE_VERSION,
        payload_profile: 'offline-runtime',
        producer: 'fortweb',
        provenance,
        runtime_origin: 'https://appassets.androidplatform.net',
        schema_version: '1.0.0',
    };
    validateManifest(manifest);
    return manifest;
}

export function validateManifest(manifest) {
    requireExactKeys(manifest, [
        'contracts', 'entrypoint', 'files', 'fortweb_commit_sha', 'package_name',
        'package_version', 'payload_profile', 'producer', 'provenance',
        'runtime_origin', 'schema_version',
    ], 'Manifest');
    const expected = {
        entrypoint: ENTRYPOINT,
        package_name: PACKAGE_NAME,
        package_version: PACKAGE_VERSION,
        payload_profile: 'offline-runtime',
        producer: 'fortweb',
        runtime_origin: 'https://appassets.androidplatform.net',
        schema_version: '1.0.0',
    };
    for (const [key, value] of Object.entries(expected)) {
        if (manifest[key] !== value) {
            throw new Error(`Manifest ${key} is not the fixed value.`);
        }
    }
    requireCommit(manifest.fortweb_commit_sha, 'Manifest FortWeb commit');
    requireExactKeys(manifest.contracts, ['runtime_requirements'], 'Manifest contracts');
    requireExactKeys(manifest.contracts.runtime_requirements, ['path'], 'Runtime requirements contract');
    if (manifest.contracts.runtime_requirements.path !== REQUIREMENTS_PATH) {
        throw new Error('Manifest runtime requirements path is not canonical.');
    }
    const paths = validateFileRows(manifest.files);
    if (!paths.has(ENTRYPOINT) || !paths.has(REQUIREMENTS_PATH)) {
        throw new Error('Manifest does not inventory its entrypoint and runtime requirements.');
    }
    if (manifest.fortweb_commit_sha !== manifest.provenance.source.fortweb_commit_sha) {
        throw new Error('Manifest FortWeb commit does not match provenance source.');
    }
    validateProvenance(manifest.provenance);
    return manifest;
}

const PACKAGE_KEYS = {
    hio: ['commit', 'version', 'wheel_filename', 'wheel_sha256'],
    keripy: ['commit', 'metadata_patch_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
    msgpack: ['source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
    cbor2: ['cargo_acquisition_sha256', 'cargo_lock_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
    blake3: ['cargo_acquisition_sha256', 'final_cargo_lock_sha256', 'lock_patch_sha256', 'original_cargo_lock_sha256', 'source_patch_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
    cryptography: ['cargo_acquisition_sha256', 'cargo_lock_sha256', 'source_sha256', 'version', 'wheel_filename', 'wheel_sha256'],
    openssl: ['source_sha256', 'version'],
    pysodium: ['conversion_patch_sha256', 'libsodium_source_sha256', 'libsodium_version', 'source_archive_sha256', 'source_commit', 'source_project', 'version', 'wheel_filename', 'wheel_sha256'],
};

export function validateProvenance(provenance) {
    requireExactKeys(provenance, ['schema', 'source', 'runtime', 'toolchain', 'packages', 'packaging', 'consumers'], 'Provenance');
    if (provenance.schema !== 'fortweb.runtime-package-provenance.v2') {
        throw new Error('Unsupported provenance schema.');
    }
    requireExactKeys(provenance.source, ['fortweb_commit_sha', 'source_identity_sha256'], 'Provenance source');
    requireCommit(provenance.source.fortweb_commit_sha, 'Provenance FortWeb commit');
    requireDigest(provenance.source.source_identity_sha256, 'Provenance source identity');
    requireExactKeys(provenance.runtime, [
        'baseline_runtime_source_identity_sha256',
        'baseline_runtime_final_verification_sha256',
        'baseline_runtime_canonical_inventory_sha256',
        'baseline_runtime_tree_aggregate_sha256',
        'current_runtime_inventory_sha256',
        'current_runtime_tree_aggregate_sha256',
        'runtime_closure_sha256',
        'wheelhouse_manifest_sha256',
    ], 'Provenance runtime');
    for (const [key, value] of Object.entries(provenance.runtime)) requireDigest(value, `Provenance runtime ${key}`);
    requireExactKeys(provenance.toolchain, ['wheelhouse_toolchain_sha256', 'pyodide_version', 'python_version', 'emscripten_version', 'abi', 'pyodide_release_commit', 'pyodide_lock_sha256', 'pyodide_core_sha256', 'xbuildenv_sha256', 'emsdk_commit', 'rust_version', 'node_version', 'node_sha256', 'node_bytes'], 'Provenance toolchain');
    for (const key of [
        'wheelhouse_toolchain_sha256', 'pyodide_lock_sha256', 'pyodide_core_sha256',
        'xbuildenv_sha256', 'node_sha256',
    ]) {
        requireDigest(provenance.toolchain[key], `Provenance toolchain ${key}`);
    }
    for (const key of ['pyodide_release_commit', 'emsdk_commit']) {
        requireCommit(provenance.toolchain[key], `Provenance toolchain ${key}`);
    }
    for (const key of [
        'pyodide_version', 'python_version', 'emscripten_version', 'abi',
        'rust_version', 'node_version',
    ]) {
        requireNonEmptyString(provenance.toolchain[key], `Provenance toolchain ${key}`);
    }
    if (!Number.isSafeInteger(provenance.toolchain.node_bytes) || provenance.toolchain.node_bytes <= 0) {
        throw new Error('Provenance toolchain node_bytes must be a positive safe integer.');
    }
    requireExactKeys(provenance.packages, Object.keys(PACKAGE_KEYS), 'Provenance packages');
    for (const [name, keys] of Object.entries(PACKAGE_KEYS)) {
        requireExactKeys(provenance.packages[name], keys, `Provenance package ${name}`);
        for (const [key, value] of Object.entries(provenance.packages[name])) {
            if (key.endsWith('_sha256')) requireDigest(value, `Provenance package ${name} ${key}`);
            if (key === 'commit' || key === 'source_commit') requireCommit(value, `Provenance package ${name} ${key}`);
            if (typeof value !== 'string') throw new Error(`Provenance package ${name} ${key} must be a string.`);
        }
    }
    requireExactKeys(provenance.packaging, ['profile', 'package_inputs_sha256', 'source_files'], 'Provenance packaging');
    if (provenance.packaging.profile !== 'fortweb.deterministic-zip.v1') {
        throw new Error('Unexpected packaging profile.');
    }
    requireDigest(provenance.packaging.package_inputs_sha256, 'Package inputs digest');
    requireSourceRows(provenance.packaging.source_files, 'Packaging source files');
    requireExactKeys(provenance.consumers, ['fort_ios', 'fortoid'], 'Provenance consumers');
    for (const name of ['fort_ios', 'fortoid']) {
        requireExactKeys(provenance.consumers[name], ['commit', 'files'], `Provenance consumer ${name}`);
        if (!/^[0-9a-f]{40}$/.test(provenance.consumers[name].commit)) {
            throw new Error(`Invalid ${name} consumer commit.`);
        }
        for (const row of provenance.consumers[name].files) {
            requireExactKeys(row, ['path', 'sha256'], `${name} consumer file`);
            validatePackagePath(row.path, { allowMetadata: true });
            requireDigest(row.sha256, `${name} consumer file digest`);
        }
        const paths = provenance.consumers[name].files.map(({ path }) => path);
        if (JSON.stringify(paths) !== JSON.stringify([...paths].sort(comparePathBytes))
            || new Set(paths).size !== paths.length) {
            throw new Error(`${name} consumer files must be unique and path-byte sorted.`);
        }
    }
    return provenance;
}
