import assert from 'node:assert/strict';
import test from 'node:test';

import { generateReleaseMetadata, serializeReleaseMetadata } from './generate-release-metadata.mjs';

test('release metadata is canonical and explicitly unpublished', () => {
    const values = {
        artifactSha256: '1'.repeat(64),
        artifactBytes: 123,
        fortwebCommitSha: '2'.repeat(40),
        ref: 'refs/heads/pyodide-314-runtime',
    };
    const release = generateReleaseMetadata(values);
    assert.equal(release.publication.status, 'unpublished');
    assert.equal(release.attestation.required, true);
    assert.equal(release.attestation.present, false);
    assert.equal(release.attestation.verified, false);
    assert.equal(release.workflow_identity, 'unpublished-local-build');
    assert.equal(release.commit_sha, values.fortwebCommitSha);
    assert.equal(release.ref_name, 'pyodide-314-runtime');
    assert.match(release.attestation.verify_command, /github\\\.com/);
    assert.equal(JSON.parse(serializeReleaseMetadata(values)).artifact_bytes, 123);
});

test('release metadata requires explicit source identity', () => {
    const values = { artifactSha256: '1'.repeat(64), artifactBytes: 123 };
    assert.throws(() => generateReleaseMetadata(values), /FortWeb commit/);
});
