import tempfile
import unittest
from pathlib import Path

from scripts.serve_local import FortWebRequestHandler, _contained_path


class ContainedPathTest(unittest.TestCase):
    def test_rejects_paths_outside_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fortweb"
            root.mkdir()

            self.assertEqual(
                _contained_path(root, "app/index.html"),
                root.resolve() / "app" / "index.html",
            )
            self.assertIsNone(_contained_path(root, "../outside.txt"))
            self.assertIsNone(_contained_path(root, "app/../../outside.txt"))

            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(_contained_path(root, "linked/secret.txt"))

    def test_handler_rejects_encoded_alias_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fortweb"
            root.mkdir()
            handler = object.__new__(FortWebRequestHandler)
            handler.fortweb_root = root.resolve()
            invalid = str(root.resolve() / ".fortweb-invalid-path")

            self.assertEqual(
                handler.translate_path("/fortweb/%2e%2e/outside.txt"),
                invalid,
            )
            self.assertEqual(
                handler.translate_path("/fortweb/app/%2e%2e/%2e%2e/outside.txt"),
                invalid,
            )
            self.assertEqual(
                handler.translate_path("/fortweb/linked/secret.txt"),
                str(root.resolve() / "linked" / "secret.txt"),
            )

    def test_handler_serves_packaged_runtime_closure_and_wheels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fortweb"
            runtime = root / "dist" / "runtime"
            closure = runtime / "runtime-closure.json"
            wheel = runtime / "wheels" / "example.whl"
            wheel.parent.mkdir(parents=True)
            closure.write_text("{}")
            wheel.write_bytes(b"wheel")

            handler = object.__new__(FortWebRequestHandler)
            handler.fortweb_root = root.resolve()

            self.assertEqual(
                handler.translate_path("/fortweb/runtime-closure.json"),
                str(closure.resolve()),
            )
            self.assertEqual(
                handler.translate_path("/fortweb/wheels/example.whl"),
                str(wheel.resolve()),
            )
            self.assertEqual(
                handler.translate_path("/fortweb/wheels/%2e%2e/outside.whl"),
                str(root.resolve() / ".fortweb-invalid-path"),
            )

            # Explicit artifact serving must not read the source or a sibling tree.
            handler.runtime_dir = runtime.resolve()
            (root / "app").mkdir()
            (root / "app" / "source-only.js").write_text("source")
            (runtime / "app").mkdir()
            (runtime / "app" / "index.html").write_text("artifact")
            (runtime / "app" / "linked.js").symlink_to(root / "app" / "source-only.js")
            self.assertEqual(handler.translate_path("/fortweb/app/"), str((runtime / "app" / "index.html").resolve()))
            self.assertEqual(handler.translate_path("/fortweb/runtime-closure.json"), str(closure.resolve()))
            self.assertEqual(handler.translate_path("/fortweb/wheels/example.whl"), str(wheel.resolve()))
            for route in ("/fortweb/app/source-only.js", "/fortweb/app/linked.js", "/fortweb/scripts/serve_local.py",
                          "/fortweb/app/../runtime-closure.json", "/fortweb/wheels/", "/outside.txt"):
                with self.subTest(route=route):
                    self.assertEqual(handler.translate_path(route), str(root.resolve() / ".fortweb-invalid-path"))


class ProxyTargetTest(unittest.TestCase):
    def _target(self, path):
        handler = object.__new__(FortWebRequestHandler)
        handler.path = path
        return handler._proxy_target_url()

    def test_accepts_explicit_loopback_target(self):
        self.assertEqual(
            self._target("/_fortweb_proxy/http/127.0.0.1:9723/bootstrap/config?region=local"),
            "http://127.0.0.1:9723/bootstrap/config?region=local",
        )

    def test_rejects_non_loopback_or_implicit_port(self):
        with self.assertRaises(ValueError):
            self._target("/_fortweb_proxy/http/example.com:9723/bootstrap/config")
        with self.assertRaises(ValueError):
            self._target("/_fortweb_proxy/http/127.0.0.1/bootstrap/config")


if __name__ == "__main__":
    unittest.main()
