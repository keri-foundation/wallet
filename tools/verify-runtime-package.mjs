import { constants } from 'node:fs';
import { lstat, open, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { crc32 } from './deterministic-zip.mjs';
import { serializeReleaseMetadata } from './generate-release-metadata.mjs';
import { serializeRuntimeRequirements } from './generate-runtime-requirements.mjs';
import {
    canonicalJson,
    comparePathBytes,
    REQUIREMENTS_PATH,
    sha256,
    validateManifest,
    validatePackagePath,
    ZIP_BASENAME,
} from './runtime-package-manifest.mjs';

const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PREFIX = 'fortweb-runtime/';
const EXPECTED_EXTERNAL = [ZIP_BASENAME, `${ZIP_BASENAME}.sha256`, 'fortweb-release.json']
    .sort(comparePathBytes);

function sameState(left, right) {
    return left.dev === right.dev
        && left.ino === right.ino
        && left.mode === right.mode
        && left.nlink === right.nlink
        && left.size === right.size
        && left.mtimeNs === right.mtimeNs
        && left.ctimeNs === right.ctimeNs;
}

async function assertRealRoot(root) {
    const info = await lstat(root, { bigint: true });
    if (!info.isDirectory() || info.isSymbolicLink()) {
        throw new Error(`product root is not a real directory: ${root}`);
    }
}

async function stableRead(root, filename) {
    validatePackagePath(filename, { allowMetadata: true });
    const target = path.join(root, filename);
    const handle = await open(target, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const before = await handle.stat({ bigint: true });
        if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n) {
            throw new Error(`product entry is not one unaliased regular file: ${filename}`);
        }
        const bytes = await handle.readFile();
        const after = await handle.stat({ bigint: true });
        const current = await lstat(target, { bigint: true });
        if (!sameState(before, after) || !sameState(after, current) || BigInt(bytes.length) !== after.size) {
            throw new Error(`product entry changed while read: ${filename}`);
        }
        return bytes;
    } finally {
        await handle.close();
    }
}

function readUInt16(buffer, offset) {
    if (offset + 2 > buffer.length) throw new Error('truncated ZIP uint16');
    return buffer.readUInt16LE(offset);
}

function readUInt32(buffer, offset) {
    if (offset + 4 > buffer.length) throw new Error('truncated ZIP uint32');
    return buffer.readUInt32LE(offset);
}

export function parseDeterministicZip(bytes) {
    if (bytes.length < 22) throw new Error('ZIP is too short.');
    const eocd = bytes.length - 22;
    const entryCount = readUInt16(bytes, eocd + 10);
    if (readUInt32(bytes, eocd) !== 0x06054b50
        || readUInt16(bytes, eocd + 4) !== 0
        || readUInt16(bytes, eocd + 6) !== 0
        || readUInt16(bytes, eocd + 8) !== entryCount
        || entryCount < 4
        || readUInt16(bytes, eocd + 20) !== 0) {
        throw new Error('ZIP EOCD is not canonical.');
    }
    const centralSize = readUInt32(bytes, eocd + 12);
    const centralOffset = readUInt32(bytes, eocd + 16);
    if (centralOffset + centralSize !== eocd) throw new Error('ZIP central extent mismatch.');

    const central = [];
    let cursor = centralOffset;
    for (let index = 0; index < entryCount; index += 1) {
        if (readUInt32(bytes, cursor) !== 0x02014b50) throw new Error('Invalid central signature.');
        const madeBy = readUInt16(bytes, cursor + 4);
        const needed = readUInt16(bytes, cursor + 6);
        const flags = readUInt16(bytes, cursor + 8);
        const method = readUInt16(bytes, cursor + 10);
        const dosTime = readUInt16(bytes, cursor + 12);
        const dosDate = readUInt16(bytes, cursor + 14);
        const crc = readUInt32(bytes, cursor + 16);
        const compressed = readUInt32(bytes, cursor + 20);
        const uncompressed = readUInt32(bytes, cursor + 24);
        const nameLength = readUInt16(bytes, cursor + 28);
        const extraLength = readUInt16(bytes, cursor + 30);
        const commentLength = readUInt16(bytes, cursor + 32);
        const disk = readUInt16(bytes, cursor + 34);
        const internal = readUInt16(bytes, cursor + 36);
        const external = readUInt32(bytes, cursor + 38);
        const localOffset = readUInt32(bytes, cursor + 42);
        if (madeBy !== 0x0314 || needed !== 0x0014 || flags !== 0x0800
            || method !== 0 || dosTime !== 0 || dosDate !== 0x0021
            || extraLength !== 0 || commentLength !== 0 || disk !== 0
            || internal !== 0 || external !== ((0o100644 << 16) >>> 0)
            || compressed !== uncompressed) {
            throw new Error('Central member metadata is not canonical.');
        }
        const nameStart = cursor + 46;
        const nameEnd = nameStart + nameLength;
        if (nameEnd > eocd) throw new Error('Truncated central name.');
        const name = bytes.subarray(nameStart, nameEnd).toString('utf8');
        if (!Buffer.from(name, 'utf8').equals(bytes.subarray(nameStart, nameEnd))) {
            throw new Error('ZIP member name is not valid UTF-8.');
        }
        validatePackagePath(name, { allowMetadata: true });
        if (!name.startsWith(PREFIX)) throw new Error(`ZIP member is outside ${PREFIX}.`);
        central.push({ name, crc, size: compressed, localOffset });
        cursor = nameEnd;
    }
    if (cursor !== eocd || cursor - centralOffset !== centralSize) {
        throw new Error('ZIP central count or size mismatch.');
    }
    const names = central.map(({ name }) => name);
    const sorted = [...names].sort(comparePathBytes);
    const folded = new Set(names.map((name) => name.toLowerCase()));
    if (JSON.stringify(names) !== JSON.stringify(sorted) || new Set(names).size !== entryCount || folded.size !== entryCount) {
        throw new Error('ZIP members are not unique and byte sorted.');
    }

    const members = new Map();
    let expectedOffset = 0;
    for (const entry of central) {
        const offset = entry.localOffset;
        if (offset !== expectedOffset || readUInt32(bytes, offset) !== 0x04034b50) {
            throw new Error('Invalid local-header offset or signature.');
        }
        const needed = readUInt16(bytes, offset + 4);
        const flags = readUInt16(bytes, offset + 6);
        const method = readUInt16(bytes, offset + 8);
        const dosTime = readUInt16(bytes, offset + 10);
        const dosDate = readUInt16(bytes, offset + 12);
        const crc = readUInt32(bytes, offset + 14);
        const compressed = readUInt32(bytes, offset + 18);
        const uncompressed = readUInt32(bytes, offset + 22);
        const nameLength = readUInt16(bytes, offset + 26);
        const extraLength = readUInt16(bytes, offset + 28);
        if (needed !== 0x0014 || flags !== 0x0800 || method !== 0 || dosTime !== 0
            || dosDate !== 0x0021 || extraLength !== 0 || crc !== entry.crc
            || compressed !== entry.size || uncompressed !== entry.size) {
            throw new Error('Local and central metadata differ.');
        }
        const nameStart = offset + 30;
        const nameEnd = nameStart + nameLength;
        const dataEnd = nameEnd + compressed;
        const name = bytes.subarray(nameStart, nameEnd).toString('utf8');
        if (name !== entry.name || dataEnd > centralOffset) throw new Error('Invalid local member extent.');
        const payload = bytes.subarray(nameEnd, dataEnd);
        if (crc32(payload) !== crc) throw new Error(`CRC mismatch for ${name}.`);
        members.set(name.slice(PREFIX.length), Buffer.from(payload));
        expectedOffset = dataEnd;
    }
    if (expectedOffset !== centralOffset || members.size !== entryCount) {
        throw new Error('ZIP local extent or member count mismatch.');
    }
    return members;
}

export async function verifyProduct(productDir) {
    const root = path.resolve(productDir);
    await assertRealRoot(root);
    const entries = (await readdir(root, { withFileTypes: true }))
        .map((entry) => {
            if (!entry.isFile() || entry.isSymbolicLink()) {
                throw new Error(`Unexpected product entry type: ${entry.name}`);
            }
            return entry.name;
        })
        .sort(comparePathBytes);
    if (JSON.stringify(entries) !== JSON.stringify(EXPECTED_EXTERNAL)) {
        throw new Error('External product file set is not exact.');
    }
    const zip = await stableRead(root, ZIP_BASENAME);
    const sidecar = await stableRead(root, `${ZIP_BASENAME}.sha256`);
    const release = await stableRead(root, 'fortweb-release.json');
    const zipDigest = sha256(zip);
    if (!sidecar.equals(Buffer.from(`${zipDigest}  ${ZIP_BASENAME}\n`))) {
        throw new Error('External ZIP sidecar mismatch.');
    }
    let releaseValue;
    try {
        releaseValue = JSON.parse(release.toString('utf8'));
    } catch (error) {
        throw new Error('Release metadata is not valid JSON.', { cause: error });
    }
    if (!release.equals(Buffer.from(serializeReleaseMetadata({
        artifactSha256: zipDigest,
        artifactBytes: zip.length,
        fortwebCommitSha: releaseValue.commit_sha,
        ref: releaseValue.ref,
    })))) {
        throw new Error('Release metadata mismatch.');
    }
    const members = parseDeterministicZip(zip);
    const manifestBytes = members.get('manifest.json');
    const checksumBytes = members.get('checksums.sha256');
    if (!manifestBytes || !checksumBytes) throw new Error('ZIP metadata is missing.');
    const manifest = JSON.parse(manifestBytes.toString('utf8'));
    validateManifest(manifest);
    if (releaseValue.commit_sha !== manifest.fortweb_commit_sha) {
        throw new Error('Release metadata commit does not match the package manifest.');
    }
    if (!manifestBytes.equals(Buffer.from(canonicalJson(manifest)))) {
        throw new Error('Manifest JSON is not canonical.');
    }
    if (!checksumBytes.equals(Buffer.from(`${sha256(manifestBytes)}  manifest.json\n`))) {
        throw new Error('Internal checksum mismatch.');
    }
    if (!members.get(REQUIREMENTS_PATH)?.equals(Buffer.from(serializeRuntimeRequirements()))) {
        throw new Error('Runtime requirements mismatch.');
    }
    const content = new Map([...members].filter(([name]) => !['manifest.json', 'checksums.sha256'].includes(name)));
    if (content.size !== manifest.files.length) throw new Error('Content closure differs from the manifest inventory.');
    for (const row of manifest.files) {
        const payload = content.get(row.path);
        if (!payload || payload.length !== row.bytes || sha256(payload) !== row.sha256) {
            throw new Error(`Manifest inventory mismatch for ${row.path}.`);
        }
    }
    if (new Set(manifest.files.map(({ path: memberPath }) => memberPath)).size !== content.size) {
        throw new Error('Manifest and ZIP content closure differ.');
    }
    const productFiles = [];
    for (const filename of EXPECTED_EXTERNAL) {
        const payload = await stableRead(root, filename);
        productFiles.push({ bytes: payload.length, path: filename, sha256: sha256(payload) });
    }
    return {
        manifest_rows: manifest.files.length,
        ok: true,
        product_files: productFiles,
        zip_bytes: zip.length,
        zip_entries: members.size,
        zip_sha256: zipDigest,
    };
}

function parseArgs(arguments_) {
    const index = arguments_.indexOf('--product-dir');
    if (index === -1 || !arguments_[index + 1] || arguments_.length !== 2) {
        throw new Error('Usage: node tools/verify-runtime-package.mjs --product-dir <directory>');
    }
    return path.resolve(process.cwd(), arguments_[index + 1]);
}

if (path.resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) {
    try {
        const report = await verifyProduct(parseArgs(process.argv.slice(2)));
        process.stdout.write(`${JSON.stringify(report)}\n`);
    } catch (error) {
        process.stderr.write(`verify-runtime-package: ${error.message}\n`);
        process.exitCode = 1;
    }
}

export { PROJECT_DIR };
