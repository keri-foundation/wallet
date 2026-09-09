"""Test browser HTTP routing against the local proxy contract."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "app/runtime/transporting.py"


def _load_module():
    name = "fortweb_transporting_test"
    original = {key: sys.modules.get(key) for key in (name, "js", "vaulting")}
    sys.modules["js"] = types.SimpleNamespace()
    sys.modules["vaulting"] = types.SimpleNamespace()
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in original.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class ProxyUrlTest(unittest.TestCase):
    def test_only_explicit_loopback_services_use_development_proxy(self):
        transporting = _load_module()
        transporting._CONFIG.update(
            origin=lambda: "http://127.0.0.1:8040",
            kf_proxy_prefix="/_fortweb_proxy",
        )
        for url in ("https://keri.example/oobi/aid", "http://127.0.0.1:8040/local",
                    "/relative", "", "http://localhost/oobi", "wss://localhost:9723/"):
            with self.subTest(url=url):
                self.assertEqual(transporting.proxy_url(url), url)
        for host in ("127.0.0.1", "localhost", "[::1]"):
            with self.subTest(host=host):
                self.assertEqual(
                    transporting.proxy_url(f"http://{host}:9723/bootstrap/config?region=local"),
                    f"http://127.0.0.1:8040/_fortweb_proxy/http/{host}:9723/bootstrap/config?region=local",
                )


if __name__ == "__main__":
    unittest.main()
