#!/usr/bin/env python3
"""
Reproducible build driver for the Fort Web browser KERI wheel (keri_web).

Invariant enforced by this script:

    exact upstream f4b9 source
        + explicit browser overlay (tools/keri-web/overlay/keri/**)
        -> deterministic, verified keri_web wheel

The wheel is written to <repo>/wheels/keri_web-2.0.0.dev6-py3-none-any.whl
(the repo-owned source dir that `npm run build:runtime` copies into dist).

Upstream source is NOT vendored into this repository. It is reconstructed from
a pinned keripy commit that must exist in a local checkout:

    upstream repo   : WebOfTrust/keripy
    upstream commit : f4b9e3e886bc37c1e3b95cdc349cbbf8d4f25048 (2.0.0-dev6)

Where to find that checkout:

  1. --keripy-dir <path>  (explicit)
  2. $KERI_WEB_KERIPY_DIR (environment)
  3. default: ../keripy resolved relative to this repository's parent that
     contains a `.git` and the pinned commit.

Usage (from the repository root):

    python3 tools/keri-web/build.py
    python3 tools/keri-web/build.py --keripy-dir /path/to/keripy
    python3 tools/keri-web/build.py --keep-work

A clean build must produce byte-identical wheels when re-run (BUILD_A == BUILD_B).
If wheel ZIP metadata causes byte drift, the script reports normalized-equality
instead of pretending byte determinism.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

UPSTREAM_COMMIT = "f4b9e3e886bc37c1e3b95cdc349cbbf8d4f25048"
WHEEL_NAME = "keri_web-2.0.0.dev6-py3-none-any.whl"
# The browser wheel is the `keri-web` distribution: Pyodide derives the package
# identity from the wheel *filename* (keri_web-*.whl -> canonical `keri-web`)
# and rejects a wheel whose internal .dist-info/METADATA does not match.
# Upstream f4b9 setup.py declares `name='keri'`, so we patch the reconstructed
# setup.py to the browser distribution name before pip builds the wheel.
DIST_NAME = "keri-web"
UPSTREAM_SETUP_NAME = "name='keri',"
# Browser overlay files, relative to overlay root (keri/...). These are the ONLY
# deltas the build is allowed to introduce over pristine f4b9. A rebuild that
# differs on any other file is treated as stale-core contamination and fails.
EXPECTED_OVERLAY = [
    "keri/app/__init__.py",
    "keri/app/habbing.py",
    "keri/app/httping.py",
    "keri/app/keeping.py",
    "keri/app/oobiing.py",
    "keri/app/webkeeping.py",
    "keri/core/__init__.py",
    "keri/db/__init__.py",
    "keri/db/basebasing.py",
    "keri/db/koming.py",
    "keri/db/subing.py",
    "keri/db/webbasing.py",
    "keri/db/webdbing.py",
    "keri/end/ending.py",
]


def repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))  # tools/keri-web -> repo root


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_py(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "keri.egg-info", "migrations")]
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root)] = p
    return out


def find_keripy_dir(explicit):
    if explicit:
        return explicit
    env = os.environ.get("KERI_WEB_KERIPY_DIR")
    if env and os.path.isdir(env):
        return env
    # default candidate: a sibling of the workspace-style parent that holds keripy.
    root = repo_root()
    candidates = [
        os.path.join(os.path.dirname(root), "keri-notes", "libs", "keripy"),
        os.path.join(os.path.dirname(os.path.dirname(root)), "keri-notes", "libs", "keripy"),
        os.path.join(os.path.dirname(root), "keripy"),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, ".git")):
            return c
    return None


def have_commit(keripy_dir, commit):
    try:
        out = subprocess.run(
            ["git", "-C", keripy_dir, "cat-file", "-t", commit],
            capture_output=True, text=True,
        )
        return out.returncode == 0 and out.stdout.strip() == "commit"
    except Exception:
        return False


def build(keripy_dir, keep_work):
    root = repo_root()
    overlay_root = os.path.join(root, "tools", "keri-web", "overlay")
    wheels_dir = os.path.join(root, "wheels")
    os.makedirs(wheels_dir, exist_ok=True)

    if keripy_dir is None or not have_commit(keripy_dir, UPSTREAM_COMMIT):
        raise SystemExit(
            "Cannot find a keripy checkout containing pinned commit %s.\n"
            "Pass --keripy-dir or set KERI_WEB_KERIPY_DIR." % UPSTREAM_COMMIT
        )

    work = os.path.join(root, "tools", "keri-web", ".build")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)

    # 1) reconstruct pristine f4b9 source
    archive = subprocess.run(
        ["git", "-C", keripy_dir, "archive", UPSTREAM_COMMIT, "src/keri", "setup.py"],
        capture_output=True,
    )
    if archive.returncode != 0:
        raise SystemExit("git archive failed: %s" % archive.stderr.decode())
    proc = subprocess.run(["tar", "-x", "-C", work], input=archive.stdout)
    if proc.returncode != 0:
        raise SystemExit("tar extraction failed")

    pristine_keri = os.path.join(work, "src", "keri")
    if not os.path.isdir(pristine_keri):
        raise SystemExit("pristine keri tree missing after archive")

    # 1b) rename the reconstructed distribution to keri-web so the built wheel's
    # internal .dist-info/METADATA matches the keri_web-*.whl filename. Without
    # this, pip builds a `keri`-named wheel and Pyodide rejects it as an
    # UnsupportedWheel (.dist-info 'keri-...' does not start with 'keri-web').
    setup_path = os.path.join(work, "setup.py")
    with open(setup_path) as fh:
        setup_src = fh.read()
    if UPSTREAM_SETUP_NAME not in setup_src:
        raise SystemExit(
            "Expected %r in reconstructed setup.py to rename to %r; setup.py "
            "format changed upstream." % (UPSTREAM_SETUP_NAME, DIST_NAME)
        )
    setup_src = setup_src.replace(UPSTREAM_SETUP_NAME, "name=%r," % DIST_NAME, 1)
    with open(setup_path, "w") as fh:
        fh.write(setup_src)
    print("distribution rename OK: keri -> %s (matches keri_web-*.whl filename)" % DIST_NAME)

    # 2) snapshot pristine hashes (for delta verification)
    pristine = walk_py(pristine_keri)
    pristine_sha = {rel: sha256_file(p) for rel, p in pristine.items()}

    # 3) apply overlay
    overlay_files = walk_py(overlay_root)  # keys are keri/...
    if sorted(overlay_files.keys()) != sorted(EXPECTED_OVERLAY):
        raise SystemExit(
            "Overlay file set changed vs EXPECTED_OVERLAY.\n"
            "actual  : %s\nexpected: %s"
            % (sorted(overlay_files.keys()), sorted(EXPECTED_OVERLAY))
        )
    for rel, src in overlay_files.items():
        dst = os.path.join(work, "src", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)

    # 4) verify the ONLY deltas vs pristine are the overlay files (no stale core)
    final = walk_py(pristine_keri)
    final_sha = {rel: sha256_file(p) for rel, p in final.items()}
    added = sorted(set(final_sha) - set(pristine_sha))
    changed = sorted(
        rel for rel in pristine_sha if rel in final_sha and pristine_sha[rel] != final_sha[rel]
    )
    deltas = added + changed
    # EXPECTED_OVERLAY keys are "keri/<rel>" (overlay-root relative); delta keys
    # are "<rel>" (relative to the keri package), so strip the "keri/" prefix.
    expected_rel = {e.split("/", 1)[1] for e in EXPECTED_OVERLAY}
    unexpected = [d for d in deltas if d not in expected_rel]
    missing = [e for e in expected_rel if e not in deltas]
    if unexpected:
        raise SystemExit(
            "STALE-CORE CONTAMINATION: unexpected deltas vs pristine f4b9:\n  %s"
            % "\n  ".join(unexpected)
        )
    if missing:
        raise SystemExit("Overlay files with no effect vs pristine (not applied?):\n  %s" % "\n  ".join(missing))
    print("delta verification OK: %d overlay deltas, no stale-core contamination" % len(deltas))

    # 5) normalize mtimes for byte-deterministic wheel output (ZIP member times
    #    otherwise follow file mtimes: pristine = commit time, overlay = now).
    FIXED_MTIME = 1600000000  # 2020-09-13 UTC, arbitrary but fixed
    for dp, dn, fn in os.walk(work):
        for name in fn:
            p = os.path.join(dp, name)
            try:
                os.utime(p, (FIXED_MTIME, FIXED_MTIME))
            except OSError:
                pass

    # 6) build wheel (mirrors original /tmp/keriweb-v2 command)
    dist = os.path.join(work, "dist")
    os.makedirs(dist, exist_ok=True)
    wheel = subprocess.run(
        ["python3", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "-w", dist, "."],
        cwd=work,
        capture_output=True, text=True,
    )
    if wheel.returncode != 0:
        sys.stderr.write(wheel.stdout)
        sys.stderr.write(wheel.stderr)
        raise SystemExit("pip wheel failed (exit %d)" % wheel.returncode)

    built_whls = [f for f in os.listdir(dist) if f.endswith(".whl")]
    if not built_whls:
        raise SystemExit("expected wheel not produced in %s" % dist)
    if WHEEL_NAME not in built_whls:
        raise SystemExit(
            "Unexpected wheel filename produced: %s (expected %s)\n"
            "The setup.py distribution rename to %r did not take effect."
            % (built_whls, WHEEL_NAME, DIST_NAME)
        )
    built = os.path.join(dist, WHEEL_NAME)

    # setuptools stamps .dist-info/* member times at build time -> byte drift
    # between otherwise identical builds. Rewrite the wheel with every member
    # timestamp pinned to the fixed epoch so output is byte-deterministic.
    # RECORD hashes are over member *content*, which this repack does not change.
    pin_wheel_timestamps(built, FIXED_MTIME)

    # verify internal .dist-info/METADATA name matches the filename-derived
    # canonical name (Pyodide rejects a mismatch as UnsupportedWheel).
    verify_wheel_metadata(built, DIST_NAME)

    sha = sha256_file(built)
    dest = os.path.join(wheels_dir, WHEEL_NAME)
    shutil.copyfile(built, dest)
    print("BUILD_WHEEL=%s" % os.path.basename(built))
    print("BUILD_SHA=%s" % sha)
    print("OUTPUT=%s" % dest)
    print("SOURCE_KERI_COMMIT=%s" % UPSTREAM_COMMIT)
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return sha


def verify_wheel_metadata(whl_path, expected_dist_name):
    """Assert a wheel's internal .dist-info/METADATA name matches its filename.

    Pyodide's package loader derives the canonical package name from the wheel
    filename (keri_web-2.0.0.dev6-py3-none-any.whl -> `keri-web`) and rejects a
    wheel whose .dist-info directory does not start with that canonical name.
    This guard turns that silent runtime failure into a build-time error.
    """
    import zipfile

    canonical = expected_dist_name.replace("-", "_")  # keri-web -> keri_web
    dist_infos = [n for n in zipfile.ZipFile(whl_path).namelist() if ".dist-info" in n]
    if not dist_infos:
        raise SystemExit("wheel has no .dist-info members: %s" % whl_path)
    dist_info_dir = dist_infos[0].split(".dist-info")[0]
    if not dist_info_dir.startswith(canonical + "-"):
        raise SystemExit(
            "Wheel .dist-info mismatch: %r does not start with %r-\n"
            "Pyodide would reject this wheel as UnsupportedWheel."
            % (dist_info_dir, canonical)
        )
    print("wheel metadata OK: .dist-info %r matches %r" % (dist_info_dir, canonical))


def pin_wheel_timestamps(whl_path, mtime_epoch):
    """Rewrite a wheel ZIP with every member timestamp pinned to a fixed epoch.

    setuptools/bdist_wheel stamp .dist-info/* members with the wall-clock build
    time, so two otherwise identical builds differ only in those ZIP member
    date_time fields. RECORD hashes are computed over member *content*, which
    this repack does not alter, so RECORD stays valid. Output is deterministic.
    """
    import io
    import zipfile

    fixed = (1980, 1, 1, 0, 0, 0)
    tmp = whl_path + ".detmp"
    with zipfile.ZipFile(whl_path, "r") as zin:
        names = zin.namelist()
        infos = zin.infolist()
        datas = {i.filename: zin.read(i.filename) for i in infos}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            info = next(i for i in infos if i.filename == name)
            zi = zipfile.ZipInfo(name, date_time=fixed)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            zout.writestr(zi, datas[name])
    os.replace(tmp, whl_path)
    # normalize source file mtimes for reproducible local state
    for dp, dn, fn in os.walk(os.path.dirname(whl_path)):
        for name in fn:
            try:
                os.utime(os.path.join(dp, name), (mtime_epoch, mtime_epoch))
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keripy-dir", default=None)
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()
    kd = find_keripy_dir(args.keripy_dir)
    build(kd, args.keep_work)


if __name__ == "__main__":
    main()
