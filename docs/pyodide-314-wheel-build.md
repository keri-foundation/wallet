# Pyodide 314 wheel and runtime build

Build the runtime from an explicit source manifest and SHA-256 digest. The
manifest selects every runtime file and wheel. Builds do not resolve Python
packages from a CDN or a developer checkout in the browser.

## Runtime contract

The browser uses Pyodide 314.0.5, Python 3.14.2, Emscripten 5.0.3, and ABI
`pyemscripten_2026_0_wasm32`. The selected set has 34 normal distributions.
Only the declared HIO and Keripy LMDB requirements are excluded in the browser.

The compiled-wheel baseline is the earlier accepted wheelhouse manifest with
SHA-256 `226411ef256e54c3382f5f845863a032eb13b61ace952b89a6aac04585900af9`.
Its compiled artifacts remain unchanged when normal HIO and Keripy source
wheels are replaced. Building those compiled artifacts from scratch remains a
separate toolchain procedure. The public source producer below verifies and
reuses the selected baseline bytes.

## Build normal source wheels

`scripts/build_runtime_source.py` accepts a verified baseline, a source
declaration, and package provenance. It builds each HIO and Keripy wheel twice,
checks byte equality and wheel RECORD entries, and composes a new runtime
source. Every source archive and patch must have an explicit SHA-256 digest.
Archive and patch paths are relative to the source declaration file.

Create archives with `git archive --format=tar <commit>`. Each archive must
have setup.py at its root. Record working changes as a separate binary Git
diff. A declaration has this shape (replace every digest with its actual value):

```json
{
  "schema": 1,
  "sources": [
    {
      "distribution": "hio",
      "repository": "https://github.com/ioflo/hio",
      "commit": "7b0350eab3115f42cd6be5dee2b203d052a320aa",
      "archive": "hio.tar",
      "sha256": "<archive-sha256>",
      "patches": []
    },
    {
      "distribution": "keri",
      "repository": "https://github.com/evanja57/keripy",
      "commit": "3d504ba4f9ce52cab0bc95bbf65642be7bf29614",
      "archive": "keri.tar",
      "sha256": "<archive-sha256>",
      "patches": [
        {"path": "keripy-hio-0.7.20.patch", "sha256": "35530bb3bc863f6f958c0583363769ca748ee5e8b9db1733ae7d5bb464f9a51c"}
      ]
    }
  ]
}
```

Use an environment with setuptools and wheel installed. Package provenance
records the baseline, package commits, compiled toolchain, and historical
consumer snapshots; it does not assert that those consumers ran this build.

```bash
python3 scripts/build_runtime_source.py \
  --baseline "$BASELINE" --baseline-sha256 "$BASELINE_SHA256" \
  --sources "$SOURCES_JSON" --sources-sha256 "$SOURCES_SHA256" \
  --package-inputs "$PACKAGE_INPUTS" --package-inputs-sha256 "$PACKAGE_INPUTS_SHA256" \
  --output build/runtime-source \
  --archive-output build/runtime-source.tar.gz
```

Outputs must be new. The command prints the new manifest path and digest.
Retain the source declaration and patches with the build evidence. Compare
packaged source modules and dependency metadata with the patched source before
promoting a changed wheel.

## Acquire, build, and verify

For a published runtime-source archive, supply both archive and manifest
identities. The CI workflow pins the public URL and both digests in Git so
fork PRs can use the same input. Update all three values together when the
runtime source changes. Manual workflow runs can supply a different URL and
both matching digests.

```bash
python3 scripts/acquire_runtime_source.py \
  --url "$FORTWEB_RUNTIME_SOURCE_URL" \
  --sha256 "$FORTWEB_RUNTIME_SOURCE_ARCHIVE_SHA256" \
  --manifest-sha256 "$FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256" \
  --output build/runtime-source

export FORTWEB_RUNTIME_SOURCE_MANIFEST=build/runtime-source/manifest.json
export FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256=<manifest-sha256>
npm ci
npm run typecheck
npm run build:runtime
npm run check:runtime-js
python3 scripts/verify_runtime_tree.py \
  --runtime-dir dist/runtime \
  --source-manifest "$FORTWEB_RUNTIME_SOURCE_MANIFEST" \
  --inventory-output test-results/runtime-inventory.json \
  --report-output test-results/runtime-verification.json
```

The builder generates the closure digest in the packaged PyScript config.
The independent verifier checks the complete inventory, selected input bytes,
and a fresh compilation of current TypeScript. File counts and runtime hashes
come from the generated inventory, not a previous acceptance run.

## Browser and package proof

Run the wheelhouse, WebBaser lifecycle, runtime canary, and application suites.
The lifecycle suite uses new workers for persistence and recovery. It checks
multi-witness receipt validation, invalid and repeated receipts, nested EXN
storage, OOBI resolution, and absence after clear.

Build two runtimes from frozen inputs. Package each into a new product folder:

```bash
npm run package:runtime -- --python python3 --output-dir dist/package
npm run verify:runtime-package -- --product-dir dist/package
python3 scripts/serve_local.py \
  --runtime-dir /absolute/extracted/fortweb-runtime --port 8765 --no-open
```

Require identical ZIP, sidecar, and release metadata bytes. The package command
records current source, complete runtime inventory, and package provenance.
The extracted-runtime server serves only that tree. Its optional local API
proxy accepts explicit loopback HTTP(S) destinations for local service tests.

Test hosted onboarding with both 1-of-1 and 3-of-4 profiles. Verify service key
state, receipt signatures, enabled watcher observation, and account queries.
Lock and reload the browser, then confirm the same persisted account state.
This proves the local producer and browser flow. Publication and mobile wrapper
execution require their own source and artifact evidence.
