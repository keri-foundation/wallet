"""Test runtime source archive safety checks."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.acquire_runtime_source import (
    AcquisitionError,
    acquire,
    require_https_url,
    require_sha256,
    safe_extract,
)


def _archive(path: Path, member: tarfile.TarInfo, body: bytes = b"") -> None:
    with tarfile.open(path, mode="w:gz") as bundle:
        if member.isfile():
            member.size = len(body)
            bundle.addfile(member, io.BytesIO(body))
        else:
            bundle.addfile(member)


class RuntimeSourceAcquisitionTest(unittest.TestCase):
    def test_safe_extract_accepts_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tar.gz"
            _archive(archive, tarfile.TarInfo("runtime/pyodide.mjs"), b"runtime")
            output = root / "output"
            safe_extract(archive, output)
            self.assertEqual((output / "runtime" / "pyodide.mjs").read_bytes(), b"runtime")

    def test_safe_extract_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, member in enumerate((
                tarfile.TarInfo("../escape"),
                tarfile.TarInfo("/absolute"),
                tarfile.TarInfo("runtime/link"),
            )):
                if index == 2:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../escape"
                archive = root / f"unsafe-{index}.tar.gz"
                _archive(archive, member, b"unsafe")
                with self.subTest(member=member.name), self.assertRaises(AcquisitionError):
                    safe_extract(archive, root / f"output-{index}")

    def test_archive_identity_requires_lowercase_sha256(self):
        self.assertEqual(require_sha256("1" * 64, "archive"), "1" * 64)
        for value in ("", "1" * 63, "A" * 64, "not-a-digest"):
            with self.subTest(value=value), self.assertRaises(AcquisitionError):
                require_sha256(value, "archive")

    def test_source_url_requires_public_https(self):
        self.assertEqual(
            require_https_url("https://example.com/runtime.tar.gz?version=1"),
            "https://example.com/runtime.tar.gz?version=1",
        )
        for value in (
            "http://example.com/runtime.tar.gz",
            "https://user@example.com/runtime.tar.gz",
            "https://example.com/runtime.tar.gz#fragment",
            "/runtime.tar.gz",
        ):
            with self.subTest(value=value), self.assertRaises(AcquisitionError):
                require_https_url(value)

    def test_acquire_rejects_repository_root_output(self):
        with self.assertRaisesRegex(AcquisitionError, "must not replace"):
            acquire("https://example.com/runtime.tar.gz", "1" * 64, Path.cwd())


if __name__ == "__main__":
    unittest.main()
