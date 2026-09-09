"""Test the production runtime package URL and manifest boundary."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import urljoin


REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "runtime" / "runtime_packages.py"
CLOSURE_PATH = REPO / "dist" / "runtime" / "runtime-closure.json"


class _URLValue:
    def __init__(self, reference, base):
        self.href = urljoin(str(base), str(reference))


class _URL:
    @staticmethod
    def new(reference, base):
        return _URLValue(reference, base)


def _load_module():
    fake_js = types.SimpleNamespace(URL=_URL)
    previous = sys.modules.get("js")
    sys.modules["js"] = fake_js
    try:
        spec = importlib.util.spec_from_file_location("fortweb_runtime_packages_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("js", None)
        else:
            sys.modules["js"] = previous


runtime_packages = _load_module()


class RuntimePackageUrlTest(unittest.TestCase):
    def test_manifest_source_identity_follows_the_authenticated_closure(self):
        manifest = json.loads(CLOSURE_PATH.read_text())
        base = "https://appassets.androidplatform.net/runtime/"
        manifest["source_manifest_sha256"] = "a" * 64
        runtime_packages._validate_manifest(manifest, base + "runtime-closure.json", "0" * 64, base)
        for value in (None, 1, int("1" * 64), "A" * 64, "a" * 63):
            with self.subTest(value=value):
                manifest["source_manifest_sha256"] = value
                with self.assertRaisesRegex(RuntimeError, "source identity"):
                    runtime_packages._validate_manifest(manifest, base + "runtime-closure.json", "0" * 64, base)

    def test_modified_closure_is_rejected_before_loading_packages(self):
        module = _load_module()
        raw = CLOSURE_PATH.read_bytes()
        module._package_config = lambda: (
            "./runtime-closure.json", hashlib.sha256(raw).hexdigest(),
            "https://appassets.androidplatform.net/runtime/",
        )

        async def fetch(_url):
            return raw + b" "

        async def load(_url):
            self.fail("Unverified closure must not load packages")

        module._fetch_bytes = fetch
        module._load_package = load
        with self.assertRaisesRegex(RuntimeError, "manifest SHA-256 mismatch"):
            asyncio.run(module._load())

    def test_accepts_supported_package_bases(self):
        for value in (
            "http://127.0.0.1:4173/fortweb/",
            "https://appassets.androidplatform.net/",
            "app://local/",
            "http://127.0.0.1:43123/2f840f/runtime/",
        ):
            with self.subTest(value=value):
                self.assertEqual(runtime_packages._validate_package_base(value), value)

    def test_rejects_invalid_package_bases_before_fetch(self):
        for value in (
            "ftp://example.test/runtime/",
            "https://user:secret@example.test/runtime/",
            "https://example.test/runtime?debug=1",
            "https://example.test/runtime/#debug",
            "https://example.test/runtime",
            "https://example.test/runtime/%2e%2e/escape/",
            "file:///tmp/runtime/",
        ):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    runtime_packages._validate_package_base(value)

    def test_confines_runtime_urls_to_the_package_base(self):
        base = "https://appassets.androidplatform.net/runtime/"
        self.assertEqual(
            runtime_packages._confine_url(
                "https://appassets.androidplatform.net/runtime/wheels/example.whl",
                base,
            ),
            "https://appassets.androidplatform.net/runtime/wheels/example.whl",
        )
        for value in (
            "https://appassets.androidplatform.net/sibling/example.whl",
            "https://other.example/runtime/wheels/example.whl",
            "https://appassets.androidplatform.net/runtime/%2e%2e/example.whl",
            "https://appassets.androidplatform.net/runtime/example.whl?download=1",
            "https://appassets.androidplatform.net/runtime/example.whl#fragment",
            "https://user@appassets.androidplatform.net/runtime/example.whl",
        ):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    runtime_packages._confine_url(value, base)

    def test_rejects_unsafe_relative_paths(self):
        for value in (
            "../escape.whl",
            "/absolute.whl",
            "wheels\\escape.whl",
            "wheels/%2e%2e/escape.whl",
            "wheels//escape.whl",
            "wheels/./escape.whl",
            "wheels/bad\nname.whl",
            "https://example.test/runtime.whl",
        ):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    runtime_packages._safe_relative_path(value)

    def test_config_reference_has_one_required_relative_prefix(self):
        self.assertEqual(
            runtime_packages._safe_config_reference("./runtime-closure.json"),
            "./runtime-closure.json",
        )
        for value in (
            "runtime-closure.json",
            "../runtime-closure.json",
            "./nested//runtime-closure.json",
            "./nested/./runtime-closure.json",
        ):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    runtime_packages._safe_config_reference(value)

    def test_manifest_rejects_control_characters_in_wheel_names(self):
        manifest = json.loads(CLOSURE_PATH.read_text())
        manifest["wheels"][0]["filename"] = "bad\nname.whl"
        with self.assertRaisesRegex(RuntimeError, "invalid wheel filename"):
            runtime_packages._validate_manifest(
                manifest,
                "https://appassets.androidplatform.net/runtime/runtime-closure.json",
                "0" * 64,
                "https://appassets.androidplatform.net/runtime/",
            )


if __name__ == "__main__":
    unittest.main()
