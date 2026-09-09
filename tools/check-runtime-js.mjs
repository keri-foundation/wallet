import { createHash } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_PROJECT_DIR = path.resolve(__dirname, '..');

function isConcreteTypeScriptPath(includePath) {
    return includePath.endsWith('.ts') && !includePath.endsWith('.d.ts') && !includePath.includes('*');
}

function normalizeRelativePath(filePath) {
    return filePath.split(path.sep).join('/');
}

export async function loadRuntimeOutputPaths(projectDir = DEFAULT_PROJECT_DIR, outputDirOverride = '') {
    const tsconfigPath = path.join(projectDir, 'tsconfig.build.json');
    const tsconfig = JSON.parse(await readFile(tsconfigPath, 'utf8'));
    const includes = Array.isArray(tsconfig.include) ? tsconfig.include : [];
    const rawOutDir = tsconfig.compilerOptions?.outDir;
    const rawRootDir = tsconfig.compilerOptions?.rootDir;
    const outDir = outputDirOverride || (typeof rawOutDir === 'string' && rawOutDir.length > 0 ? rawOutDir : '');
    const rootDir = typeof rawRootDir === 'string' && rawRootDir.length > 0 ? rawRootDir : '';

    return includes
        .filter(isConcreteTypeScriptPath)
        .map((includePath) => {
            const sourcePath = rootDir ? path.relative(rootDir, includePath) : includePath;
            const outputPath = outDir ? path.join(outDir, sourcePath) : sourcePath;
            return normalizeRelativePath(outputPath).replace(/\.ts$/u, '.js');
        });
}

async function fileFingerprint(filePath) {
    try {
        const buffer = await readFile(filePath);
        const digest = createHash('sha256').update(buffer).digest('hex');
        const info = await stat(filePath);
        return {
            exists: true,
            digest,
            size: info.size,
        };
    } catch {
        return {
            exists: false,
            digest: null,
            size: null,
        };
    }
}

export async function captureSnapshot(projectDir, relativePaths) {
    const entries = await Promise.all(
        relativePaths.map(async (relativePath) => {
            const absolutePath = path.join(projectDir, relativePath);
            return [relativePath, await fileFingerprint(absolutePath)];
        }),
    );

    return new Map(entries);
}

export function diffSnapshots(beforeSnapshot, afterSnapshot) {
    const changed = [];

    for (const [relativePath, before] of beforeSnapshot.entries()) {
        const after = afterSnapshot.get(relativePath);
        if (!after) {
            changed.push(relativePath);
            continue;
        }

        if (before.exists !== after.exists || before.digest !== after.digest || before.size !== after.size) {
            changed.push(relativePath);
        }
    }

    return changed.sort();
}

function runCommand(command, args, cwd) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, {
            cwd,
            stdio: 'inherit',
        });

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

async function readGitStatus(projectDir, relativePaths) {
    return new Promise((resolve, reject) => {
        const child = spawn('git', ['status', '--short', '--', ...relativePaths], {
            cwd: projectDir,
            stdio: ['ignore', 'pipe', 'pipe'],
        });

        let stdout = '';
        let stderr = '';

        child.stdout.on('data', (chunk) => {
            stdout += chunk.toString();
        });
        child.stderr.on('data', (chunk) => {
            stderr += chunk.toString();
        });

        child.on('error', reject);
        child.on('exit', (code) => {
            if (code === 0) {
                resolve(stdout.trim().split('\n').filter(Boolean));
                return;
            }

            reject(new Error(stderr.trim() || `git status failed with exit code ${code ?? 'unknown'}`));
        });
    });
}

function collectMissingOutputs(snapshot) {
    const missing = [];

    for (const [relativePath, entry] of snapshot.entries()) {
        if (!entry.exists) {
            missing.push(relativePath);
        }
    }

    return missing.sort();
}

export function createFailureMessage(changedOutputs, missingOutputs = []) {
    const lines = [
        '[check-runtime-js] generated runtime JavaScript output is invalid.',
        'TypeScript is the source of truth for FortWeb runtime modules.',
        'Generated runtime output under `dist/runtime` must exist and remain stable across repeated builds.',
        'Run `npm run build:runtime`, review the emitted output contract, and fix any non-deterministic or missing runtime JS before relying on it.',
    ];

    if (missingOutputs.length > 0) {
        lines.push('', 'Missing generated runtime outputs:', ...missingOutputs.map((outputPath) => `- ${outputPath}`));
    }

    if (changedOutputs.length > 0) {
        lines.push('', 'Generated runtime outputs that changed across repeated builds:', ...changedOutputs.map((outputPath) => `- ${outputPath}`));
    }

    return lines.join('\n');
}

export async function main(projectDir = DEFAULT_PROJECT_DIR) {
    const configuredRuntime = process.env.FORTWEB_RUNTIME_DIR ?? '';
    const runtimeOutput = configuredRuntime
        ? normalizeRelativePath(path.relative(projectDir, path.resolve(projectDir, configuredRuntime)))
        : '';
    if (runtimeOutput.startsWith('../') || path.isAbsolute(runtimeOutput)) {
        throw new Error('FORTWEB_RUNTIME_DIR must remain inside the repository.');
    }
    const runtimeOutputs = await loadRuntimeOutputPaths(projectDir, runtimeOutput);
    const buildArguments = ['run', 'build:runtime'];
    if (runtimeOutput) {
        buildArguments.push('--', '--out-dir', runtimeOutput);
    }
    await runCommand('npm', buildArguments, projectDir);

    const firstSnapshot = await captureSnapshot(projectDir, runtimeOutputs);
    const missingOutputs = collectMissingOutputs(firstSnapshot);

    await runCommand('npm', buildArguments, projectDir);

    const secondSnapshot = await captureSnapshot(projectDir, runtimeOutputs);
    const changedOutputs = diffSnapshots(firstSnapshot, secondSnapshot);
    const remainingMissingOutputs = collectMissingOutputs(secondSnapshot);
    const allMissingOutputs = Array.from(new Set([...missingOutputs, ...remainingMissingOutputs])).sort();

    if (allMissingOutputs.length > 0 || changedOutputs.length > 0) {
        throw new Error(createFailureMessage(changedOutputs, allMissingOutputs));
    }

    const dirtyGeneratedOutputs = await readGitStatus(projectDir, runtimeOutputs);
    if (dirtyGeneratedOutputs.length > 0) {
        process.stdout.write(`[check-runtime-js] note: generated runtime outputs are ignored/untracked or locally modified:\n${dirtyGeneratedOutputs.map((entry) => `- ${entry}`).join('\n')}\n`);
    }

    process.stdout.write('[check-runtime-js] generated runtime JavaScript output exists and is deterministic.\n');
}

const entrypointPath = process.argv[1] ? path.resolve(process.argv[1]) : null;

if (entrypointPath === fileURLToPath(import.meta.url)) {
    main().catch((error) => {
        process.stderr.write(`${error.message}\n`);
        process.exitCode = 1;
    });
}
