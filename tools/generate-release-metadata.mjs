import { canonicalJson, ZIP_BASENAME } from './runtime-package-manifest.mjs';

export function generateReleaseMetadata({ artifactSha256, artifactBytes, fortwebCommitSha, ref }) {
    if (typeof fortwebCommitSha !== 'string' || !/^[0-9a-f]{40}$/.test(fortwebCommitSha)) {
        throw new Error('Release metadata requires a lowercase 40-hex FortWeb commit.');
    }
    if (typeof ref !== 'string' || !/^refs\/heads\/[A-Za-z0-9._/-]+$/.test(ref)) {
        throw new Error('Release metadata requires an explicit branch ref.');
    }
    const refName = ref.slice('refs/heads/'.length);
    return {
        artifact_bytes: artifactBytes,
        artifact_name: ZIP_BASENAME,
        artifact_sha256: artifactSha256,
        attestation: {
            present: false,
            required: true,
            required_for_publication: true,
            status: 'not-produced',
            type: 'github-artifact-attestation',
            verified: false,
            verify_command: 'gh attestation verify fortweb-runtime-0.0.0.zip --repo keri-foundation/fortweb --cert-identity-regexp "^https://github\\.com/keri-foundation/fortweb/\\.github/workflows/fortweb-runtime-package\\.yml@refs/(heads/main|tags/v.*)$"',
        },
        commit_sha: fortwebCommitSha,
        entrypoint: 'app/index.html',
        package_version: '0.0.0',
        publication: { status: 'unpublished' },
        ref,
        ref_name: refName,
        repository: 'keri-foundation/fortweb',
        runtime_origin: 'https://appassets.androidplatform.net',
        schema_version: '1.0.0',
        workflow: '.github/workflows/fortweb-runtime-package.yml',
        workflow_identity: 'unpublished-local-build',
    };
}

export function serializeReleaseMetadata(values) {
    return canonicalJson(generateReleaseMetadata(values));
}
