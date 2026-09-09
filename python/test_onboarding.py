"""Test hosted-resource session failure boundaries."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "runtime" / "onboarding.py"


class RuntimeFault(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _load_module():
    module_name = "fortweb_onboarding_test"
    original_modules = {
        name: sys.modules.get(name)
        for name in (module_name, "transporting", "vaulting")
    }
    sys.modules["transporting"] = types.SimpleNamespace()
    sys.modules["vaulting"] = types.SimpleNamespace(RuntimeFault=RuntimeFault)
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _complete_payload(**values):
    payload = {
        "state": "active",
        "witnesses": [{"eid": "BWitness", "name": "Witness"}],
        "watcher": {"eid": "BWatcher", "name": "Watcher"},
    }
    payload.update(values)
    return payload


class SessionResourceFailureTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.onboarding = _load_module()
        self.onboarding._CONFIG["cesr_timeout_ms"] = 1_000
        self.record = self.onboarding.KfVaultState(onboarding_session_id="session-1")
        self.snapshot = {
            "bootstrap": {
                "regionId": "region-1",
                "regionName": "Region One",
                "watcherRequired": True,
            }
        }
        self.option = {"witnessCount": 1}

    async def _await(self, payload):
        return await self.onboarding._await_session_resources(
            object(),
            object(),
            surfaces=object(),
            record=self.record,
            boot_server_aid="BBoot",
            start_payload=payload,
            snapshot=self.snapshot,
            option=self.option,
        )

    async def test_terminal_session_rejects_complete_resource_payload(self):
        cleared = []
        self.onboarding._clear_kf_onboarding_session = (
            lambda _hby, _record, *, delete_auth_hab: cleared.append(delete_auth_hab)
        )

        with self.assertRaisesRegex(RuntimeFault, "cancelled") as raised:
            await self._await(_complete_payload(state="cancelled"))

        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertEqual(cleared, [True])

    async def test_failed_provisioning_rejects_complete_resource_payload(self):
        with self.assertRaisesRegex(RuntimeFault, "allocation failed") as raised:
            await self._await(
                _complete_payload(
                    session_provision_operation={
                        "state": "failed",
                        "last_error": "Hosted allocation failed.",
                    }
                )
            )

        self.assertEqual(raised.exception.code, "CONFLICT")


class KelReplayTest(unittest.TestCase):
    def test_replay_keeps_inception_and_receipts_and_fills_missing_events(self):
        onboarding = _load_module()
        inception = b"0:inception-with-receipts"
        rotation = b"2:rotation-with-receipts"
        onboarding.vaulting.load_modules = lambda: {
            "serdering": types.SimpleNamespace(
                SerderKERI=lambda *, raw: types.SimpleNamespace(
                    sn=int(raw[:1]), ked={"s": raw[:1].decode()}
                )
            )
        }

        def own_event(*, sn):
            if sn != 1:
                self.fail("An available replay message must preserve its attachments.")
            return b"1:missing-event"

        hab = types.SimpleNamespace(
            pre="account",
            kever=types.SimpleNamespace(sn=0),
            db=types.SimpleNamespace(clonePreIter=lambda *, pre: iter([inception])),
            msgOwnEvent=own_event,
        )
        self.assertEqual(list(onboarding._iter_hab_kel_messages(hab)), [inception])

        hab.kever.sn = 2
        hab.db.clonePreIter = lambda *, pre: iter([rotation, inception, inception])
        self.assertEqual(
            list(onboarding._iter_hab_kel_messages(hab)),
            [inception, b"1:missing-event", rotation],
        )


if __name__ == "__main__":
    unittest.main()
