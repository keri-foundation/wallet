#!/usr/bin/env python3
"""Acquire and verify the reviewed runtime source archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parent.parent
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256


class AcquisitionError(RuntimeError):
    """A runtime source acquisition contract failed."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: str, label: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise AcquisitionError(f"{label} must be a lowercase SHA-256 digest")
    return value


def require_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise AcquisitionError("runtime source URL must be a public HTTPS URL without credentials or a fragment")
    return value


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with tarfile.open(archive, mode="r:*") as bundle:
        members = bundle.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise AcquisitionError("runtime source archive contains too many members")
        seen: set[str] = set()
        total_bytes = 0
        for member in members:
            raw_name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
            raw_parts = raw_name.split("/")
            relative = PurePosixPath(raw_name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in raw_parts)
                or "\\" in member.name
                or not (member.isdir() or member.isreg())
            ):
                raise AcquisitionError(f"runtime source archive contains an unsafe member: {member.name}")
            canonical = "/".join(raw_parts)
            if canonical in seen:
                raise AcquisitionError(f"runtime source archive repeats a member: {member.name}")
            seen.add(canonical)
            total_bytes += member.size
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise AcquisitionError("runtime source archive expands beyond its size limit")
            target = (root / Path(*relative.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise AcquisitionError(
                    f"runtime source archive member escapes its root: {member.name}"
                ) from error
        bundle.extractall(root, members=members, filter="data")


def verify_source_tree(root: Path, manifest_sha256: str) -> Path:
    require_sha256(manifest_sha256, "runtime source manifest identity")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise AcquisitionError("runtime source archive is missing manifest.json")
    if sha256(manifest_path) != manifest_sha256:
        raise AcquisitionError("runtime source manifest SHA-256 mismatch")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise AcquisitionError("runtime source manifest schema is unsupported")
    runtime = manifest.get("runtime")
    wheels = manifest.get("wheels")
    rows = manifest.get("files")
    if not isinstance(runtime, dict) or not isinstance(wheels, list) or not isinstance(rows, list):
        raise AcquisitionError("runtime source manifest structure is invalid")
    core_files = runtime.get("core_files")
    if not isinstance(core_files, list) or len(core_files) != 5 or len(wheels) != 34:
        raise AcquisitionError("runtime source manifest closure is invalid")

    file_rows = {row.get("path"): row for row in rows if isinstance(row, dict)}
    required_paths = [f"runtime/{name}" for name in core_files]
    required_paths.extend(f"wheelhouse/{row.get('filename')}" for row in wheels)
    if len(file_rows) != len(rows) or len(set(required_paths)) != len(required_paths):
        raise AcquisitionError("runtime source manifest contains duplicate paths")
    for relative in required_paths:
        if ("\\" in relative or "%" in relative or relative.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise AcquisitionError(f"unsafe runtime source path: {relative}")
        row = file_rows.get(relative)
        target = root / relative
        if (
            not isinstance(row, dict)
            or not target.is_file()
            or target.is_symlink()
            or not isinstance(row.get("bytes"), int)
            or row["bytes"] < 0
            or SHA256_PATTERN.fullmatch(str(row.get("sha256", ""))) is None
            or target.stat().st_size != row["bytes"]
            or sha256(target) != row["sha256"]
        ):
            raise AcquisitionError(f"runtime source artifact does not match its manifest: {relative}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    expected_paths = {"manifest.json", *required_paths}
    if actual_paths != expected_paths:
        raise AcquisitionError("runtime source archive contains an unexpected file set")
    return manifest_path


def acquire(url: str, archive_sha256: str, output: Path, manifest_sha256: str | None = None) -> Path:
    require_https_url(url)
    expected_archive_sha256 = require_sha256(archive_sha256, "runtime source archive identity")
    output = output.resolve()
    if output == REPO.resolve():
        raise AcquisitionError("runtime source output must not replace the repository root")
    try:
        output.relative_to(REPO.resolve())
    except ValueError as error:
        raise AcquisitionError("runtime source output must remain inside the repository") from error
    if output.exists():
        raise AcquisitionError(f"runtime source output already exists: {output}")
    manifest_sha256 = require_sha256(manifest_sha256 or os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", ""), "runtime source manifest identity")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".runtime-source-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        archive = temporary_root / "runtime-source.tar.gz"
        request = urllib.request.Request(url, headers={"User-Agent": "fortweb-runtime-source/1"})
        with urllib.request.urlopen(request) as response, archive.open("xb") as stream:
            require_https_url(response.geturl())
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_ARCHIVE_BYTES:
                    raise AcquisitionError("runtime source archive exceeds its download size limit")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if sha256(archive) != expected_archive_sha256:
            raise AcquisitionError("runtime source archive SHA-256 mismatch")

        extracted = temporary_root / "payload"
        safe_extract(archive, extracted)
        manifest_path = verify_source_tree(extracted, manifest_sha256)
        os.rename(extracted, output)
        return output / manifest_path.relative_to(extracted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-sha256", default=os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", ""))
    args = parser.parse_args()
    try:
        manifest = acquire(args.url, args.sha256, args.output, args.manifest_sha256)
    except (AcquisitionError, OSError, tarfile.TarError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(manifest.relative_to(REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
