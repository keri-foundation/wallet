import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { existsSync, lstatSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RUNTIME = path.resolve(process.env.FORTWEB_RUNTIME_DIR ?? path.join(PROJECT_DIR, 'dist/runtime'));
const CLOSURE_SHA256 = sha256(readFileSync(path.join(RUNTIME, 'runtime-closure.json')));
const SOURCE_MANIFEST_SHA256 = process.env.FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256 ?? '';
assert.match(SOURCE_MANIFEST_SHA256, /^[0-9a-f]{64}$/);

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

function walk(root, prefix = '') {
    const rows = [];
    for (const entry of readdirSync(path.join(root, prefix), { withFileTypes: true })) {
        const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
        assert.equal(entry.isSymbolicLink(), false, `symlink in runtime: ${relative}`);
        if (entry.isDirectory()) {
            rows.push(...walk(root, relative));
        } else {
            assert.equal(entry.isFile(), true, `non-regular runtime entry: ${relative}`);
            assert.equal(lstatSync(path.join(root, relative)).nlink, 1, `hard-link alias in runtime: ${relative}`);
            rows.push(relative);
        }
    }
    return rows.sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right)));
}

function resolveLocalReference(reference, sourceFile) {
    assert.ok(reference && !reference.includes('\\') && !reference.includes('%'), `unsafe reference ${reference} from ${sourceFile}`);
    assert.doesNotMatch(reference, /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i, `external reference ${reference} from ${sourceFile}`);
    const clean = reference.split(/[?#]/, 1)[0];
    assert.ok(clean, `empty reference ${reference} from ${sourceFile}`);
    assert.ok(
        clean.startsWith('./') || clean.startsWith('../'),
        `non-relative reference ${reference} from ${sourceFile}`,
    );
    const target = path.resolve(path.dirname(sourceFile), clean);
    const relative = path.relative(RUNTIME, target);
    assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative));
    assert.ok(existsSync(target), `missing local resource ${reference} from ${sourceFile}`);
    assert.ok(lstatSync(target).isFile() && !lstatSync(target).isSymbolicLink());
    return target;
}

function resourceReferencesFromText(text, extension) {
    const references = [];
    if (extension === '.html') {
        for (const match of text.matchAll(/<(?:script|img|source|video|audio|iframe)[^>]+src=["']([^"']+)["']/gi)) {
            references.push(match[1]);
        }
        for (const match of text.matchAll(/<link[^>]+href=["']([^"']+)["']/gi)) {
            references.push(match[1]);
        }
    }
    if (extension === '.css') {
        for (const match of text.matchAll(/url\(\s*["']?([^"')\s]+)["']?\s*\)/gi)) {
            references.push(match[1]);
        }
        for (const match of text.matchAll(/@import\s+(?:url\()?\s*["']([^"']+)["']/gi)) {
            references.push(match[1]);
        }
    }
    if (extension === '.js') {
        const patterns = [
            /(?:import|export)[^;"']*?\bfrom\s*["']([^"']+)["']/g,
            /import\s*["']([^"']+)["']/g,
            /import\s*\(\s*["']([^"']+)["']/g,
            /new\s+URL\s*\(\s*["']([^"']+)["']/g,
        ];
        for (const pattern of patterns) {
            for (const match of text.matchAll(pattern)) {
                references.push(match[1]);
            }
        }
    }
    return references;
}

function resourceReferences(file) {
    return resourceReferencesFromText(readFileSync(file, 'utf8'), path.extname(file));
}

describe('complete runtime closure', () => {
    test('internal closure is exact and package-root relative', () => {
        const raw = readFileSync(path.join(RUNTIME, 'runtime-closure.json'));
        assert.equal(sha256(raw), CLOSURE_SHA256);
        assert.equal(raw.at(-1), 0x0a);
        const closure = JSON.parse(raw);
        assert.deepEqual(Object.keys(closure).sort(), [
            'dependency_exclusions', 'files', 'install_order', 'runtime', 'schema', 'source_manifest_sha256', 'wheels',
        ]);
        assert.equal(closure.schema, 1);
        assert.equal(closure.source_manifest_sha256, SOURCE_MANIFEST_SHA256);
        assert.equal(closure.files.length, 39);
        assert.equal(closure.wheels.length, 34);
        assert.equal(closure.install_order.length, 34);
        assert.deepEqual(closure.dependency_exclusions, [
            { owner: 'hio', requirement: 'lmdb>=1.7.5' },
            { owner: 'keri', requirement: 'lmdb==2.1.1' },
        ]);
        assert.deepEqual(closure.wheels.map((row) => row.filename), closure.install_order);
        assert.deepEqual(
            closure.files.map((row) => row.path),
            [
                ...closure.runtime.core_files.map((name) => `vendor/pyodide/314.0.5/${name}`),
                ...closure.install_order.map((name) => `wheels/${name}`),
            ],
        );
        for (const row of closure.files) {
            const target = path.join(RUNTIME, ...row.path.split('/'));
            const bytes = readFileSync(target);
            assert.equal(bytes.length, row.bytes, row.path);
            assert.equal(sha256(bytes), row.sha256, row.path);
        }
    });

    test('runtime root has only the reviewed product surface', () => {
        const paths = walk(RUNTIME);
        assert.ok(paths.length > 0);
        assert.deepEqual(
            [...new Set(paths.map((value) => value.split('/')[0]))].sort(),
            ['app', 'pyscript-ci.toml', 'runtime-closure.json', 'vendor', 'wheels'],
        );
        assert.equal(paths.some((value) => value.startsWith('app/') && (value.endsWith('.ts') || value.endsWith('.pyc'))), false);
        for (const forbidden of ['manifest.json', 'checksums.sha256', 'fortweb-release.json', 'contracts/runtime-requirements.json']) {
            assert.equal(paths.includes(forbidden), false);
        }
    });

    test('every HTML, CSS, and JavaScript module resource stays in the runtime tree', () => {
        const files = walk(RUNTIME).filter((value) => /\.(?:css|html|js)$/.test(value));
        let referenceCount = 0;
        for (const relative of files) {
            if (relative.startsWith('vendor/pyscript/')) {
                continue;
            }
            const source = path.join(RUNTIME, relative);
            for (const reference of resourceReferences(source)) {
                resolveLocalReference(reference, source);
                referenceCount += 1;
            }
        }
        assert.ok(referenceCount > 5);
    });

    test('production package loading has one owner and workers contain no package list', () => {
        const loader = readFileSync(path.join(RUNTIME, 'app/runtime/runtime_packages.py'), 'utf8');
        const lifecycle = readFileSync(path.join(PROJECT_DIR, 'python/run_webbaser_lifecycle.py'), 'utf8');
        const worker = readFileSync(path.join(RUNTIME, 'app/runtime/wallet-worker.py'), 'utf8');
        assert.equal((loader.match(/loadPackage\(/g) ?? []).length, 1);
        for (const source of [lifecycle, worker]) {
            assert.doesNotMatch(source, /loadPackage\(|PYODIDE_PACKAGE_NAMES|LOCAL_WHEEL_PATHS/);
            assert.match(source, /runtime_packages/);
        }
    });
});

test('reference validator rejects traversal and external schemes', () => {
    const source = path.join(RUNTIME, 'app/index.html');
    for (const value of [
        '../../../outside.js',
        '/absolute.js',
        'bare-package',
        'https://example.com/a.js',
        '//example.com/a.js',
        'file:///etc/passwd',
        './encoded%2fpath.js',
    ]) {
        assert.throws(() => resolveLocalReference(value, source));
    }
});

test('JavaScript scanner exposes every forbidden literal reference to the resolver', () => {
    const source = path.join(RUNTIME, 'app/app/main.js');
    for (const text of [
        'import value from "bare-package";',
        'export { value } from "https://example.com/value.js";',
        'import("/absolute.js");',
        'new URL("./encoded%2fpath.js", import.meta.url);',
    ]) {
        const references = resourceReferencesFromText(text, '.js');
        assert.equal(references.length, 1);
        assert.throws(() => resolveLocalReference(references[0], source));
    }
});
