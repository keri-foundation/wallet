# Runtime package lineage

FortWeb adapted selected ideas from three pull requests by Jay. The implementation was written in this branch. No donor commit was cherry-picked.

| Donor | Frozen donor commit | Adapted behavior |
| --- | --- | --- |
| [PR #27](https://github.com/keri-foundation/fortweb/pull/27) | [`e079701a337468acb1484a1c2cf86acc517d8464`](https://github.com/keri-foundation/fortweb/commit/e079701a337468acb1484a1c2cf86acc517d8464) | Runtime package contract, release metadata, package manifest and verifier structure, local server guidance, copy icon, and modular worker behavior. |
| [PR #32](https://github.com/keri-foundation/fortweb/pull/32) | [`2d7c0883798a75b074407a3e205391c7e4195ba2`](https://github.com/keri-foundation/fortweb/commit/2d7c0883798a75b074407a3e205391c7e4195ba2) | Empty-state, route, sidebar, runtime-origin, and browser-noise test cases. |
| [PR #35](https://github.com/keri-foundation/fortweb/pull/35) | [`b179e868c997d29479a61ecec8ad5834a00d1578`](https://github.com/keri-foundation/fortweb/commit/b179e868c997d29479a61ecec8ad5834a00d1578) | Pyodide boot canary, runtime configuration, and browser integration test structure. |

Jay authored the donor commits. His recorded commit email is `alexander.elliot.it@protonmail.com`.

## Adaptation boundary

FortWeb keeps the donor concepts only where they match the Pyodide 314 runtime design. It replaces the legacy Pyodide 0.29.3 and CPython 3.13 package set, old `hio_web` and `keri_web` wheels, `pychloride`, absolute runtime paths, the monolithic worker, hard-coded origin data, and drawer-only readiness checks.

The committed source boundary includes the runtime package schema and reusable manifest, metadata, deterministic ZIP, and verifier primitives. The public package entrypoint is tools/package-runtime.mjs. It captures current source and verifies the compiled runtime before packaging. Previous acceptance-run orchestration remains historical evidence. A later source change must rebuild the runtime and package before any extracted-package or consumer claim is valid.

The eventual pull request description must retain this attribution and the no-cherry-pick statement.

## Current producer inputs

The public producer captures FortWeb's current source, including working
changes. For this PR, use normal HIO commit
`7b0350eab3115f42cd6be5dee2b203d052a320aa` and Keripy webbaser commit
`3d504ba4f9ce52cab0bc95bbf65642be7bf29614`.
The Keripy dependency patch selects HIO 0.7.20 while its upstream package
release is pending. The compiled baseline remains the accepted Pyodide 314
wheelhouse. See the build document for the input format and commands.

`tools/package-runtime.mjs` verifies current source and runtime bytes before it
produces the ZIP, sidecar, and release metadata. A source, runtime, wheel,
harness, or package change requires fresh products and evidence. Publication
still needs an immutable acquisition location and the required CI inputs.

## Mobile consumer handoff

The downstream handoff must refresh each consumer branch before it imports a
package. The current validated source heads are:

| Consumer | Validated source | Import overlay | Acquisition change still required |
| --- | --- | --- | --- |
| Fort iOS PR #34 | `095663cec33745714a3bf22f15a5d0a8d4608d3c` | Adds wrapper-owned root `index.html` | Replace its FortWeb checkout pin to donor commit `e079701a337468acb1484a1c2cf86acc517d8464` with an immutable final package identity. Make archive and export consume that same imported package. |
| Fortoid PR #24 | `ac1e47fcd1d3f34cbb18482f205ac675b13fdad7` | None in the current importer | Replace `config/fortweb-runtime.json` pin to donor commit `e079701a337468acb1484a1c2cf86acc517d8464` with an immutable final package identity. Remove the active Pyodide `0.29.3`, CPython 3.13, and old wheel assumptions. |

The package manifest contains the consumer snapshots that were frozen when the
producer ran. A later live importer gate is a separate record and can use a
newer consumer head. It does not rewrite or relabel the package manifest.

Fort iOS package-import checks do not prove its archive and export path. The
archive and export targets still run source synchronization, and the separate
slow Pyodide lane still carries its own old runtime assumptions. The consumer
must import the final package, apply `index.html`, recompute the complete
post-overlay digest, build the final `.xcarchive`, verify the archived payload,
and then export and verify the IPA.

Fortoid still contains active `0.29.3` assumptions in its WebView runtime and
tests, including old `/vendor/pyodide/0.29.3/` paths and CPython 3.13 wheel
names. Its importer can accept the new schema while the application runtime is
still stale. The consumer must update those assumptions, import the final
package, recompute its complete post-overlay digest, and prove the final APK
and AAB.

Importer and static validator success proves only package ingestion and schema
compatibility. It does not prove WKWebView or Android WebView execution,
publication, deployment, release, or shipping.
