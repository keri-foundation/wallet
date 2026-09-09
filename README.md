# Fortweb Wallet

Wallet for KERI. This is the universal web wallet based on Python running in the browser (Pyodide WASM).

## Local development (browser)

Pyodide is selected by `pyscript-ci.toml`. The `[fort_runtime_packages]`
manifest selects the reviewed wheel set and its relative artifact paths. The
HTTP **document root must be the parent of the `fortweb` directory** (in this
workspace, that is usually `libs/`).

Do **not** open only `http://127.0.0.1:8765/` against a server rooted at
`fortweb/app`. Configured runtime paths resolve from the FortWeb application
base, so a narrower document root returns HTML 404 pages for runtime modules.

From the `fortweb` repo:

```bash
python3 scripts/serve_local.py
```

Then use **`http://127.0.0.1:8765/fortweb/app/`** (the script redirects `/` there). Stop with Ctrl+C.

The **SharedArrayBuffer** / PyScript FAQ warning in the console is expected for a plain `http.server`; the app should still load. If wheel fetches fail, confirm nothing else is bound to the same port and that you are not mixing two different server roots in multiple tabs.

## Type checking

FortWeb now has an active TypeScript conversion lane for the runtime seam and the smallest adjacent app helpers.

This does **not** change the browser runtime path, bundling model, or local serve path. The browser still loads `.js` files from `app/`, but the first converted runtime files now use `.ts` as the source of truth and emit `.js` back into the same runtime path.

From the `fortweb` repo:

```bash
export FORTWEB_RUNTIME_SOURCE_MANIFEST=path/to/pyodide-314-wheelhouse/manifest.json
export FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256=<reviewed-manifest-sha256>
npm run build:runtime
npm run typecheck
npm run test:e2e
```

The source manifest must remain inside the repository and match the reviewed
Pyodide 314 wheelhouse manifest. The builder does not use private execution
state as an implicit source input.

CI and clean checkouts acquire the runtime source from an explicit HTTPS archive:

```bash
python3 scripts/acquire_runtime_source.py \
  --url "$FORTWEB_RUNTIME_SOURCE_URL" \
  --sha256 "$FORTWEB_RUNTIME_SOURCE_ARCHIVE_SHA256" \
  --manifest-sha256 "$FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256" \
  --output build/runtime-source
export FORTWEB_RUNTIME_SOURCE_MANIFEST=build/runtime-source/manifest.json
```

The archive must contain the reviewed manifest plus its `runtime/` and
`wheelhouse/` inputs. The acquisition command verifies the archive digest, the
explicit manifest digest, and every selected runtime and wheel artifact before
the builder can use it.

## Offline runtime package

FortWeb packages the verified runtime as three products:

```text
fortweb-runtime-0.0.0.zip
fortweb-runtime-0.0.0.zip.sha256
fortweb-release.json
```

Build a product from the selected source manifest and verified runtime:

```bash
npm run package:runtime -- --python python3 --output-dir dist/package
```

The command recompiles the current TypeScript for comparison, verifies every
runtime file, and records source and dependency provenance. Output directories
must be new. Serve an extracted product without source fallback:

```bash
python3 scripts/serve_local.py --runtime-dir /absolute/extracted/fortweb-runtime --port 8765 --no-open
```

Verify an existing product directory with:

```bash
npm run verify:runtime-package -- --product-dir path/to/product
```

The verifier checks the external product set, ZIP structure, manifest,
checksums, file inventory, provenance, and release metadata. A local package
remains unpublished and unattested. Importer success does not prove a mobile
runtime, an iOS archive, an Android APK or AAB, publication, deployment,
release, or shipping.

See [the package contract](docs/runtime-package-contract.md),
[the Pyodide 314 wheel and runtime build](docs/pyodide-314-wheel-build.md), and
[the source and consumer lineage](docs/runtime-package-lineage.md).

## Browser smoke tests

FortWeb now has a validation-first browser smoke harness using Playwright.

The smoke suite stays aligned with the current FortWeb runtime posture:

- it serves the app through `python3 scripts/serve_local.py`
- it keeps the browser entrypoint at `app/index.html`
- it checks one real app boot path plus deterministic fixture routes
- it does not introduce a bundler-first test runtime

From the `fortweb` repo:

```bash
npm run test:e2e
```

This command runs only the application smoke suite through `serve_local.py`.
The runtime canary, lifecycle, and source-wheelhouse suites use
`playwright.runtime.config.ts`. They require the matching
`serve_runtime_browser.py` mode and explicit runtime, inventory, source, and
evidence paths. The runtime workflow shows the complete CI invocation for each
suite.

Current smoke coverage:

- app boot through `/fortweb/app/`
- fixture index route
- populated identifiers fixture
- hosted witnesses fixture

The fixture routes remain the most stable UI validation surface because they render deterministic states without depending on live wallet data.

Current scope:

- `app/runtime/messages.ts` -> emits `app/runtime/messages.js`
- `app/runtime/method-catalog.ts` -> emits `app/runtime/method-catalog.js`
- `app/runtime/logger.ts` -> emits `app/runtime/logger.js`
- `app/runtime/bridge.ts` -> emits `app/runtime/bridge.js`
- `app/app/router.ts` -> emits `app/app/router.js`
- `app/app/session.ts` -> emits `app/app/session.js`

The current conversion slice stays intentionally narrow so FortWeb can replace JavaScript source files with TypeScript without forcing a bundler-first rewrite across the whole app.
