# FortWeb runtime package contract

## Status

FortWeb implements schema version `1.0.0` for a deterministic offline runtime
package. Local producer, verifier, extracted-browser, and exact importer proof
exists for the current Pyodide 314 source boundary. No package has been
published, attested, deployed, or released.

## Products

One package operation must produce exactly these three files:

```text
fortweb-runtime-0.0.0.zip
fortweb-runtime-0.0.0.zip.sha256
fortweb-release.json
```

The sidecar contains the ZIP SHA-256. `fortweb-release.json` records the
producer commit, package identity, artifact size and digest, workflow identity,
publication state, and attestation state. A local product must remain
`unpublished` and `not-produced` for attestation.

The ZIP uses one `fortweb-runtime/` prefix and contains:

```text
fortweb-runtime/
  manifest.json
  checksums.sha256
  app/
  contracts/runtime-requirements.json
  pyscript-ci.toml
  vendor/
  wheels/
```

The package stores regular files only. It rejects directory members,
symlinks, hard links, special files, duplicate or case-folded paths, path
escapes, undeclared files, and non-deterministic ZIP metadata.

## Manifest

`manifest.json` uses schema version `1.0.0`, package name
`fortweb-runtime`, producer `fortweb`, payload profile `offline-runtime`, and
entrypoint `app/index.html`.

The manifest must include:

- the exact FortWeb commit and source identity;
- the exact Pyodide, Python, Emscripten, PyEmscripten ABI, Rust, Node, and
  xbuild environment identities;
- the exact Keripy and HIO commits and wheel hashes;
- the runtime tree, runtime closure, wheelhouse manifest, and package input
  hashes;
- the frozen runtime requirements contract;
- one sorted `files` row for every payload file, with its relative path, byte
  count, and lowercase SHA-256.

`checksums.sha256` contains only the exact digest of `manifest.json`. The
portable verifier must validate the external product set, raw ZIP structure,
manifest schema, checksums, file inventory, provenance, release metadata, and
final bytes. Consumers must fail closed on any mismatch.

## Producer boundary

The producer must build the runtime and package twice from the same frozen
inputs. Both runtime trees and all three package products must be byte
identical. The producer must independently recapture its source and execution
inputs after verification and remove its scratch roots before it publishes an
acceptance pointer.

Changing source, runtime, wheel, harness, package, or consumer-overlay bytes
invalidates the affected evidence. Do not relabel an older artifact or review.

See [Pyodide 314 wheel and runtime build](pyodide-314-wheel-build.md) for the
fixed toolchain and reproduction gates. See
[Runtime package lineage](runtime-package-lineage.md) for donor attribution and
the current artifact and consumer handoff.

## Consumer import

A consumer must acquire the ZIP by an immutable artifact identity and verify
its SHA-256 before import. The importer must verify the ZIP, manifest,
`checksums.sha256`, all declared payload rows, the runtime requirements
contract, and its platform configuration before it stages bytes.

Fort iOS currently adds one wrapper-owned root `index.html` after import. The
imported producer tree and the post-overlay consumer tree therefore have
different digests. Fortoid currently adds no overlay. Every consumer must
recompute and record its complete post-overlay digest after any import,
overlay, acquisition, or wrapper change.

Importer and static schema success do not prove execution in WKWebView or
Android WebView. They also do not prove an iOS archive or export, an Android
APK or AAB, publication, deployment, release, or shipping.

## Publication and release

Publication requires a clean source boundary, the exact accepted package
digest, a trusted workflow identity, a verified GitHub artifact attestation,
and an immutable acquisition location. Mobile release requires the final
post-overlay payload to run in each wrapper and requires proof from the final
`.xcarchive`, APK, and AAB. These are separate gates from producer and importer
proof.
