#!/usr/bin/env python3
"""Build normal HIO/Keripy wheels and compose a digest-verified runtime source.

Compiled wheels and Pyodide core files come from an immutable baseline. Each
normal source wheel is built twice from the same archive and explicit patches.
This command does not publish artifacts or change a source checkout.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/") or "\\" in value
            or "%" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(f"unsafe input path: {value}")
    return value


def read_input(root: Path, relative: str, expected: str, size: int | None = None) -> bytes:
    relative_path(relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"input contains a symlink: {relative}")
    if not current.is_file() or current.stat().st_nlink != 1:
        raise ValueError(f"input must be an unaliased regular file: {relative}")
    data = current.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(data) != expected:
        raise ValueError(f"input SHA-256 mismatch: {relative}")
    if size is not None and len(data) != size:
        raise ValueError(f"input size mismatch: {relative}")
    return data


def read_document(path: Path, expected: str) -> dict:
    return json.loads(read_input(path.parent, path.name, expected))


def extract_source(data: bytes, destination: Path) -> None:
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > 20000 or sum(member.size for member in members) > 256 * 1024 * 1024:
            raise ValueError("source archive exceeds its size limit")
        seen = set()
        for member in members:
            name = member.name.rstrip("/") if member.isdir() else member.name
            relative_path(name)
            if name in seen or not (member.isdir() or member.isfile() or member.issym()):
                raise ValueError(f"duplicate or non-regular source member: {name}")
            if member.issym():
                link = member.linkname
                if (not link or Path(link).is_absolute() or "\\" in link
                        or any(ord(character) < 32 or ord(character) == 127 for character in link)):
                    raise ValueError(f"unsafe source symlink: {name}")
                target = (destination / name).parent / link
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError(f"source symlink escapes its root: {name}")
            seen.add(name)
        try:
            archive.extractall(destination, members=members, filter="data")
        except tarfile.FilterError as error:
            raise ValueError(f"unsafe source archive: {error}") from error
    if not (destination / "setup.py").is_file():
        raise ValueError("source archive must put setup.py at its root")


def inspect_wheel(filename: str, data: bytes, distribution: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate members")
        for name in names:
            relative_path(name)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(metadata_names) != 1 or len(wheel_names) != 1 or len(record_names) != 1:
            raise ValueError("wheel must have one metadata, wheel, and RECORD file")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        wheel = BytesParser().parsebytes(archive.read(wheel_names[0]))
        records = list(csv.reader(io.StringIO(archive.read(record_names[0]).decode())))
        if len(records) != len(names) or {row[0] for row in records} != set(names):
            raise ValueError("wheel RECORD does not cover every member exactly once")
        for name, encoded, size in records:
            if name == record_names[0]:
                if encoded or size:
                    raise ValueError("wheel RECORD self-row must be empty")
                continue
            payload = archive.read(name)
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
            if encoded != expected or size != str(len(payload)):
                raise ValueError(f"wheel RECORD mismatch: {name}")
        required = {
            "hio": {"hio/base/doing.py", "hio/base/webduring.py"},
            "keri": {"keri/app/webkeeping.py", "keri/db/webbasing.py", "keri/db/webdbing.py"},
        }[distribution]
        if not required.issubset(names):
            raise ValueError(f"wheel is missing browser modules: {required - set(names)}")
        if any(name.endswith((".so", ".wasm", ".a", ".dylib")) for name in names):
            raise ValueError("normal HIO/Keripy wheels must not contain native libraries")
    if (metadata["Name"] != distribution or wheel["Root-Is-Purelib"] != "true"
            or wheel.get_all("Tag", []) != ["py3-none-any"]
            or filename != f"{distribution}-{metadata['Version']}-py3-none-any.whl"):
        raise ValueError("normal source wheel metadata or filename is invalid")
    return {
        "filename": filename, "name": distribution, "normalized_name": distribution,
        "version": metadata["Version"], "bytes": len(data), "sha256": digest(data),
        "requires_python": metadata["Requires-Python"], "requires_dist": metadata.get_all("Requires-Dist", []),
        "root_is_purelib": "true", "tags": ["py3-none-any"], "native_members": [],
        "import_name": distribution,
    }


def build_wheel(spec: dict, source_root: Path, scratch: Path) -> tuple[dict, bytes]:
    distribution = spec["distribution"]
    if distribution not in {"hio", "keri"} or not re.fullmatch(r"[0-9a-f]{40}", spec["commit"]):
        raise ValueError("source requires a normal distribution and exact commit")
    archive = read_input(source_root, spec["archive"], spec["sha256"])
    patches = [(row, read_input(source_root, row["path"], row["sha256"])) for row in spec.get("patches", [])]
    wheels = []
    for index in range(2):
        root = scratch / f"{distribution}-{index + 1}"
        extract_source(archive, root)
        patch_environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(scratch.resolve()))
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
            patch_environment.pop(name, None)
        for number, (_, patch) in enumerate(patches):
            patch_path = scratch / f"{distribution}-{index + 1}-{number}.patch"
            patch_path.write_bytes(patch)
            subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=root, env=patch_environment, check=True)
            subprocess.run(["git", "apply", str(patch_path)], cwd=root, env=patch_environment, check=True)
        output = scratch / f"{distribution}-{index + 1}-dist"
        environment = dict(os.environ, SOURCE_DATE_EPOCH="315532800", PYTHONHASHSEED="0",
                           PYTHONDONTWRITEBYTECODE="1", TZ="UTC", LC_ALL="C", PIP_NO_INDEX="1")
        result = subprocess.run([sys.executable, "setup.py", "--quiet", "bdist_wheel", "--dist-dir", str(output)],
                                cwd=root, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"{distribution} wheel build failed:\n{result.stdout}")
        built = list(output.glob("*.whl"))
        if len(built) != 1:
            raise ValueError(f"expected one {distribution} wheel")
        data = built[0].read_bytes()
        wheels.append((inspect_wheel(built[0].name, data, distribution), data))
    if wheels[0] != wheels[1]:
        raise ValueError(f"{distribution} wheel builds are not byte-identical")
    row, data = wheels[0]
    row["origin"] = {
        "kind": "source-archive-build", "repository": spec["repository"], "commit": spec["commit"],
        "source_archive_sha256": spec["sha256"],
        "patches": [{"sha256": item["sha256"], "path": item["path"]} for item, _ in patches],
        "sha256": row["sha256"],
    }
    return row, data


def compose(args: argparse.Namespace) -> Path:
    baseline = args.baseline.resolve()
    manifest = read_document(baseline / "manifest.json", args.baseline_sha256)
    sources = read_document(args.sources, args.sources_sha256)
    provenance = read_document(args.package_inputs, args.package_inputs_sha256)
    if (manifest.get("schema") != 1 or len(manifest.get("wheels", [])) != 34
            or len({row["normalized_name"] for row in manifest["wheels"]}) != 34
            or len(manifest.get("install_order", [])) != 34
            or set(manifest["install_order"]) != {row["filename"] for row in manifest["wheels"]}):
        raise ValueError("baseline must select 34 unique, ordered wheels")
    if sources.get("schema") != 1 or sorted(row["distribution"] for row in sources["sources"]) != ["hio", "keri"]:
        raise ValueError("source declaration must contain exactly HIO and Keripy")
    output = args.output.absolute()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-source-build-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        payload = scratch / "payload"
        payload.mkdir()
        built = {spec["distribution"]: build_wheel(spec, args.sources.parent, scratch) for spec in sources["sources"]}
        selected = {row["normalized_name"]: row for row in manifest["wheels"]}
        files = {row["path"]: row for row in manifest["files"]}
        result = copy.deepcopy(manifest)
        result["files"] = []
        result["wheels"] = []
        result["install_order"] = []
        for core in manifest["runtime"]["core_files"]:
            relative = f"runtime/{core}"
            row = files[relative]
            data = read_input(baseline, relative, row["sha256"], row["bytes"])
            target = payload / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            result["files"].append({"path": relative, "bytes": len(data), "sha256": digest(data)})
        by_filename = {row["filename"]: row for row in selected.values()}
        for filename in manifest["install_order"]:
            original = by_filename[filename]
            name = original["normalized_name"]
            if name in built:
                row, data = built[name]
            else:
                row = original
                source_row = files[f"wheelhouse/{filename}"]
                if (row["sha256"], row["bytes"]) != (source_row["sha256"], source_row["bytes"]):
                    raise ValueError(f"baseline wheel metadata does not match its file row: {filename}")
                data = read_input(baseline, f"wheelhouse/{filename}", row["sha256"], row["bytes"])
            relative = f"wheelhouse/{row['filename']}"
            target = payload / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            result["files"].append({"path": relative, "bytes": len(data), "sha256": digest(data)})
            result["wheels"].append(row)
            result["install_order"].append(row["filename"])
        for name, (row, _) in built.items():
            key = "keripy" if name == "keri" else name
            package = provenance["packages"][key]
            package.update(commit=row["origin"]["commit"], version=row["version"],
                           wheel_filename=row["filename"], wheel_sha256=row["sha256"])
            if name == "keri":
                metadata_patches = [patch for patch in row["origin"]["patches"] if "hio" in Path(patch["path"]).name]
                if len(metadata_patches) != 1:
                    raise ValueError("Keripy build must declare the HIO metadata patch")
                package["metadata_patch_sha256"] = metadata_patches[0]["sha256"]
        for key, package in provenance["packages"].items():
            if "wheel_filename" not in package:
                continue
            matching = next((row for row in result["wheels"] if row["filename"] == package["wheel_filename"]), None)
            if matching is None or matching["sha256"] != package["wheel_sha256"]:
                raise ValueError(f"package provenance does not match selected wheel: {key}")
        result["package_provenance"] = {key: provenance[key] for key in ("baseline", "consumers", "packages", "toolchain")}
        result["source_build"] = {
            "baseline_manifest_sha256": args.baseline_sha256, "sources_sha256": args.sources_sha256,
            "package_inputs_sha256": args.package_inputs_sha256, "source_date_epoch": 315532800,
            "python": sys.version.split()[0],
            "setuptools": importlib.metadata.version("setuptools"), "wheel": importlib.metadata.version("wheel"),
        }
        result["files"].sort(key=lambda row: row["path"].encode())
        (payload / "manifest.json").write_bytes(canonical(result))
        os.rename(payload, output)
    return output / "manifest.json"


def archive_runtime_source(root: Path, output: Path) -> None:
    """Write the exact selected runtime source with deterministic TAR/GZIP metadata."""
    manifest_data = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_data)
    rows = [{"path": "manifest.json", "sha256": digest(manifest_data), "bytes": len(manifest_data)}, *manifest["files"]]
    with output.open("xb") as stream:
        with gzip.GzipFile(fileobj=stream, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for row in sorted(rows, key=lambda item: item["path"].encode()):
                    data = read_input(root, row["path"], row["sha256"], row["bytes"])
                    member = tarfile.TarInfo(row["path"])
                    member.size = len(data)
                    member.mode = 0o644
                    member.mtime = 315532800
                    archive.addfile(member, io.BytesIO(data))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "sources", "package-inputs", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("baseline", "sources", "package-inputs"):
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--archive-output", type=Path)
    args = parser.parse_args()
    manifest = compose(args)
    if args.archive_output:
        archive_runtime_source(manifest.parent, args.archive_output)
    print(json.dumps({"manifest": str(manifest), "sha256": digest(manifest.read_bytes())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
