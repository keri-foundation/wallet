import assert from 'node:assert/strict';
import test from 'node:test';

import { createDeterministicZip } from './deterministic-zip.mjs';

test('ZIP bytes are stable and use the fixed header profile', () => {
    const entries = [
        { name: 'fortweb-runtime/b.txt', data: Buffer.from('b') },
        { name: 'fortweb-runtime/a.txt', data: Buffer.from('a') },
    ];
    const one = createDeterministicZip(entries);
    const two = createDeterministicZip([...entries].reverse());
    assert.deepEqual(one, two);
    assert.equal(one.readUInt32LE(0), 0x04034b50);
    assert.equal(one.readUInt16LE(4), 0x0014);
    assert.equal(one.readUInt16LE(6), 0x0800);
    assert.equal(one.readUInt16LE(8), 0);
    assert.equal(one.readUInt16LE(one.length - 14), 2);
    assert.equal(one.readUInt16LE(one.length - 12), 2);
});

test('ZIP writer rejects duplicate and unsafe members', () => {
    assert.throws(() => createDeterministicZip([
        { name: 'fortweb-runtime/a', data: Buffer.alloc(0) },
        { name: 'fortweb-runtime/a', data: Buffer.alloc(0) },
    ]), /Duplicate ZIP member/);
    assert.throws(() => createDeterministicZip([
        { name: 'fortweb-runtime/A', data: Buffer.alloc(0) },
        { name: 'fortweb-runtime/a', data: Buffer.alloc(0) },
    ]), /Duplicate ZIP member/);
    assert.throws(() => createDeterministicZip([
        { name: 'fortweb-runtime/../a', data: Buffer.alloc(0) },
    ]), /path/i);
});
