"""Test independent Runtime config and path validation."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parent.parent
VERIFIER_PATH = REPO / "scripts" / "verify_runtime_tree.py"
RUNTIME = REPO / "dist" / "runtime"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("fortweb_runtime_verifier_test", VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


class RuntimeRuntimeVerifierTest(unittest.TestCase):
    def test_compiled_output_rejects_tampering_and_stale_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source"
            runtime = root / "runtime"
            (source / "app").mkdir(parents=True)
            (runtime / "app").mkdir(parents=True)
            shutil.copytree(REPO / "node_modules/typescript", source / "node_modules/typescript")
            shutil.copy2(REPO / "package-lock.json", source / "package-lock.json")
            (source / "tsconfig.build.json").write_text(json.dumps({
                "compilerOptions": {"target": "ES2022", "module": "ESNext", "rootDir": ".", "types": [], "skipLibCheck": True},
                "include": ["app/*.ts"],
            }))
            original = "export const value = 1;\n"
            (source / "app/example.ts").write_text(original)
            (runtime / "app/example.js").write_text(original)
            with patch.object(verifier, "REPO", source):
                self.assertEqual(verifier.verify_compiled_outputs(runtime), 1)
                (runtime / "app/example.js").write_text("throw new Error('tampered');\n")
                with self.assertRaisesRegex(RuntimeError, "compiled runtime file does not match current source"):
                    verifier.verify_compiled_outputs(runtime)
                (runtime / "app/example.js").write_text(original)
                (source / "app/example.ts").write_text("export const value = 2;\n")
                with self.assertRaisesRegex(RuntimeError, "compiled runtime file does not match current source"):
                    verifier.verify_compiled_outputs(runtime)

    def test_accepts_the_exact_packaged_config(self):
        raw = (RUNTIME / "pyscript-ci.toml").read_bytes()
        config = verifier.validate_runtime_config(raw, RUNTIME)
        self.assertEqual(config["interpreter"], "./vendor/pyodide/314.0.5/pyodide.mjs")

    def test_rejects_external_version_override(self):
        raw = (RUNTIME / "pyscript-ci.toml").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "unexpected top-level keys"):
            verifier.validate_runtime_config(
                b'version = "https://example.test/pyodide.mjs"\n' + raw,
                RUNTIME,
            )

    def test_rejects_changed_worker_file_map(self):
        raw = (RUNTIME / "pyscript-ci.toml").read_text()
        changed = raw.replace(
            '"./app/runtime/onboarding.py" = "./onboarding.py"',
            '"https://example.test/onboarding.py" = "./onboarding.py"',
        ).encode()
        with self.assertRaisesRegex(RuntimeError, "worker file map is not exact"):
            verifier.validate_runtime_config(changed, RUNTIME)

    def test_rejects_raw_empty_and_dot_path_components(self):
        for value in ("wheels//x.whl", "wheels/./x.whl"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    verifier.safe_relative(value, "test path")


if __name__ == "__main__":
    unittest.main()
