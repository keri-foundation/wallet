"""Test the vault-independent settings boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "runtime" / "vaulting.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fortweb_vaulting_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VaultlessSettingsTest(unittest.TestCase):
    def setUp(self):
        self.vaulting = _load_module()
        self.calls = []

        def fail(name):
            def called(*_args, **_kwargs):
                self.calls.append(name)
                self.fail(f"vaultless settings called {name}")

            return called

        self.vaulting.configure_runtime(
            ensure_runtime_packages=fail("ensure_runtime_packages"),
            load_modules=fail("load_modules"),
            clienter_factory=fail("clienter_factory"),
            storage_opener=fail("storage_opener"),
            wallet_storage_prefix="fortweb-vault-",
            registry_name="fortweb-vault-registry",
            registry_store="vaults.",
            legacy_root_salt="0AAwMTIzNDU2Nzg5YWJjZGVm",
            passcode_kdf_defaults={},
            default_settings={"tempDatastore": False, "keyTier": "low"},
            kf_state_subdb="kfst.",
        )
        self.vaulting.ensure_registry = fail("ensure_registry")
        self.vaulting.load_modules = fail("load_modules")
        self.vaulting._build_vault_state = fail("build_vault_state")
        self.vaulting._STATE = None
        self.vaulting._REGISTRY = None

    def assert_vaultless_defaults(self, params):
        result = asyncio.run(self.vaulting.dispatch("settings.get", params))
        self.assertEqual(
            result,
            {
                "settings": {
                    "tempDatastore": False,
                    "keyTier": "low",
                    "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
                }
            },
        )
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.vaulting._STATE)
        self.assertIsNone(self.vaulting._REGISTRY)

    def test_missing_none_and_blank_vault_id_return_defaults_without_access(self):
        real_require_open_state = self.vaulting.require_open_state
        self.vaulting.require_open_state = self.vaulting.ensure_registry
        try:
            for params in ({}, {"vaultId": None}, {"vaultId": "  "}):
                with self.subTest(params=params):
                    state = self.vaulting._STATE
                    registry = self.vaulting._REGISTRY
                    self.assert_vaultless_defaults(params)
                    self.assertIs(self.vaulting._STATE, state)
                    self.assertIs(self.vaulting._REGISTRY, registry)
        finally:
            self.vaulting.require_open_state = real_require_open_state

    def test_named_closed_vault_returns_locked(self):
        with self.assertRaises(self.vaulting.RuntimeFault) as raised:
            asyncio.run(
                self.vaulting.dispatch("settings.get", {"vaultId": "vault-locked"})
            )

        self.assertEqual(raised.exception.code, "LOCKED")
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.vaulting._STATE)
        self.assertIsNone(self.vaulting._REGISTRY)

    def test_matching_open_vault_returns_settings(self):
        self.vaulting._STATE = {"vault": {"id": "vault-open"}}
        result = asyncio.run(
            self.vaulting.dispatch("settings.get", {"vaultId": "vault-open"})
        )
        self.assertEqual(
            result,
            {
                "settings": {
                    "tempDatastore": False,
                    "keyTier": "low",
                    "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
                }
            },
        )
        self.assertEqual(self.calls, [])


class IdentifierNamespaceTest(unittest.TestCase):
    def test_internal_identifiers_stay_out_of_list_and_detail_after_reopen(self):
        vaulting = _load_module()
        # Reopened habitats do not retain their namespace on the Hab object.
        public = SimpleNamespace(name="kf-onboarding-personal", pre="public")
        internal = SimpleNamespace(name="kf-onboarding-session", pre="internal")
        habitats = {hab.pre: hab for hab in (public, internal)}
        hby = SimpleNamespace(
            prefixes=list(habitats),
            habByPre=habitats.get,
            habByName=lambda name: public if name == public.name else None,
        )
        vaulting._identifier_record = lambda hab: {"aid": hab.pre, "alias": hab.name}

        self.assertEqual(vaulting._list_identifier_records(hby), [
            {"aid": "public", "alias": "kf-onboarding-personal"},
        ])
        self.assertEqual(vaulting._get_identifier_record(hby, "public")["aid"], "public")
        with self.assertRaises(vaulting.RuntimeFault) as raised:
            vaulting._get_identifier_record(hby, "internal")
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        self.assertIs(hby.habByPre("internal"), internal)


if __name__ == "__main__":
    unittest.main()
