"""Load and verify FortWeb's configured Pyodide wheel closure."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import re
import sys
import sysconfig
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import js


EXPECTED_RUNTIME = {
    "pyodide": "314.0.5",
    "python": "3.14.2",
    "emscripten": "5.0.3",
    "abi": "pyemscripten_2026_0_wasm32",
}
EXPECTED_CORE_FILES = {
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
}
EXPECTED_MANIFEST_KEYS = {
    "schema",
    "source_manifest_sha256",
    "runtime",
    "files",
    "wheels",
    "install_order",
    "dependency_exclusions",
}
ALLOWED_LMDB_REQUIREMENTS = {
    ("hio", "lmdb>=1.7.5"),
    ("keri", "lmdb==2.1.1"),
}

_TASK = None
_RESULT = None


def _canonicalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _config_dict():
    config = importlib.import_module("polyscript").config.to_py()
    if not isinstance(config, dict):
        raise RuntimeError("PyWorker configuration must be an object")
    return config


def _package_config():
    config = _config_dict()
    package_config = config.get("fort_runtime_packages")
    if not isinstance(package_config, dict):
        raise RuntimeError("fort_runtime_packages configuration is required")

    manifest = package_config.get("manifest")
    digest = package_config.get("sha256")
    if not isinstance(manifest, str) or not manifest or manifest != manifest.strip():
        raise RuntimeError("fort_runtime_packages.manifest is required")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError("fort_runtime_packages.sha256 must be a lowercase SHA-256")
    package_base = package_config.get("package_base")
    if not isinstance(package_base, str) or not package_base or package_base != package_base.strip():
        raise RuntimeError("fort_runtime_packages.package_base is required")
    return manifest, digest, package_base


async def _fetch_bytes(url):
    response = await js.fetch(url)
    if not response.ok:
        raise RuntimeError(f"package manifest fetch failed: HTTP {response.status}")
    buffer = await response.arrayBuffer()
    return bytes(js.Uint8Array.new(buffer).to_py())


def _safe_relative_path(value):
    if not isinstance(value, str) or not value:
        raise RuntimeError("manifest file path must be a nonempty string")
    decoded = unquote(value)
    parsed = urlparse(decoded)
    parts = value.split("/")
    if (
        decoded != value
        or parsed.scheme
        or parsed.netloc
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise RuntimeError(f"unsafe manifest file path: {value}")
    return value


def _safe_config_reference(value):
    if not isinstance(value, str) or not value.startswith("./"):
        raise RuntimeError("package manifest config reference must start with ./")
    _safe_relative_path(value[2:])
    return value


def _resolve_url(reference, base):
    try:
        return str(js.URL.new(reference, base).href)
    except Exception as exc:
        raise RuntimeError(f"unable to resolve runtime package URL: {reference}") from exc


def _validate_package_base(package_base):
    parsed = urlparse(package_base)
    if (
        parsed.scheme not in {"app", "http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/")
        or unquote(parsed.path) != parsed.path
    ):
        raise RuntimeError(f"invalid runtime package base URL: {package_base}")
    return package_base


def _confine_url(url, package_base):
    parsed = urlparse(url)
    base = urlparse(package_base)
    base_path = base.path.rstrip("/") + "/"
    if (
        not parsed.scheme
        or parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or not (parsed.path + "/").startswith(base_path)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or unquote(parsed.path) != parsed.path
    ):
        raise RuntimeError(f"runtime package URL is outside the package base: {url}")
    return url


def _file_index(manifest):
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise RuntimeError("manifest files must be a list")

    by_path = {}
    seen_paths = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            raise RuntimeError("manifest file row must be an object")
        path = _safe_relative_path(row.get("path"))
        if path in seen_paths:
            raise RuntimeError(f"duplicate manifest file path: {path}")
        seen_paths.add(path)
        if re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) is None:
            raise RuntimeError(f"manifest file SHA-256 is invalid for {path}")
        if not isinstance(row.get("bytes"), int) or row["bytes"] < 0:
            raise RuntimeError(f"manifest file byte count is invalid for {path}")
        by_path[path] = row
    return by_path


def _match_file(by_path, path, *, sha256=None, size=None):
    row = by_path.get(path)
    if row is None:
        raise RuntimeError(f"manifest file is missing: {path}")
    if sha256 is not None and row.get("sha256") != sha256:
        raise RuntimeError(f"manifest file SHA-256 mismatch for {path}")
    if size is not None and row.get("bytes") != size:
        raise RuntimeError(f"manifest file byte count mismatch for {path}")
    if re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) is None:
        raise RuntimeError(f"manifest file SHA-256 is invalid for {path}")
    if not isinstance(row.get("bytes"), int) or row["bytes"] < 0:
        raise RuntimeError(f"manifest file byte count is invalid for {path}")
    return row


def _validate_manifest(manifest, manifest_url, manifest_sha256, package_base):
    if (
        not isinstance(manifest, dict)
        or set(manifest) != EXPECTED_MANIFEST_KEYS
        or manifest.get("schema") != 1
    ):
        raise RuntimeError("unsupported package manifest schema")
    source_digest = manifest.get("source_manifest_sha256")
    if not isinstance(source_digest, str) or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None:
        raise RuntimeError("package manifest source identity must be a lowercase SHA-256")
    runtime = manifest.get("runtime")
    if (
        not isinstance(runtime, dict)
        or set(runtime) != {*EXPECTED_RUNTIME, "core_files"}
        or any(runtime.get(key) != value for key, value in EXPECTED_RUNTIME.items())
    ):
        raise RuntimeError(f"runtime identity mismatch: {runtime}")
    core_files = runtime.get("core_files")
    if (
        not isinstance(core_files, list)
        or core_files != [
            "pyodide.mjs",
            "pyodide.asm.mjs",
            "pyodide.asm.wasm",
            "python_stdlib.zip",
            "pyodide-lock.json",
        ]
    ):
        raise RuntimeError(f"runtime core closure mismatch: {core_files}")

    wheels = manifest.get("wheels")
    install_order = manifest.get("install_order")
    if not isinstance(wheels, list) or len(wheels) != 34:
        raise RuntimeError("manifest must contain 34 wheel rows")
    if not isinstance(install_order, list) or len(install_order) != 34:
        raise RuntimeError("manifest install_order must contain 34 entries")

    exclusions = manifest.get("dependency_exclusions")
    if exclusions != [
        {"owner": "hio", "requirement": "lmdb>=1.7.5"},
        {"owner": "keri", "requirement": "lmdb==2.1.1"},
    ]:
        raise RuntimeError(f"package manifest dependency exclusions mismatch: {exclusions}")

    by_path = _file_index(manifest)
    if len(by_path) != 39:
        raise RuntimeError(f"package manifest must contain 39 file rows, found {len(by_path)}")
    wheel_rows = {}
    expected_distributions = {}
    for wheel in wheels:
        if not isinstance(wheel, dict) or set(wheel) != {
            "filename", "name", "normalized_name", "version", "sha256", "bytes"
        }:
            raise RuntimeError("wheel row must be an object")
        filename = wheel.get("filename")
        name = _canonicalize(str(wheel.get("name", "")))
        normalized_name = _canonicalize(str(wheel.get("normalized_name", "")))
        version = wheel.get("version")
        digest = wheel.get("sha256")
        size = wheel.get("bytes")
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        ):
            raise RuntimeError(f"invalid wheel filename: {filename}")
        if not name or name != normalized_name:
            raise RuntimeError(f"wheel name mismatch for {filename}")
        if not isinstance(version, str) or not version:
            raise RuntimeError(f"wheel version is missing for {filename}")
        if re.fullmatch(r"[0-9a-f]{64}", str(digest or "")) is None:
            raise RuntimeError(f"wheel SHA-256 is invalid for {filename}")
        if not isinstance(size, int) or size < 0:
            raise RuntimeError(f"wheel byte count is invalid for {filename}")
        if filename in wheel_rows:
            raise RuntimeError(f"duplicate wheel filename: {filename}")
        if name in expected_distributions:
            raise RuntimeError(f"duplicate wheel distribution: {name}")

        path = _safe_relative_path(f"wheels/{filename}")
        file_row = _match_file(
            by_path,
            path,
            sha256=digest,
            size=size,
        )
        wheel_rows[filename] = {
            "filename": filename,
            "name": name,
            "version": version,
            "sha256": digest,
            "bytes": size,
            "path": path,
            "url": _confine_url(_resolve_url(path, manifest_url), package_base),
        }
        expected_distributions[name] = version

    if len(set(install_order)) != len(install_order) or set(install_order) != set(wheel_rows):
        raise RuntimeError("manifest install_order is not an exact wheel permutation")

    core = {}
    for filename in core_files:
        path = _safe_relative_path(f"vendor/pyodide/314.0.5/{filename}")
        file_row = _match_file(by_path, path)
        core[filename] = {
            "path": path,
            "url": _confine_url(_resolve_url(path, manifest_url), package_base),
            "sha256": file_row["sha256"],
            "bytes": file_row["bytes"],
        }

    expected_paths = [
        *(f"vendor/pyodide/314.0.5/{filename}" for filename in core_files),
        *(f"wheels/{filename}" for filename in install_order),
    ]
    if list(row["path"] for row in manifest["files"]) != expected_paths:
        raise RuntimeError("package manifest file order or closure mismatch")

    return {
        "manifest_url": manifest_url,
        "manifest_sha256": manifest_sha256,
        "runtime": runtime,
        "core": core,
        "wheels": wheel_rows,
        "install_order": list(install_order),
        "expected_distributions": expected_distributions,
    }


async def _load_package(url):
    from pyodide_js import loadPackage

    pending = loadPackage(url)
    if inspect.isawaitable(pending):
        await pending


def _installed_report(expected):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.version import Version

    installed = {}
    for distribution in importlib.metadata.distributions():
        name = _canonicalize(str(distribution.metadata.get("Name", "")))
        if not name:
            raise RuntimeError("installed distribution has no name")
        if name in installed:
            raise RuntimeError(f"duplicate installed distribution: {name}")
        installed[name] = {
            "name": distribution.metadata["Name"],
            "version": distribution.version,
            "requires_python": distribution.metadata.get("Requires-Python", ""),
            "requires": list(distribution.requires or ()),
            "files": sorted(str(path) for path in (distribution.files or ())),
        }

    missing = sorted(set(expected) - set(installed))
    extra = sorted(set(installed) - set(expected))
    wrong = {
        name: {"expected": version, "actual": installed.get(name, {}).get("version")}
        for name, version in expected.items()
        if installed.get(name, {}).get("version") != version
    }
    if missing or extra or wrong:
        raise RuntimeError(
            f"installed distribution mismatch: missing={missing}, extra={extra}, wrong={wrong}"
        )

    marker_environment = default_environment()
    marker_environment.update({
        "python_version": "3.14",
        "python_full_version": "3.14.2",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "os_name": "posix",
        "sys_platform": "emscripten",
        "platform_machine": "wasm32",
        "platform_system": "Emscripten",
        "extra": "",
    })
    dependency_edges = []
    exclusions = []
    for owner in sorted(installed):
        for raw in installed[owner]["requires"]:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                continue
            dependency = _canonicalize(requirement.name)
            if dependency == "lmdb":
                edge = (owner, raw)
                if edge not in ALLOWED_LMDB_REQUIREMENTS:
                    raise RuntimeError(f"unexpected LMDB exclusion: {edge}")
                exclusions.append({"owner": owner, "requirement": raw})
                continue
            if dependency not in installed:
                raise RuntimeError(f"missing dependency edge {owner}: {raw}")
            if requirement.specifier and Version(installed[dependency]["version"]) not in requirement.specifier:
                raise RuntimeError(f"dependency version mismatch {owner}: {raw}")
            dependency_edges.append({"owner": owner, "requirement": raw, "selected": dependency})

    actual_exclusions = {(item["owner"], item["requirement"]) for item in exclusions}
    if actual_exclusions != ALLOWED_LMDB_REQUIREMENTS:
        raise RuntimeError(f"LMDB exclusion set mismatch: {sorted(actual_exclusions)}")
    return installed, dependency_edges, exclusions


async def _load():
    manifest_path, expected_digest, package_base = _package_config()
    _safe_config_reference(manifest_path)
    package_base = _validate_package_base(package_base)
    manifest_url = _confine_url(_resolve_url(manifest_path, package_base), package_base)
    raw = await _fetch_bytes(manifest_url)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"package manifest SHA-256 mismatch: expected {expected_digest}, got {actual_digest}"
        )

    manifest = json.loads(raw.decode("utf-8"))
    result = _validate_manifest(manifest, manifest_url, actual_digest, package_base)
    for filename in result["install_order"]:
        await _load_package(result["wheels"][filename]["url"])

    from packaging.tags import sys_tags

    installed, dependency_edges, exclusions = _installed_report(result["expected_distributions"])
    pyodide = importlib.import_module("pyodide")
    actual_runtime = {
        "pyodide": str(getattr(pyodide, "__version__", "")),
        "python": ".".join(str(value) for value in sys.version_info[:3]),
        "platform": sys.platform,
        "sysconfig_platform": sysconfig.get_platform(),
        "tags": [str(tag) for tag in sys_tags()],
    }
    if actual_runtime["pyodide"] != EXPECTED_RUNTIME["pyodide"]:
        raise RuntimeError(f"loaded Pyodide version mismatch: {actual_runtime}")
    if actual_runtime["python"] != EXPECTED_RUNTIME["python"]:
        raise RuntimeError(f"loaded Python version mismatch: {actual_runtime}")
    if actual_runtime["platform"] != "emscripten":
        raise RuntimeError(f"loaded platform mismatch: {actual_runtime}")
    if actual_runtime["sysconfig_platform"] != "emscripten-5.0.3-wasm32":
        raise RuntimeError(f"loaded sysconfig platform mismatch: {actual_runtime}")
    if "cp314-cp314-pyemscripten_2026_0_wasm32" not in actual_runtime["tags"]:
        raise RuntimeError(f"loaded ABI tag mismatch: {actual_runtime['tags'][:10]}")

    result["actual_runtime"] = actual_runtime
    result["installed_distributions"] = installed
    result["dependency_edges"] = dependency_edges
    result["exclusions"] = exclusions
    return result


async def ensure_runtime_packages():
    """Load the configured closure once and return verified runtime evidence."""
    global _RESULT, _TASK

    if _TASK is None:
        _TASK = asyncio.ensure_future(_load())
    task = _TASK
    try:
        result = await task
    except Exception:
        if _TASK is task:
            _TASK = None
            _RESULT = None
        raise
    _RESULT = result
    return _RESULT


def runtime_package_result():
    if _RESULT is None:
        raise RuntimeError("runtime packages are not loaded")
    return _RESULT


def runtime_evidence(module_names):
    """Return compact evidence from the verified installed runtime."""
    result = runtime_package_result()
    module_paths = {}
    for name in module_names:
        module = importlib.import_module(name)
        path = str(getattr(module, "__file__", "") or "")
        if "/site-packages/" not in path:
            raise RuntimeError(f"module did not load from site-packages: {name}={path}")
        module_paths[name] = path

    forbidden = sorted(
        name
        for name in sys.modules
        if name in {"lmdb", "falcon", "keri.app.httping"} or name.startswith("hio.core.http")
    )
    if forbidden:
        raise RuntimeError(f"forbidden browser imports are loaded: {forbidden}")

    installed = {
        name: {
            "name": row["name"],
            "version": row["version"],
            "requires_python": row["requires_python"],
            "requires": row["requires"],
        }
        for name, row in result["installed_distributions"].items()
    }
    evidence = {
        "manifest_url": result["manifest_url"],
        "manifest_sha256": result["manifest_sha256"],
        "runtime": result["runtime"],
        "actual_runtime": result["actual_runtime"],
        "core": result["core"],
        "install_order": result["install_order"],
        "wheels": result["wheels"],
        "installed_distributions": installed,
        "dependency_edges": result["dependency_edges"],
        "exclusions": result["exclusions"],
        "module_paths": module_paths,
        "forbidden_imports": forbidden,
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    evidence["closure_sha256"] = hashlib.sha256(encoded).hexdigest()
    return evidence
