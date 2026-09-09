from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.serve_runtime_browser import (
    RuntimeBrowserHandler,
    RuntimeBrowserServer,
    WHEELHOUSE_PUBLIC_ROOT,
    OOBI_AID,
    OOBI_PATH,
    RUNTIME_ROOT,
    TIMEOUT_OOBI_DELAY_SECONDS,
    _oobi_delay_seconds,
    _read_owned_file,
    _write_ready_file,
)

WHEELHOUSE_ROOT = Path(os.environ["FORTWEB_WHEELHOUSE_ROOT"]) if os.environ.get("FORTWEB_WHEELHOUSE_ROOT") else None

@contextlib.contextmanager
def running_server(mode: str):
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        inventory_path = None
        if mode != "wheelhouse":
            rows = []
            aggregate = hashlib.sha256()
            paths = [item for item in RUNTIME_ROOT.rglob("*") if item.is_file()]
            paths.sort(key=lambda item: item.relative_to(RUNTIME_ROOT).as_posix().encode())
            for path in paths:
                body = path.read_bytes()
                relative = path.relative_to(RUNTIME_ROOT).as_posix()
                digest = hashlib.sha256(body).hexdigest()
                rows.append({"path": relative, "bytes": len(body), "sha256": digest})
                aggregate.update(f"{relative}\0{len(body)}\0{digest}\n".encode())
            inventory_path = Path(directory) / "inventory.json"
            inventory_path.write_text(
                json.dumps({"schema": 1, "files": rows, "aggregate_sha256": aggregate.hexdigest()}),
                encoding="utf-8",
            )
        server = RuntimeBrowserServer(
            ("127.0.0.1", 0),
            RuntimeBrowserHandler,
            mode=mode,
            inventory_path=inventory_path,
            wheelhouse_root=WHEELHOUSE_ROOT,
            manifest_sha256=os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", ""),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def request(port: int, path: str, method: str = "GET") -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read(), {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


class RuntimeBrowserServerTest(unittest.TestCase):
    def test_only_exact_timeout_oobi_aliases_are_delayed(self):
        self.assertEqual(
            _oobi_delay_seconds("/oobi?name=webbaser-timeout-stale-test", "application"),
            TIMEOUT_OOBI_DELAY_SECONDS,
        )
        for target, mode in (
            ("/oobi?name=blind-test", "application"),
            ("/oobi?name=webbaser-timeout-short-test", "application"),
            (f"{OOBI_PATH}?name=webbaser-timeout-short-test", "application"),
            ("/oobi?name=webbaser-timeout-short-test", "isolated"),
            ("/oobi?name=webbaser-timeout-short-test&extra=1", "application"),
        ):
            self.assertEqual(_oobi_delay_seconds(target, mode), 0.0)

    def test_isolated_serves_only_runtime(self):
        with running_server("isolated") as port:
            status, body, headers = request(port, "/fortweb/app/index.html")
            self.assertEqual(status, 200)
            self.assertIn(b"<title>FortWeb</title>", body)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(request(port, "/fortweb/runtime-closure.json")[0], 200)
            for blocked in (
                "/_runtime-test/python/run_webbaser_lifecycle.py",
                "/fortweb/app/",
                "/fortweb/.codex/secret",
                "/fortweb/../pyscript-ci.toml",
                "/fortweb/%2e%2e/pyscript-ci.toml",
                "/fortweb/app/runtime/wallet-worker.py?changed=1",
                "/oobi",
            ):
                self.assertEqual(request(port, blocked)[0], 404, blocked)

    def test_application_allows_only_declared_fixtures_and_oobi(self):
        with running_server("application") as port:
            allowed = (
                "/fortweb/app/index.html",
                "/_runtime-test/ci/fixtures/webbaser-lifecycle/index.html",
                "/_runtime-test/ci/fixtures/webbaser-lifecycle/production.html?invalidRuntimeContract=1",
                "/_runtime-test/ci/fixtures/webbaser-lifecycle/production.html?preloadFailure=1",
                "/_runtime-test/python/run_webbaser_lifecycle.py",
                "/fortweb/pyscript-preload-failure.toml",
                "/oobi?name=blind-test",
                f"/oobi/{OOBI_AID}/controller?name=test",
            )
            for path in allowed:
                self.assertEqual(request(port, path)[0], 200, path)
            for blocked in (
                "/ci/fixtures/webbaser-lifecycle/index.html",
                "/_runtime-test/ci/fixtures/webbaser-lifecycle/other.html",
                "/_runtime-test/python/other.py",
                "/fortweb/pyscript-preload-failure.toml?changed=1",
                "/fortweb/python/run_webbaser_lifecycle.py",
                "/oobi/other/controller",
            ):
                self.assertEqual(request(port, blocked)[0], 404, blocked)

    @unittest.skipUnless(WHEELHOUSE_ROOT, "FORTWEB_WHEELHOUSE_ROOT is required")
    def test_wheelhouse_hides_physical_input_path_and_other_roots(self):
        with running_server("wheelhouse") as port:
            for path in (
                "/fortweb/_wheelhouse-test/fixture.html",
                "/fortweb/_wheelhouse-test/worker.mjs",
                f"{WHEELHOUSE_PUBLIC_ROOT}manifest.json",
                f"{WHEELHOUSE_PUBLIC_ROOT}runtime/pyodide.mjs",
            ):
                self.assertEqual(request(port, path)[0], 200, path)
            for blocked in (
                "/fortweb/app/index.html",
                "/fortweb/runtime-closure.json",
                "/fortweb/private-wheelhouse/manifest.json",
                f"{WHEELHOUSE_PUBLIC_ROOT}../manifest.json",
            ):
                self.assertEqual(request(port, blocked)[0], 404, blocked)

    def test_rejects_non_get_methods(self):
        with running_server("isolated") as port:
            for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                self.assertEqual(request(port, "/fortweb/app/index.html", method=method)[0], 405)

    def test_ready_file_is_written_atomically(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            target = Path(directory) / "ready.json"
            self.assertFalse(target.exists())
            payload = {"url": "http://127.0.0.1:1"}
            original_write = os.write
            write_calls = 0

            def short_write(descriptor: int, data: bytes) -> int:
                nonlocal write_calls
                write_calls += 1
                return original_write(descriptor, data[:3])

            with mock.patch("scripts.serve_runtime_browser.os.write", side_effect=short_write):
                _write_ready_file(target, payload)
            self.assertGreater(write_calls, 1)
            self.assertTrue(target.is_file())
            self.assertEqual(json.loads(target.read_text()), payload)
            with self.assertRaises(FileExistsError):
                _write_ready_file(target, {"url": "http://127.0.0.1:2"})

            rows = []
            aggregate = hashlib.sha256()
            paths = [item for item in RUNTIME_ROOT.rglob("*") if item.is_file()]
            paths.sort(
                key=lambda item: item.relative_to(RUNTIME_ROOT).as_posix().encode()
            )
            for path in paths:
                body = path.read_bytes()
                relative = path.relative_to(RUNTIME_ROOT).as_posix()
                digest = hashlib.sha256(body).hexdigest()
                rows.append({"path": relative, "bytes": len(body), "sha256": digest})
                aggregate.update(f"{relative}\0{len(body)}\0{digest}\n".encode())
            inventory = Path(directory) / "inventory.json"
            inventory.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "files": rows,
                        "aggregate_sha256": aggregate.hexdigest(),
                    }
                )
            )
            subprocess_ready = Path(directory) / "subprocess-ready.json"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/serve_runtime_browser.py",
                    "--mode",
                    "isolated",
                    "--port",
                    "0",
                    "--ready-file",
                    str(subprocess_ready),
                    "--inventory",
                    str(inventory),
                ],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(200):
                    if subprocess_ready.is_file():
                        break
                    if process.poll() is not None:
                        self.fail(process.communicate()[1])
                    time.sleep(0.01)
                self.assertTrue(subprocess_ready.is_file())
                process.terminate()
                _stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_rejects_symlinked_root_ancestor(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            parent = Path(directory)
            real_root = parent / "real"
            real_root.mkdir()
            (real_root / "input.txt").write_text("trusted", encoding="utf-8")
            alias = parent / "alias"
            os.symlink(real_root, alias)
            with self.assertRaises(OSError):
                _read_owned_file(alias / "input.txt", alias)


if __name__ == "__main__":
    unittest.main()
