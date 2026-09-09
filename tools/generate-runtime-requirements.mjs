import { canonicalJson, REQUIREMENTS_SCHEMA, sha256 } from './runtime-package-manifest.mjs';

export const RUNTIME_REQUIREMENTS = {
    capabilities: {
        bundled_assets_only: {
            description: 'All runtime assets must be served from the application bundle.',
            required: true,
        },
        deterministic_entrypoint: {
            description: 'The runtime entrypoint must be loaded from a deterministic package-relative path.',
            required: true,
        },
        main_frame_provenance: {
            description: 'Bridge messages and navigation must be restricted to the main document frame.',
            required: true,
        },
        no_fallback_shell_substitution: {
            description: 'The runtime must not substitute a fallback shell when the declared entrypoint is unavailable.',
            required: true,
        },
        origin_provenance: {
            description: 'Bridge messages must be restricted to the configured origin with exact host matching.',
            required: true,
        },
        persistent_storage_partition: {
            description: 'IndexedDB must persist across launches within a stable storage partition.',
            required: true,
        },
        remote_network_prohibition: {
            description: 'General network access must be prohibited. Only bundled assets may be loaded.',
            required: true,
        },
        secure_context: {
            description: 'The runtime must execute in a secure context.',
            required: true,
        },
        stable_origin_across_launches: {
            description: 'The document origin must be stable across app launches.',
            required: true,
        },
        worker_availability: {
            description: 'Web Workers must be available for the Pyodide runtime.',
            required: true,
        },
    },
    forbidden_behaviors: [
        'network_fetch',
        'service_worker_registration',
        'general_purpose_browsing',
        'localhost_or_loopback_origin',
        'http_fallback',
    ],
    payload_profile: 'offline-runtime',
    producer: 'fortweb',
    schema: REQUIREMENTS_SCHEMA,
    version: 1,
};

export function serializeRuntimeRequirements() {
    const text = canonicalJson(RUNTIME_REQUIREMENTS);
    if (Buffer.byteLength(text) !== 1581 || sha256(text) !== '990bfa32719dac5eaa6bdfc2bf17de29df92720c1e47917fcfd083bbf3202781') {
        throw new Error('Runtime requirements bytes do not match the frozen consumer contract.');
    }
    return text;
}
