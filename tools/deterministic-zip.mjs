import { comparePathBytes, validatePackagePath } from './runtime-package-manifest.mjs';

const LOCAL_SIGNATURE = 0x04034b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const EOCD_SIGNATURE = 0x06054b50;
const VERSION_MADE_BY = 0x0314;
const VERSION_NEEDED = 0x0014;
const FLAGS = 0x0800;
const METHOD = 0;
const DOS_TIME = 0;
const DOS_DATE = 0x0021;
const EXTERNAL_ATTRIBUTES = (0o100644 << 16) >>> 0;

const crcTable = new Uint32Array(256);
for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
        value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
    }
    crcTable[index] = value >>> 0;
}

export function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
        crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
}

function uint16(value, label) {
    if (!Number.isSafeInteger(value) || value < 0 || value > 0xffff) {
        throw new Error(`${label} exceeds the classic ZIP uint16 limit.`);
    }
    return value;
}

function uint32(value, label) {
    if (!Number.isSafeInteger(value) || value < 0 || value > 0xffffffff) {
        throw new Error(`${label} exceeds the classic ZIP uint32 limit.`);
    }
    return value;
}

export function createDeterministicZip(entries) {
    if (!Array.isArray(entries) || entries.length === 0) {
        throw new Error('ZIP entries must be a non-empty array.');
    }
    uint16(entries.length, 'ZIP entry count');
    const normalized = entries.map(({ name, data }) => {
        validatePackagePath(name, { allowMetadata: true });
        const nameBytes = Buffer.from(name, 'utf8');
        const payload = Buffer.isBuffer(data) ? data : Buffer.from(data);
        uint16(nameBytes.length, `ZIP member name length for ${name}`);
        uint32(payload.length, `ZIP member size for ${name}`);
        return { name, nameBytes, data: payload, crc: crc32(payload) };
    }).sort((a, b) => comparePathBytes(a.name, b.name));

    const names = new Set();
    const foldedNames = new Set();
    for (const entry of normalized) {
        const folded = entry.name.toLowerCase();
        if (names.has(entry.name) || foldedNames.has(folded)) {
            throw new Error(`Duplicate ZIP member: ${entry.name}`);
        }
        names.add(entry.name);
        foldedNames.add(folded);
    }

    const localParts = [];
    const centralParts = [];
    let localOffset = 0;
    for (const entry of normalized) {
        const local = Buffer.alloc(30);
        local.writeUInt32LE(LOCAL_SIGNATURE, 0);
        local.writeUInt16LE(VERSION_NEEDED, 4);
        local.writeUInt16LE(FLAGS, 6);
        local.writeUInt16LE(METHOD, 8);
        local.writeUInt16LE(DOS_TIME, 10);
        local.writeUInt16LE(DOS_DATE, 12);
        local.writeUInt32LE(entry.crc, 14);
        local.writeUInt32LE(entry.data.length, 18);
        local.writeUInt32LE(entry.data.length, 22);
        local.writeUInt16LE(entry.nameBytes.length, 26);
        local.writeUInt16LE(0, 28);
        localParts.push(local, entry.nameBytes, entry.data);

        const central = Buffer.alloc(46);
        central.writeUInt32LE(CENTRAL_SIGNATURE, 0);
        central.writeUInt16LE(VERSION_MADE_BY, 4);
        central.writeUInt16LE(VERSION_NEEDED, 6);
        central.writeUInt16LE(FLAGS, 8);
        central.writeUInt16LE(METHOD, 10);
        central.writeUInt16LE(DOS_TIME, 12);
        central.writeUInt16LE(DOS_DATE, 14);
        central.writeUInt32LE(entry.crc, 16);
        central.writeUInt32LE(entry.data.length, 20);
        central.writeUInt32LE(entry.data.length, 24);
        central.writeUInt16LE(entry.nameBytes.length, 28);
        central.writeUInt16LE(0, 30);
        central.writeUInt16LE(0, 32);
        central.writeUInt16LE(0, 34);
        central.writeUInt16LE(0, 36);
        central.writeUInt32LE(EXTERNAL_ATTRIBUTES, 38);
        central.writeUInt32LE(uint32(localOffset, 'Local header offset'), 42);
        centralParts.push(central, entry.nameBytes);
        localOffset += local.length + entry.nameBytes.length + entry.data.length;
        uint32(localOffset, 'Local data extent');
    }

    const centralDirectory = Buffer.concat(centralParts);
    const centralOffset = uint32(localOffset, 'Central directory offset');
    const centralSize = uint32(centralDirectory.length, 'Central directory size');
    const eocd = Buffer.alloc(22);
    eocd.writeUInt32LE(EOCD_SIGNATURE, 0);
    eocd.writeUInt16LE(0, 4);
    eocd.writeUInt16LE(0, 6);
    eocd.writeUInt16LE(entries.length, 8);
    eocd.writeUInt16LE(entries.length, 10);
    eocd.writeUInt32LE(centralSize, 12);
    eocd.writeUInt32LE(centralOffset, 16);
    eocd.writeUInt16LE(0, 20);
    return Buffer.concat([...localParts, centralDirectory, eocd]);
}
