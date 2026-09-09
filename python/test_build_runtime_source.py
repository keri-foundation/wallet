"""Verify immutable source and wheel-container boundaries of the public producer."""

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_runtime_source import archive_runtime_source, build_wheel, canonical, digest, extract_source, read_input


def source_archive(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class RuntimeSourceBuildTest(unittest.TestCase):
    def test_normal_wheel_build_records_and_applies_the_exact_source_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            source = source_archive({
                "setup.py": b"from setuptools import setup, find_packages\nsetup(name='hio', version='0.7.20', packages=find_packages())\n",
                "hio/__init__.py": b"", "hio/base/__init__.py": b"",
                "hio/base/doing.py": b"VALUE = 1\n", "hio/base/webduring.py": b"",
            })
            patch = b"diff --git a/hio/base/doing.py b/hio/base/doing.py\n--- a/hio/base/doing.py\n+++ b/hio/base/doing.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
            (root / "hio.tar").write_bytes(source)
            (root / "change.patch").write_bytes(patch)
            spec = {"distribution": "hio", "repository": "https://github.com/ioflo/hio", "commit": "1" * 40,
                    "archive": "hio.tar", "sha256": digest(source),
                    "patches": [{"path": "change.patch", "sha256": digest(patch)}]}
            row, data = build_wheel(spec, root, root)
            self.assertEqual(row["name"], "hio")
            self.assertEqual(row["origin"]["source_archive_sha256"], digest(source))
            self.assertEqual(row["origin"]["patches"], spec["patches"])
            with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                self.assertEqual(wheel.read("hio/base/doing.py"), b"VALUE = 2\n")
            (root / "change.patch").write_bytes(patch + b"\n")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                read_input(root, "change.patch", digest(patch))

    def test_source_archive_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                for name, data in {"setup.py": b"# source", "src/module.py": b"VALUE = 1\n"}.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                member = tarfile.TarInfo("docs/ref")
                member.type = tarfile.SYMTYPE
                member.linkname = "../src"
                archive.addfile(member)
            extract_source(stream.getvalue(), root / "safe")
            self.assertTrue((root / "safe/docs/ref").is_symlink())
            self.assertEqual((root / "safe/docs/ref/module.py").read_bytes(), b"VALUE = 1\n")
            for index, name in enumerate(("../escape", "/absolute", "link")):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    if name == "link":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "../outside"
                    archive.addfile(member)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    extract_source(stream.getvalue(), root / str(index))

    def test_runtime_source_archive_is_repeatable_and_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            (payload / "runtime").mkdir()
            data = b"runtime"
            (payload / "runtime/core").write_bytes(data)
            (payload / "manifest.json").write_bytes(canonical({"files": [
                {"path": "runtime/core", "bytes": len(data), "sha256": digest(data)},
            ]}))
            archive_runtime_source(payload, root / "one.tar.gz")
            archive_runtime_source(payload, root / "two.tar.gz")
            self.assertEqual((root / "one.tar.gz").read_bytes(), (root / "two.tar.gz").read_bytes())
            with tarfile.open(root / "one.tar.gz") as archive:
                self.assertEqual(archive.getnames(), ["manifest.json", "runtime/core"])
                self.assertEqual(json.load(archive.extractfile("manifest.json"))["files"][0]["sha256"], digest(data))
            (payload / "runtime/core").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                archive_runtime_source(payload, root / "bad.tar.gz")


if __name__ == "__main__":
    unittest.main()
