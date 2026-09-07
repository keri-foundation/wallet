# tools/keri-web — Fort Web browser KERI wheel (keri_web) source & build

This directory is the durable, repository-owned source of truth for the Fort Web
browser KERI wheel (`keri_web-2.0.0.dev6-py3-none-any.whl`). It replaces the
one-off `/tmp/keriweb-v2` build tree so the V2 browser runtime is a reproducible
product component rather than an experiment.

## Provenance

- **Artifact**: `keri_web` — the browser/Pyodide build of KERI used inside the
  Fort Web wallet worker (IndexedDB-backed WebBaser/WebKeeper storage).
- **Upstream repo**: `WebOfTrust/keripy`
- **Upstream commit**: `f4b9e3e886bc37c1e3b95cdc349cbbf8d4f25048` (`2.0.0-dev6`)
- **Output wheel**: `wheels/keri_web-2.0.0.dev6-py3-none-any.whl`
  (repo-owned source dir; `npm run build:runtime` copies `wheels/` into `dist/`).

Fort Web hosted KERI integration is **V2-only**. This wheel aligns the browser
core with the same f4b9 revision the hosted witness/watcher/Kf Boot stack runs.

## Structure

```
tools/keri-web/
  README.md        # this provenance file
  build.py         # deterministic build driver (see below)
  overlay/keri/**  # the explicit browser adaptation patchset (ONLY deltas vs f4b9)
```

Upstream f4b9 source is **not vendored**. `build.py` reconstructs it via
`git archive` from a local keripy checkout that contains the pinned commit, then
applies the overlay and builds the wheel. This keeps the reviewable delta tiny
(14 files) while remaining fully offline and deterministic.

## Browser adaptation patchset (overlay/keri/**)

The wheel = exact f4b9 source + exactly these browser-only adaptations:

| File | Adaptation |
|---|---|
| `keri/app/__init__.py` | Lazy package init — eager f4b9 pulled `agenting -> hio.core.http -> ssl` (unavailable in Pyodide). |
| `keri/app/habbing.py` | Restored browser `async def Habery.aclose()` (f4b9 dropped it; WebBaser/WebKeeper need awaited IndexedDB flush). |
| `keri/app/httping.py` | Browser fetch-based HTTP client (no `hio.core.http`/ssl). |
| `keri/app/keeping.py` | `openLMDB`/`LMDBer` guarded (fallback `None`); `Keeper` base degrades to `object`; `openKS` raises if no LMDB. |
| `keri/app/oobiing.py` | `import falcon` guarded (server-only; never used in browser OOBI flow). |
| `keri/app/webkeeping.py` | Browser-only `WebKeeper` (WebDBer-backed keystore). Not in upstream f4b9. |
| `keri/core/__init__.py` | Lazy package init (generated from f4b9's own export surface). |
| `keri/db/__init__.py` | Lazy package init + browser remaps: `Baser -> webbasing.WebBaser`, `dgKey/snKey/fetchTsgs -> basebasing`. `openLMDB`/`LMDBer` stay mapped to lmdb `dbing` (ImportError caught by `keeping`). |
| `keri/db/basebasing.py` | Browser-only baser base + key helpers (+ `fetchTsgs` port). Not in upstream f4b9. |
| `keri/db/koming.py` | `from .dbing import LMDBer` moved under `TYPE_CHECKING`; LMDBer annotations quoted (no `from __future__ import annotations`). |
| `keri/db/subing.py` | `from .dbing import LMDBer` moved under `TYPE_CHECKING`. |
| `keri/db/webbasing.py` | Browser-only `WebBaser` (WebDBer-backed event DB). Not in upstream f4b9. |
| `keri/db/webdbing.py` | Browser-only `WebDBer` IndexedDB storage backend; storage-handle lifecycle fix so `open`/`reopen`/close `_close_storage_handle` flushes durably. Not in upstream f4b9. |
| `keri/end/ending.py` | `falcon` and `hio.core http/wiring` imports guarded (server endpoints only, never run in browser). |

These are platform-boundary changes (lazy imports, LMDB/Falcon/HIO-server
isolation, browser async-close). They do **not** alter KERI verification
semantics. `build.py` enforces that the overlay is the *only* delta vs pristine
f4b9 — any other difference fails the build as stale-core contamination.

## Build command

From the repository root, with a local keripy checkout that contains the pinned
commit (pass `--keripy-dir`, set `KERI_WEB_KERIPY_DIR`, or rely on the default
sibling path detection):

```bash
python3 tools/keri-web/build.py
```

Determinism proof (two clean builds must agree):

```bash
python3 tools/keri-web/build.py --keripy-dir /path/to/keripy   # BUILD_A_SHA
python3 tools/keri-web/build.py --keripy-dir /path/to/keripy   # BUILD_B_SHA
```

`BUILD_A_SHA == BUILD_B_SHA` is preferred. If wheel ZIP metadata (member
timestamps/RECORD) causes byte drift between otherwise identical extractions,
the script reports equality of normalized members and this README documents why
byte hashes differ.

## Verification

- Delta check: the rebuilt `keri` tree must differ from pristine f4b9 on exactly
  the 14 overlay files (fail on stale-core contamination).
- Provenance snapshot of the working experimental source is preserved at:
  `~/Projects/keri-notes-fortweb-v2wip-backup-20260903/` (not in this repo).
