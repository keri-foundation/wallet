#!/usr/bin/env python3
"""Independently verify a complete FortWeb Pyodide 314 runtime tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


REPO = Path(__file__).resolve().parent.parent
EXPECTED_RUNTIME = {
    "pyodide": "314.0.5",
    "python": "3.14.2",
    "emscripten": "5.0.3",
    "abi": "pyemscripten_2026_0_wasm32",
}
CORE_FILES = [
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]
DEPENDENCY_EXCLUSIONS = [
    {"owner": "hio", "requirement": "lmdb>=1.7.5"},
    {"owner": "keri", "requirement": "lmdb==2.1.1"},
]
FORBIDDEN_ACTIVE_TOKENS = (
    "0.29.3",
    "cp313-cp313",
    "pyodide_2025_0",
    "hio_web",
    "keri_web",
    "pychloride",
)
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".py", ".toml"}
ALLOWED_APP_SUFFIXES = {
    ".css", ".html", ".ico", ".jpeg", ".jpg", ".js", ".png", ".py", ".svg", ".ttf", ".webp", ".woff", ".woff2"
}
FORBIDDEN_APP_DIRECTORIES = {
    ".git", ".tmp", "artifacts", "build", "dist", "logs", "node_modules", "tmp", "vendor", "wheels"
}
EXPECTED_CONFIG_FILES = {
    "./app/runtime/runtime_packages.py": "./runtime_packages.py",
    "./app/runtime/vaulting.py": "./vaulting.py",
    "./app/runtime/transporting.py": "./transporting.py",
    "./app/runtime/onboarding.py": "./onboarding.py",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()


def safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise RuntimeError(f"{label} must be a nonempty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise RuntimeError(f"unsafe {label}: {value}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RuntimeError(f"control character in {label}: {value}")
    return value


def safe_config_reference(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("./"):
        raise RuntimeError(f"{label} must start with ./")
    safe_relative(value[2:], label)
    return value


def validate_runtime_config(raw: bytes, root: Path) -> dict:
    config = tomllib.loads(raw.decode())
    if set(config) != {"interpreter", "fort_runtime_packages", "files"}:
        raise RuntimeError("packaged PyScript config has unexpected top-level keys")
    if config["interpreter"] != "./vendor/pyodide/314.0.5/pyodide.mjs":
        raise RuntimeError("packaged PyScript interpreter is not exact")
    package_config = config["fort_runtime_packages"]
    if package_config != {
        "manifest": "./runtime-closure.json",
        "sha256": sha256_bytes(read_regular(root / "runtime-closure.json", root)),
    }:
        raise RuntimeError("packaged runtime package declaration is not exact")
    files_config = config["files"]
    if files_config != EXPECTED_CONFIG_FILES:
        raise RuntimeError("packaged PyScript worker file map is not exact")
    safe_config_reference(config["interpreter"], "PyScript interpreter")
    safe_config_reference(package_config["manifest"], "runtime package manifest")
    for source, target in files_config.items():
        safe_config_reference(source, "PyScript worker source")
        safe_config_reference(target, "PyScript worker target")
        source_path = root.joinpath(*PurePosixPath(source[2:]).parts)
        read_regular(source_path, root)
    return config


def read_regular(path: Path, root: Path) -> bytes:
    absolute_root = Path(os.path.abspath(root))
    absolute_path = Path(os.path.abspath(path))
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise RuntimeError(f"path escapes trusted root: {path}") from exc
    root_metadata = absolute_root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise RuntimeError(f"trusted root must be a real directory: {root}")
    if relative == Path("."):
        raise RuntimeError(f"path escapes trusted root: {path}")
    current = absolute_root
    for part in relative.parts:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"symlink is not allowed: {current}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute_path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(f"file must be one unaliased regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = absolute_path.lstat()
    def identity(item: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
    if identity(metadata) != identity(after) or identity(after) != identity(path_after) or len(data) != after.st_size:
        raise RuntimeError(f"file changed while it was read: {path}")
    return data


def inventory(root: Path) -> list[dict[str, object]]:
    rows = []
    for current, directories, files in os.walk(root, followlinks=False):
        directory = Path(current)
        for name in [*directories, *files]:
            path = directory / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(f"symlink is not allowed in runtime: {path.relative_to(root)}")
            if name in files and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
                raise RuntimeError(f"non-regular or aliased runtime file: {path.relative_to(root)}")
        for name in files:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            data = read_regular(path, root)
            rows.append({"path": relative, "bytes": len(data), "sha256": sha256_bytes(data)})
    rows.sort(key=lambda row: row["path"].encode())
    return rows


def aggregate(rows: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row["path"]).encode())
        digest.update(b"\0")
        digest.update(str(row["bytes"]).encode())
        digest.update(b"\0")
        digest.update(str(row["sha256"]).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def source_manifest(source_manifest_path: Path, expected_sha256: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise RuntimeError("an explicit source manifest SHA-256 is required")
    manifest_path = Path(os.path.abspath(source_manifest_path))
    try:
        manifest_path.relative_to(REPO)
    except ValueError as exc:
        raise RuntimeError("source manifest must remain inside the repository") from exc
    raw = read_regular(manifest_path, REPO)
    if sha256_bytes(raw) != expected_sha256:
        raise RuntimeError("source manifest bytes do not match the reviewed wheelhouse manifest")
    return json.loads(raw), manifest_path


def file_index(manifest: dict) -> dict[str, dict]:
    result = {}
    for row in manifest.get("files", []):
        relative = safe_relative(row.get("path"), "source file path")
        if relative in result:
            raise RuntimeError(f"duplicate source file path: {relative}")
        if not isinstance(row.get("bytes"), int) or not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise RuntimeError(f"invalid source file identity: {relative}")
        result[relative] = row
    return result


def project_closure(manifest: dict, manifest_sha256: str) -> tuple[dict, list[tuple[str, str]]]:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or any(runtime.get(key) != value for key, value in EXPECTED_RUNTIME.items()):
        raise RuntimeError("source runtime identity mismatch")
    if runtime.get("core_files") != CORE_FILES:
        raise RuntimeError("source runtime core order mismatch")
    install_order = manifest.get("install_order")
    wheels = manifest.get("wheels")
    if not isinstance(install_order, list) or len(install_order) != 34 or len(set(install_order)) != 34:
        raise RuntimeError("source install order must contain 34 unique wheels")
    if not isinstance(wheels, list) or len(wheels) != 34:
        raise RuntimeError("source manifest must contain 34 wheel rows")
    wheel_by_filename = {}
    normalized_names = set()
    for row in wheels:
        filename = row.get("filename")
        normalized = str(canonicalize_name(str(row.get("name", ""))))
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or normalized != row.get("normalized_name")
            or normalized in normalized_names
        ):
            raise RuntimeError(f"invalid source wheel row: {filename}")
        normalized_names.add(normalized)
        wheel_by_filename[filename] = row
    if set(wheel_by_filename) != set(install_order):
        raise RuntimeError("source install order is not the selected wheel set")

    sources = file_index(manifest)
    files = []
    copies = []
    for filename in CORE_FILES:
        source = f"runtime/{filename}"
        target = f"vendor/pyodide/314.0.5/{filename}"
        row = sources.get(source)
        if row is None:
            raise RuntimeError(f"missing exact source core row: {source}")
        files.append({"path": target, "bytes": row["bytes"], "sha256": row["sha256"]})
        copies.append((source, target))
    projected_wheels = []
    for filename in install_order:
        source = f"wheelhouse/{filename}"
        target = f"wheels/{filename}"
        source_file = sources.get(source)
        wheel = wheel_by_filename[filename]
        if source_file is None or (source_file["bytes"], source_file["sha256"]) != (wheel["bytes"], wheel["sha256"]):
            raise RuntimeError(f"selected wheel does not match exact source path: {source}")
        files.append({"path": target, "bytes": wheel["bytes"], "sha256": wheel["sha256"]})
        copies.append((source, target))
        projected_wheels.append({
            "filename": filename,
            "name": wheel["name"],
            "normalized_name": wheel["normalized_name"],
            "version": wheel["version"],
            "sha256": wheel["sha256"],
            "bytes": wheel["bytes"],
        })
    return {
        "schema": 1,
        "source_manifest_sha256": manifest_sha256,
        "runtime": {**EXPECTED_RUNTIME, "core_files": CORE_FILES},
        "files": files,
        "wheels": projected_wheels,
        "install_order": install_order,
        "dependency_exclusions": DEPENDENCY_EXCLUSIONS,
    }, copies


def source_output_paths() -> tuple[set[str], dict[str, Path]]:
    paths = set()
    copies = {}
    app_root = REPO / "app"
    for path in sorted(app_root.rglob("*")):
        if "__pycache__" in path.relative_to(app_root).parts:
            continue
        if path.is_dir():
            if path.is_symlink() or path.name in FORBIDDEN_APP_DIRECTORIES:
                raise RuntimeError(f"unexpected application directory: {path.relative_to(REPO)}")
            continue
        relative = path.relative_to(REPO).as_posix()
        if path.suffix == ".ts":
            if not path.name.endswith(".d.ts"):
                paths.add(relative.removesuffix(".ts") + ".js")
            continue
        if path.suffix == ".pyc":
            raise RuntimeError(f"Python cache is not an application input: {relative}")
        if path.suffix == ".js" and path.with_suffix(".ts").exists():
            continue
        if path.suffix.lower() not in ALLOWED_APP_SUFFIXES:
            raise RuntimeError(f"forbidden application input file type: {relative}")
        paths.add(relative)
        copies[relative] = path

    pyscript_root = REPO / "vendor" / "pyscript" / "2025.11.2"
    for path in sorted(pyscript_root.rglob("*")):
        if path.is_dir():
            if path.is_symlink():
                raise RuntimeError(f"symlink in PyScript input: {path.relative_to(REPO)}")
            continue
        relative = path.relative_to(REPO).as_posix()
        paths.add(relative)
        copies[relative] = path
    paths.update({"pyscript-ci.toml", "runtime-closure.json"})
    copies["pyscript-ci.toml"] = REPO / "pyscript-ci.toml"
    return paths, copies


def parse_wheel_metadata(path: Path) -> tuple[str, str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(members) != 1:
            raise RuntimeError(f"wheel must contain exactly one METADATA member: {path.name}")
        message = Parser().parsestr(archive.read(members[0]).decode("utf-8"))
    return message["Name"], message["Version"], message.get_all("Requires-Dist", [])


def dependency_report(root: Path, closure: dict) -> tuple[list[dict], list[dict]]:
    selected = {
        row["normalized_name"]: {"name": row["name"], "version": row["version"], "filename": row["filename"]}
        for row in closure["wheels"]
    }
    requirements = {}
    for normalized, row in selected.items():
        name, version, requires = parse_wheel_metadata(root / "wheels" / row["filename"])
        if str(canonicalize_name(name)) != normalized or version != row["version"]:
            raise RuntimeError(f"wheel metadata identity mismatch: {row['filename']}")
        requirements[normalized] = requires

    environment = default_environment()
    environment.update({
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
    edges = []
    exclusions = []
    for owner in sorted(selected):
        for raw in requirements[owner]:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = str(canonicalize_name(requirement.name))
            if dependency == "lmdb":
                exclusions.append({"owner": owner, "requirement": raw})
                continue
            if dependency not in selected:
                raise RuntimeError(f"missing active dependency {owner}: {raw}")
            version = Version(selected[dependency]["version"])
            if requirement.specifier and version not in requirement.specifier:
                raise RuntimeError(f"active dependency version mismatch {owner}: {raw}")
            edges.append({"owner": owner, "requirement": raw, "selected": dependency})
    if exclusions != DEPENDENCY_EXCLUSIONS:
        raise RuntimeError(f"dependency exclusion mismatch: {exclusions}")
    if len(edges) != 43:
        raise RuntimeError(f"expected 43 active dependency edges, found {len(edges)}")
    return edges, exclusions


def validate_runtime_root(root: Path) -> Path:
    original = root if root.is_absolute() else Path.cwd() / root
    original = Path(os.path.abspath(original))
    current = Path(original.anchor)
    for part in original.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"runtime root path contains a symlink: {current}")
    metadata = original.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"runtime root must be a real directory: {original}")
    resolved = original.resolve()
    relative = resolved.relative_to((REPO / "dist").resolve())
    if relative != Path("runtime") and not (
        len(relative.parts) == 2
        and relative.parts[0] == ".runtime-builds"
        and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", relative.parts[1])
    ):
        raise RuntimeError(f"runtime root is outside the reviewed output boundary: {resolved}")
    return resolved


def verify_compiled_outputs(root: Path) -> int:
    """Compare all emitted JavaScript with a fresh compilation of current sources."""
    package_root = REPO / "node_modules" / "typescript"
    package = json.loads((package_root / "package.json").read_text())
    lock = json.loads((REPO / "package-lock.json").read_text())
    if package.get("version") != lock["packages"]["node_modules/typescript"]["version"]:
        raise RuntimeError("installed TypeScript does not match package-lock.json; run npm ci")
    node = os.environ.get("FORTWEB_NODE", "node")
    with tempfile.TemporaryDirectory(prefix="fortweb-typescript-verify-") as temporary:
        compiled = Path(temporary).resolve()
        result = subprocess.run(
            [node, str(package_root / "bin" / "tsc"), "--project", str(REPO / "tsconfig.build.json"),
             "--outDir", str(compiled)],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode:
            raise RuntimeError(f"independent TypeScript compilation failed:\n{result.stdout}")
        rows = inventory(compiled)
        if not rows:
            raise RuntimeError("independent TypeScript compilation emitted no files")
        for row in rows:
            relative = row["path"]
            expected = read_regular(compiled / relative, compiled)
            actual = read_regular(root / relative, root)
            if actual != expected:
                raise RuntimeError(f"compiled runtime file does not match current source: {relative}")
        return len(rows)


def verify_runtime(root: Path, source_manifest_path: Path, manifest_sha256: str | None = None) -> dict:
    manifest_sha256 = manifest_sha256 or os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", "")
    root = validate_runtime_root(root)
    root_info = root.stat()
    manifest, manifest_path = source_manifest(source_manifest_path, manifest_sha256)
    closure, copies = project_closure(manifest, manifest_sha256)
    closure_bytes = canonical_json(closure)
    actual_closure = read_regular(root / "runtime-closure.json", root)
    if actual_closure != closure_bytes:
        raise RuntimeError("runtime-closure.json is not the independent canonical projection")

    rows = inventory(root)
    row_by_path = {row["path"]: row for row in rows}
    expected_paths, source_copies = source_output_paths()
    expected_paths.update(target for _, target in copies)
    if set(row_by_path) != expected_paths:
        raise RuntimeError(
            f"runtime path closure mismatch: missing={sorted(expected_paths - set(row_by_path))}, "
            f"extra={sorted(set(row_by_path) - expected_paths)}"
        )
    if {PurePosixPath(path).parts[0] for path in row_by_path} != {
        "app", "pyscript-ci.toml", "runtime-closure.json", "vendor", "wheels"
    }:
        raise RuntimeError("runtime root contains an unexpected top-level entry")
    compiled_file_count = verify_compiled_outputs(root)

    for relative, source in source_copies.items():
        source_bytes = read_regular(source, REPO)
        output_bytes = read_regular(root.joinpath(*PurePosixPath(relative).parts), root)
        if relative == "pyscript-ci.toml":
            if source_bytes.count(b"__RUNTIME_CLOSURE_SHA256__") != 1:
                raise RuntimeError("source runtime config must contain one digest placeholder")
            source_bytes = source_bytes.replace(b"__RUNTIME_CLOSURE_SHA256__", sha256_bytes(closure_bytes).encode())
        if output_bytes != source_bytes:
            raise RuntimeError(f"copied runtime input changed: {relative}")
    source_root = manifest_path.parent
    for source, target in copies:
        output = row_by_path[target]
        source_row = file_index(manifest)[source]
        if (output["bytes"], output["sha256"]) != (source_row["bytes"], source_row["sha256"]):
            raise RuntimeError(f"runtime artifact changed from reviewed wheelhouse bytes: {target}")
        if read_regular(source_root.joinpath(*PurePosixPath(source).parts), source_root) != read_regular(
            root.joinpath(*PurePosixPath(target).parts), root
        ):
            raise RuntimeError(f"runtime artifact bytes differ from reviewed wheelhouse input: {target}")

    validate_runtime_config(read_regular(root / "pyscript-ci.toml", root), root)

    for relative, row in row_by_path.items():
        if any(token in relative for token in FORBIDDEN_ACTIVE_TOKENS):
            raise RuntimeError(f"stale active runtime path: {relative}")
        path = root.joinpath(*PurePosixPath(relative).parts)
        if path.suffix in TEXT_SUFFIXES and not relative.startswith("vendor/pyscript/"):
            text = read_regular(path, root).decode("utf-8")
            for token in FORBIDDEN_ACTIVE_TOKENS:
                if token in text:
                    raise RuntimeError(f"stale active runtime text {token}: {relative}")
    for forbidden in (
        "manifest.json",
        "checksums.sha256",
        "fortweb-release.json",
        "contracts/runtime-requirements.json",
    ):
        if forbidden in row_by_path:
            raise RuntimeError(f"package contract leaked into runtime tree: {forbidden}")
    if any(relative.endswith(".zip") and relative != "vendor/pyodide/314.0.5/python_stdlib.zip" for relative in row_by_path):
        raise RuntimeError("unexpected ZIP output in Runtime runtime")

    edges, exclusions = dependency_report(root, closure)
    return {
        "root": str(root),
        "target_dev": root_info.st_dev,
        "target_ino": root_info.st_ino,
        "closure_sha256": sha256_bytes(closure_bytes),
        "source_manifest_sha256": manifest_sha256,
        "files": rows,
        "aggregate_sha256": aggregate(rows),
        "file_count": len(rows),
        "compiled_file_count": compiled_file_count,
        "wheel_count": len(closure["wheels"]),
        "dependency_edges": edges,
        "dependency_edge_count": len(edges),
        "dependency_exclusions": exclusions,
    }


def write_output(path: Path | None, data: bytes) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument(
        "--source-manifest",
        required=not bool(os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST")),
        type=Path,
        default=os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST"),
    )
    parser.add_argument("--inventory-output", type=Path)
    parser.add_argument("--digest-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--compare-inventory", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--source-identity-sha256", default="")
    parser.add_argument("--source-manifest-sha256", default=os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", ""))
    args = parser.parse_args()

    verified = verify_runtime(args.runtime_dir, args.source_manifest, args.source_manifest_sha256)
    identity = {}
    if args.run_id:
        identity["run_id"] = args.run_id
    if args.source_identity_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", args.source_identity_sha256):
            raise RuntimeError("source identity must be a lowercase SHA-256")
        identity["source_identity_sha256"] = args.source_identity_sha256
    inventory_payload = {
        "schema": 1,
        **identity,
        "files": verified["files"],
        "aggregate_sha256": verified["aggregate_sha256"],
    }
    inventory_sha256 = ""
    if args.compare_inventory:
        expected_raw = read_regular(args.compare_inventory.resolve(), REPO)
        expected = json.loads(expected_raw)
        if (
            expected.get("files") != inventory_payload["files"]
            or expected.get("aggregate_sha256") != inventory_payload["aggregate_sha256"]
            or expected.get("run_id") != args.run_id
            or (expected.get("source_identity_sha256") or "")
            != args.source_identity_sha256
        ):
            raise RuntimeError("runtime does not match the comparison inventory")
        inventory_sha256 = sha256_bytes(expected_raw)
    report = {
        "schema": 1,
        **identity,
        "ok": True,
        **({"inventory_sha256": inventory_sha256} if inventory_sha256 else {}),
        **{key: value for key, value in verified.items() if key != "files"},
    }
    write_output(args.inventory_output, canonical_json(inventory_payload))
    write_output(args.digest_output, f"{verified['aggregate_sha256']}\n".encode())
    write_output(args.report_output, canonical_json(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
