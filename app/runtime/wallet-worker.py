from __future__ import annotations

import asyncio
import base64
import importlib
import json
import re
import traceback
import warnings
from datetime import datetime, timezone
from inspect import isawaitable
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlparse
from uuid import uuid4

import js
try:
    import pyscript
except ModuleNotFoundError:
    pyscript = SimpleNamespace(config={})

try:
    from pyscript import sync
except ModuleNotFoundError:
    sync = SimpleNamespace()

import onboarding
import transporting
import vaulting

NullConfiger = vaulting.NullConfiger
RuntimeFault = vaulting.RuntimeFault
KfVaultState = onboarding.KfVaultState

warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"^keri(\.|$)")
warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"^hio(\.|$)")


WALLET_STORAGE_PREFIX = "fortweb-vault-"
REGISTRY_NAME = "fortweb-vault-registry"
REGISTRY_STORE = "vaults."
LEGACY_ROOT_SALT = "0AAwMTIzNDU2Nzg5YWJjZGVm"
REQUEST_KIND = "fortweb.runtime.request"
RESPONSE_KIND = "fortweb.runtime.response"
PASSCODE_KDF_DEFAULTS = {
    "algorithm": "argon2id13",
    "salt": "NHCtv3Actrddf8jC",
    "opslimit": 2,
    "memlimit": 67_108_864,
    "outlen": 16,
}
ALLOWED_METHODS = {
    "vaults.list",
    "vaults.create",
    "vaults.open",
    "vaults.close",
    "vaults.summary",
    "identifiers.list",
    "identifiers.get",
    "identifiers.create",
    "remotes.list",
    "remotes.get",
    "remotes.resolveOobi",
    "remotes.update",
    "settings.get",
    "kf.bootstrap.get",
    "kf.onboarding.start",
    "kf.account.witnesses.list",
    "kf.account.watchers.list",
    "kf.account.watchers.status",
    "kf.account.watchers.query",
    "kf.services.overview",
}

# --- Test-only OOBI parser gate (never enabled in production config) ---
# The OOBI browser no-network regression drives Parser.parse inside the real
# worker through this reserved method. It is registered ONLY when the PyScript
# runtime config carries `fort_oobi_test: true` (see pyscript-oobi-test.toml).
# Normal production config leaves it rejected by the allowlist below.
TEST_OOBI_METHOD = "__test.oobi.parse"
# Test-only reopen-persistence probe (Phase 8). Also marker-gated so normal
# production config keeps rejecting it.
TEST_P8_METHOD = "__test.oobi.p8"
OOBI_TEST_MARKER_PATHS = ("./oobi-test-marker.txt", "/oobi-test-marker.txt")


# The reserved __test.oobi.parse method is gated DYNAMICALLY at request time.
# Arbitrary TOML keys do NOT reach the worker's python pyscript.config (only
# PyScript-known schema keys are forwarded), so the test-only pyscript-oobi-test
# TOML carries the flag by copying app/oobi-test-marker.txt into the worker
# virtual FS via the honored [files] mapping. Production configs never copy the
# marker, so the method stays rejected there.
def _test_oobi_enabled() -> bool:
    try:
        if bool(_config_dict().get("fort_oobi_test", False)):
            return True
    except Exception:
        pass
    try:
        # [files] copies land in the same directory as the runtime modules
        # (vaulting.py is importable there), so locate that directory and check
        # for the test-only marker next to it.
        import os

        import vaulting as _vaulting_mod

        marker_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(_vaulting_mod.__file__)), "oobi-test-marker.txt"),
            *OOBI_TEST_MARKER_PATHS,
        ]
        for marker_path in marker_candidates:
            try:
                with open(marker_path, "r", encoding="utf-8") as marker_file:
                    if "fort_oobi_test=true" in marker_file.read():
                        return True
            except FileNotFoundError:
                continue
            except Exception:
                continue
    except Exception:
        pass
    return False


EXPECTED_OOBI_FIXTURE_SHA = "48fcc323fc2f7956ecd81d2ced766dc09caecc25c9d79ebdc96bb17642a95453"
OOBI_FIXTURE_WITNESS_AID = "BIWLbdRiC1X2ylzDl-blkqkXKz7LI-1ErzbjokYVlk9Z"
OOBI_FIXTURE_HTTPS_URL = "https://138.68.53.132:5633"
# Deterministic V2 controller-OOBI fixtures the no-network regression can feed the
# real worker. Keyed by SHA256; the handler only accepts fixtures in this registry.
#   witness: https://138.68.53.132:5633 (EID BIWLbdRi...)
#   watcher: https://138.68.53.132:7633 (EID BBYFnq8-...)
OOBI_FIXTURES = {
    EXPECTED_OOBI_FIXTURE_SHA: {
        "label": "witness",
        "aid": OOBI_FIXTURE_WITNESS_AID,
        "url": OOBI_FIXTURE_HTTPS_URL,
    },
    "6565f493cb65f21abc965dc5b8b5f065de5a1169b2f00453183b7633212f2e1a": {
        "label": "watcher",
        "aid": "BBYFnq8-_i2hjGemEsUMdE6M4RAB9Bi3iY7dvfEGKV2O",
        "url": "https://138.68.53.132:7633",
    },
}
# Deterministic genuine-V1 controller-OOBI fixture (KERI10 JSON, produced with
# f4b9 tooling at version=Vrsn_1_0). Fort Web hosted integration is V2-only, so
# this fixture MUST be rejected at the hosted boundary with UNSUPPORTED_KERI_VERSION.
V1_OOBI_FIXTURE_SHA = "6f6a86bb63ce5a15bcb2bced2020ea821cecd29a596a40b711efa38c9e5a0b61"
UNSUPPORTED_KERI_VERSION = "UNSUPPORTED_KERI_VERSION"


def _require_hosted_v2(body: bytes) -> int:
    """V2-only hosted-boundary guard.

    Fort Web hosted KERI integration is V2-only (the f4b9 stack emits CESR V2 and
    explicitly refuses V1 CESR). Before a hosted OOBI body is allowed to touch the
    parser/keystore, read the first KERI event's protocol version with keri's own
    version machinery and reject anything whose major version is not 2.

    Returns the version major on success; raises RuntimeFault(UNSUPPORTED_KERI_VERSION)
    when the stream is not V2. This is bounded and never touches keystore state, so a
    rejected stream can never mark an OOBI resolved or insert trusted loc/role/kever.
    """
    import json

    from keri.core import coring
    from keri.kering import Colds, deversify, sniff

    ims = bytearray(body)
    if not ims:
        raise RuntimeFault(UNSUPPORTED_KERI_VERSION, "Hosted OOBI response was empty.")
    cold = sniff(ims)
    if cold in (Colds.msg,):
        # JSON/CBOR/MGPK message. Fort Web only accepts V2; read the first event's
        # "v" version string via a real JSON decode (not a regex), then deversify.
        stripped = bytes(ims).lstrip()
        if not stripped.startswith(b"{"):
            # Non-JSON serialized message (CBOR/MGPK) is not a supported hosted body.
            raise RuntimeFault(UNSUPPORTED_KERI_VERSION, "Hosted OOBI body is not a supported V2 CESR/JSON stream.")
        try:
            obj, _ = json.JSONDecoder().raw_decode(stripped.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any malformed body is unsupported
            raise RuntimeFault(UNSUPPORTED_KERI_VERSION, "Hosted OOBI body is not valid JSON.") from exc
        vs = obj.get("v") if isinstance(obj, dict) else None
        if not isinstance(vs, str):
            raise RuntimeFault(UNSUPPORTED_KERI_VERSION, "Hosted OOBI body has no KERI version string.")
        try:
            _, pvrsn, _, _, _ = deversify(vs)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeFault(UNSUPPORTED_KERI_VERSION, "Hosted OOBI body has an unreadable KERI version.") from exc
        major = getattr(pvrsn, "major", None)
        if major != 2:
            raise RuntimeFault(UNSUPPORTED_KERI_VERSION, f"Hosted OOBI protocol major={major}; Fort Web is V2-only.")
        return major
    # CESR counter stream: f4b9 emits CESR V2 only, so a CESR body is V2.
    return 2
DEFAULT_KF_BOOT_URL = "http://127.0.0.1:9723"
KF_STATE_KEY = "state"
KF_STATE_SUBDB = "kfst."
KF_PROXY_PREFIX = "/_fortweb_proxy"
# Only loopback/local dev endpoints are routed through the same-origin dev
# proxy. External/public http(s) endpoints are fetched directly by the browser.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
KF_ONBOARDING_AUTH_NAMESPACE = "kf_onboarding"
KF_ONBOARDING_AUTH_ALIAS_PREFIX = "kf-onboarding"
KF_BOOTSTRAP_TIMEOUT_MS = 15_000
KF_ACCOUNT_QUERY_TIMEOUT_MS = 15_000
KF_WITNESS_REGISTRATION_TIMEOUT_MS = 30_000
KF_CESR_TIMEOUT_MS = 30_000
KF_CESR_REPLY_MESSAGE_LIMIT = 16
KF_CESR_REPLY_STEP_LIMIT = 4_096
DEFAULT_SETTINGS = {
    "tempDatastore": False,
    "storageBackend": "Browser IndexedDB via WebBaser and WebKeeper",
    "keyAlgorithm": "salty",
    "keyTier": "low",
    "witnessProfile": "Direct",
}
REMOTE_METADATA_FIELDS = ("alias", "company", "org", "note")
ERROR_MESSAGE_MAX_CHARS = 240
IOS_APP_LOCAL_ORIGIN = "app://local"
IOS_LOOPBACK_HOST = "127.0.0.1"
IOS_LOOPBACK_PREFIX = "_fortios"
IOS_LOOPBACK_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,}$")
SENSITIVE_ERROR_PATTERN = re.compile(
    r"(pass(code|word)?|secret|seed|mnemonic|private[_-]?key|token|api[_-]?key|authorization|cookie|credential)",
    re.IGNORECASE,
)
PYODIDE_PACKAGE_NAMES = [
    "cryptography",
    "jsonschema",
    "multidict",
    "packaging",
    "pyyaml",
    "setuptools",
    "sortedcontainers",
    "typing-extensions",
    "wcwidth",
]
LOCAL_WHEEL_PATHS = [
    "/fortweb/vendor/pyodide/0.29.3/wheels/apispec-6.9.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/cbor2-5.8.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/hjson-3.1.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/http_sfv-0.9.9-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/mnemonic-0.21-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/multicommand-1.0.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/ordered_set-4.1.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/prettytable-3.17.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/pyasn1-0.6.2-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/pyasn1_alt_modules-0.4.7-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/pypng-0.20220715.0-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/qrcode-7.4.2-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/semver-3.0.4-py3-none-any.whl",
    "/fortweb/vendor/pyodide/0.29.3/wheels/wheel-0.45.1-py3-none-any.whl",
    "/fortweb/wheels/blake3-1.0.8-cp313-cp313-pyodide_2025_0_wasm32.whl",
    "/fortweb/wheels/msgpack-1.1.2-py3-none-any.whl",
    "/fortweb/wheels/pychloride-0.7.18.2-py3-none-any.whl",
    "/fortweb/wheels/hio_web-0.7.20-py3-none-any.whl",
    "/fortweb/wheels/keri_web-2.0.0.dev6-py3-none-any.whl",
]


_MODULES = None
_REQUEST_LOCK = asyncio.Lock()
_RUNTIME_PACKAGES_TASK = None  # Shared task so _preload and _dispatch coalesce package loads


def _origin():
    contract_origin = _runtime_contract_text(("documentOrigin",))
    if contract_origin:
        return contract_origin.rstrip("/")

    try:
        return str(js.location.origin or "")
    except Exception:
        return ""


def _is_bundled_app_shell():
    """True for WKWebView app:// (and file:) — no HTTP proxy; skip remote KF fetches that would block the worker."""
    origin = _origin()
    if not origin:
        return False
    scheme = origin.split(":", 1)[0].lower()
    return scheme in {"app", "file"}


def _absolute_url(path: str):
    app_base_url = _runtime_contract_text(("appBaseUrl",))
    if app_base_url and path.startswith("/"):
        return f"{app_base_url.rstrip('/')}{path}"

    origin = _origin()
    if origin and path.startswith("/"):
        return f"{origin}{path}"
    return path


async def _load_package_batch(specs):
    if not specs:
        return

    from pyodide_js import loadPackage

    pending = loadPackage(specs)
    if isawaitable(pending):
        await pending


async def _do_load_runtime_packages():
    await _load_package_batch(PYODIDE_PACKAGE_NAMES)
    await _load_package_batch([_absolute_url(path) for path in LOCAL_WHEEL_PATHS])


async def _ensure_runtime_packages():
    """Load Pyodide wheels once; concurrent callers await the same in-flight task."""
    global _RUNTIME_PACKAGES_TASK

    if _RUNTIME_PACKAGES_TASK is None:
        _RUNTIME_PACKAGES_TASK = asyncio.ensure_future(_do_load_runtime_packages())

    try:
        await _RUNTIME_PACKAGES_TASK
    except Exception:
        _RUNTIME_PACKAGES_TASK = None
        raise


def _load_modules():
    global _MODULES
    if _MODULES is None:
        _MODULES = {
            "habbing": importlib.import_module("keri.app.habbing"),
            "httping": importlib.import_module("keri.app.httping"),
            "webkeeping": importlib.import_module("keri.app.webkeeping"),
            "webbasing": importlib.import_module("keri.db.webbasing"),
            "webdbing": importlib.import_module("keri.db.webdbing"),
            "koming": importlib.import_module("keri.db.koming"),
            "oobiing": importlib.import_module("keri.app.oobiing"),
            "organizing": importlib.import_module("keri.app.organizing"),
            "eventing": importlib.import_module("keri.core.eventing"),
            "parsing": importlib.import_module("keri.core.parsing"),
            "routing": importlib.import_module("keri.core.routing"),
            "recording": importlib.import_module("keri.recording"),
            "serdering": importlib.import_module("keri.core.serdering"),
            "kering": importlib.import_module("keri.kering"),
            "coring": importlib.import_module("keri.core.coring"),
            "exchanging": importlib.import_module("keri.peer.exchanging"),
            "signing": importlib.import_module("keri.core.signing"),
        }
    transporting.install_browser_clienter_proxy(_MODULES["httping"])
    return _MODULES


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _storage_name_for(vault_id: str):
    return f"{WALLET_STORAGE_PREFIX}{vault_id}"


def _generate_root_salt():
    return _load_modules()["signing"].Salter().qb64


def _coerce_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_passcode_kdf(raw_value=None):
    raw_value = raw_value if isinstance(raw_value, dict) else {}
    return {
        "algorithm": str(raw_value.get("algorithm") or PASSCODE_KDF_DEFAULTS["algorithm"]),
        "salt": str(raw_value.get("salt") or PASSCODE_KDF_DEFAULTS["salt"]),
        "opslimit": _coerce_int(raw_value.get("opslimit"), PASSCODE_KDF_DEFAULTS["opslimit"]),
        "memlimit": _coerce_int(raw_value.get("memlimit"), PASSCODE_KDF_DEFAULTS["memlimit"]),
        "outlen": _coerce_int(raw_value.get("outlen"), PASSCODE_KDF_DEFAULTS["outlen"]),
    }


def _vault_record(
    *,
    vault_id: str,
    alias: str,
    encrypted: bool,
    root_salt: str,
    passcode_kdf: dict | None = None,
    created_at: str | None = None,
):
    return {
        "id": vault_id,
        "alias": alias,
        "storageName": _storage_name_for(vault_id),
        "description": "Browser-safe KERI vault backed by IndexedDB and a PyScript worker.",
        "runtimeMode": "pyodide-worker",
        "encrypted": bool(encrypted),
        "otpConfigured": False,
        "createdAt": created_at or _now_iso(),
        "rootSalt": root_salt,
        "passcodeKdf": _normalize_passcode_kdf(passcode_kdf),
    }


def _registry_payload(record, existing=None):
    payload = dict(existing) if isinstance(existing, dict) else {}
    payload.update(
        {
            "id": record["id"],
            "alias": record["alias"],
            "storageName": record["storageName"],
            "description": record["description"],
            "runtimeMode": record["runtimeMode"],
            "encrypted": record["encrypted"],
            "otpConfigured": record["otpConfigured"],
            "createdAt": record["createdAt"],
            "rootSalt": record["rootSalt"],
            "passcodeKdf": record["passcodeKdf"],
        }
    )
    return payload


def _coerce_vault_record(record):
    vault_id = str(record.get("id") or "").strip()
    alias = str(record.get("alias") or "").strip() or vault_id
    encrypted = bool(record.get("encrypted", False))
    created_at = str(record.get("createdAt") or "").strip() or _now_iso()
    root_salt = str(record.get("rootSalt") or "").strip() or LEGACY_ROOT_SALT
    coerced = _vault_record(
        vault_id=vault_id,
        alias=alias,
        encrypted=encrypted,
        root_salt=root_salt,
        passcode_kdf=record.get("passcodeKdf"),
        created_at=created_at,
    )
    coerced["description"] = str(record.get("description") or coerced["description"])
    coerced["runtimeMode"] = str(record.get("runtimeMode") or coerced["runtimeMode"])
    coerced["otpConfigured"] = bool(record.get("otpConfigured", record.get("hasOtp", False)))
    storage_name = str(record.get("storageName") or "").strip()
    if storage_name:
        coerced["storageName"] = storage_name
    return coerced


def _vault_view(record):
    return {
        "id": record["id"],
        "alias": record["alias"],
        "storageName": record["storageName"],
        "description": record["description"],
        "runtimeMode": record["runtimeMode"],
        "encrypted": record["encrypted"],
        "otpConfigured": record["otpConfigured"],
        "createdAt": record["createdAt"],
        "opened": _STATE is not None and _STATE["vault"]["id"] == record["id"],
    }


def _json_bytes(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _json_from_bytes(raw_value):
    if raw_value is None:
        return None
    return json.loads(raw_value.decode("utf-8"))


def _registry_records(registry):
    records = []
    for raw_key, raw_value in registry["store"].items.items():
        raw_record = _json_from_bytes(raw_value)
        if raw_record is None:
            continue
        record = _coerce_vault_record(raw_record)
        if not record["id"]:
            continue
        migrated = _registry_payload(record, existing=raw_record)
        if migrated != raw_record:
            registry["db"].setVal(registry["store"], raw_key, _json_bytes(migrated))
        records.append(record)
    records.sort(key=lambda item: (_sort_key(item["alias"]), item["id"]))
    return records


def _registry_record_by_id(registry, vault_id: str):
    raw_key = vault_id.encode("utf-8")
    raw_value = registry["db"].getVal(registry["store"], raw_key)
    record = _json_from_bytes(raw_value)
    if record is None:
        return None

    coerced = _coerce_vault_record(record)
    migrated = _registry_payload(coerced, existing=record)
    if migrated != record:
        registry["db"].setVal(registry["store"], raw_key, _json_bytes(migrated))
    return coerced


def _registry_record_by_alias(registry, alias: str):
    target = _sort_key(alias)
    for record in _registry_records(registry):
        if _sort_key(record["alias"]) == target:
            return record
    return None


def _set_registry_record(registry, record):
    registry["db"].setVal(
        registry["store"],
        record["id"].encode("utf-8"),
        _json_bytes(_registry_payload(_coerce_vault_record(record))),
    )


async def _ensure_registry():
    global _REGISTRY

    if _REGISTRY is not None:
        return _REGISTRY

    await _ensure_runtime_packages()
    modules = _load_modules()
    started_at = _perf_ms()
    emit_runtime_diagnostic(
        "webdber.open.registry.start",
        registry_name=REGISTRY_NAME,
        registry_store=REGISTRY_STORE,
        **_runtime_contract_summary(),
    )
    try:
        db = await modules["webdbing"].WebDBer.open(name=REGISTRY_NAME, stores=[REGISTRY_STORE])
    except Exception as exc:
        emit_runtime_diagnostic(
            "webdber.open.registry.error",
            level="error",
            registry_name=REGISTRY_NAME,
            registry_store=REGISTRY_STORE,
            duration_ms=_duration_ms(started_at),
            **_diagnostic_error_fields(exc),
        )
        raise
    emit_runtime_diagnostic(
        "webdber.open.registry.end",
        registry_name=REGISTRY_NAME,
        registry_store=REGISTRY_STORE,
        duration_ms=_duration_ms(started_at),
    )
    store = db.env.open_db(REGISTRY_STORE)
    _REGISTRY = {"db": db, "store": store}
    return _REGISTRY


def _format_bran(passcode: str):
    return passcode.replace("-", "")


def _stretch_password_to_bran(password: str, passcode_kdf: dict):
    import pysodium

    fixed_salt = str(passcode_kdf["salt"]).encode("utf-8")
    stretched = pysodium.crypto_pwhash(
        outlen=passcode_kdf["outlen"],
        passwd=password,
        salt=fixed_salt,
        opslimit=passcode_kdf["opslimit"],
        memlimit=passcode_kdf["memlimit"],
        alg=pysodium.crypto_pwhash_ALG_ARGON2ID13,
    )
    return base64.urlsafe_b64encode(stretched).decode("utf-8").rstrip("=")


def _prepare_bran(passcode, passcode_kdf=None):
    password = _format_bran(str(passcode or "").strip())
    if not password:
        return ""
    return _stretch_password_to_bran(password, _normalize_passcode_kdf(passcode_kdf))


def _vault_id_for_alias(alias: str, existing_ids: set[str]):
    base = re.sub(r"[^a-z0-9]+", "-", alias.lower()).strip("-") or "vault"
    candidate = base
    counter = 2
    while candidate in existing_ids:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _perf_ms():
    from js import performance
    return performance.now()


_DIAGNOSTIC_KIND = "fortweb.runtime.diagnostic"


def _config_dict():
    raw_config = getattr(pyscript, "config", {})
    if isinstance(raw_config, dict):
        return raw_config
    try:
        return dict(raw_config)
    except Exception:
        return {}


def _runtime_origin_contract():
    contract = _config_dict().get("fort_runtime_origin")
    return contract if isinstance(contract, dict) else None


def _runtime_contract_text(path: tuple[str, ...], default: str = ""):
    value = _runtime_origin_contract()
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    if isinstance(value, str):
        return value.strip() or default
    return default


def _runtime_contract_bool(path: tuple[str, ...], default=False):
    value = _runtime_origin_contract()
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return value if isinstance(value, bool) else default


def _url_scheme(value: str):
    parsed = urlparse(value)
    return parsed.scheme.lower() if parsed.scheme else ""


def _parse_runtime_contract_url(path: tuple[str, ...]):
    value = _runtime_contract_text(path)
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeFault("BAD_CONFIG", "Runtime origin contract URL was invalid.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeFault("BAD_CONFIG", "Runtime origin contract URL included unsupported components.")
    return parsed


def _runtime_url_origin(parsed):
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeFault("BAD_CONFIG", "Runtime origin contract port was invalid.") from error
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _same_runtime_origin(left, right):
    return _runtime_url_origin(left) == _runtime_url_origin(right)


def _validate_ios_common_runtime_contract():
    if _runtime_contract_bool(("capabilities", "networkAllowed"), True):
        raise RuntimeFault("BAD_CONFIG", "iOS runtime origin contract cannot allow network bootstrap.")
    if not _runtime_contract_bool(("capabilities", "bundledAssetsOnly"), False):
        raise RuntimeFault("BAD_CONFIG", "iOS runtime origin contract must require bundled assets.")
    if _runtime_contract_bool(("capabilities", "httpsLikeAssetOrigin"), True):
        raise RuntimeFault("BAD_CONFIG", "iOS runtime origin contract HTTPS-like asset flag was invalid.")
    if _runtime_contract_bool(("capabilities", "implicitBlobOriginSafe"), True):
        raise RuntimeFault("BAD_CONFIG", "iOS runtime origin contract blob safety flag was invalid.")


def _validate_ios_app_local_runtime_contract():
    if _runtime_contract_text(("documentOrigin",)).rstrip("/") != IOS_APP_LOCAL_ORIGIN:
        raise RuntimeFault("BAD_CONFIG", "iOS app-local runtime origin contract document origin was invalid.")
    if _runtime_contract_text(("appBaseUrl",)).rstrip("/") != IOS_APP_LOCAL_ORIGIN:
        raise RuntimeFault("BAD_CONFIG", "iOS app-local runtime origin contract app base URL was invalid.")
    if _runtime_contract_text(("storage", "originPartition")).rstrip("/") != IOS_APP_LOCAL_ORIGIN:
        raise RuntimeFault("BAD_CONFIG", "iOS app-local runtime origin contract origin partition was invalid.")
    for path in (("entryUrl",), ("workerUrl",), ("configUrl",)):
        if not _runtime_contract_text(path).startswith(f"{IOS_APP_LOCAL_ORIGIN}/"):
            raise RuntimeFault("BAD_CONFIG", "iOS app-local runtime origin contract asset URL was invalid.")
    if not _runtime_contract_bool(("capabilities", "customScheme"), False):
        raise RuntimeFault("BAD_CONFIG", "iOS app-local runtime origin contract custom scheme flag was invalid.")
    _validate_ios_common_runtime_contract()


def _validate_ios_loopback_runtime_contract():
    document_origin_text = _runtime_contract_text(("documentOrigin",))
    if not document_origin_text.startswith(f"http://{IOS_LOOPBACK_HOST}:"):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract host was invalid.")

    document_origin = _parse_runtime_contract_url(("documentOrigin",))
    document_parts = _runtime_url_origin(document_origin)
    if document_parts[0] != "http" or document_parts[1] != IOS_LOOPBACK_HOST or not document_parts[2]:
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract origin was invalid.")
    if document_parts[2] == 0 or document_origin.path not in ("", "/"):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract origin shape was invalid.")

    app_base_url = _parse_runtime_contract_url(("appBaseUrl",))
    origin_partition = _parse_runtime_contract_url(("storage", "originPartition"))
    if not _same_runtime_origin(app_base_url, document_origin):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract app base origin was invalid.")
    if not _same_runtime_origin(origin_partition, document_origin) or origin_partition.path not in ("", "/"):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract origin partition was invalid.")

    app_base_segments = [segment for segment in app_base_url.path.split("/") if segment]
    if len(app_base_segments) != 2 or app_base_segments[0] != IOS_LOOPBACK_PREFIX:
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract path prefix was invalid.")
    nonce = app_base_segments[1]
    if not IOS_LOOPBACK_NONCE_PATTERN.fullmatch(nonce):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract nonce was invalid.")
    nonce_prefix = f"/{IOS_LOOPBACK_PREFIX}/{nonce}/"

    for path in (("entryUrl",), ("workerUrl",), ("configUrl",)):
        parsed = _parse_runtime_contract_url(path)
        if not _same_runtime_origin(parsed, document_origin) or not parsed.path.startswith(nonce_prefix):
            raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract asset URL was invalid.")

    if _runtime_contract_bool(("capabilities", "customScheme"), True):
        raise RuntimeFault("BAD_CONFIG", "iOS loopback runtime origin contract custom scheme flag was invalid.")
    _validate_ios_common_runtime_contract()


def _runtime_contract_summary():
    contract = _runtime_origin_contract()
    if contract is None:
        return {"present": False}

    return {
        "present": True,
        "platform": str(contract.get("platform") or ""),
        "mode": str(contract.get("mode") or ""),
        "document_origin_scheme": _url_scheme(str(contract.get("documentOrigin") or "")),
        "app_base_scheme": _url_scheme(str(contract.get("appBaseUrl") or "")),
        "worker_scheme": _url_scheme(str(contract.get("workerUrl") or "")),
        "config_scheme": _url_scheme(str(contract.get("configUrl") or "")),
        "storage_namespace": _runtime_contract_text(("storage", "storageNamespace")),
    }


def _validate_runtime_origin_contract():
    contract = _runtime_origin_contract()
    if contract is None:
        emit_runtime_diagnostic("runtime_origin_contract_missing", fallback="browser_defaults")
        return

    if contract.get("schema") != "fortweb.runtime-origin.v1" or contract.get("version") != 1:
        raise RuntimeFault("BAD_CONFIG", "Runtime origin contract was invalid.")

    if _runtime_contract_text(("platform",)) == "ios-wkwebview":
        if _runtime_contract_text(("mode",)) != "bundled-offline":
            raise RuntimeFault("BAD_CONFIG", "iOS runtime origin contract mode was invalid.")
        if _runtime_contract_text(("documentOrigin",)).rstrip("/") == IOS_APP_LOCAL_ORIGIN:
            _validate_ios_app_local_runtime_contract()
        else:
            _validate_ios_loopback_runtime_contract()

    emit_runtime_diagnostic("runtime_origin_contract_present", **_runtime_contract_summary())


def emit_runtime_diagnostic(event: str, *, level: str = "info", **fields: object) -> None:
    payload = {"kind": _DIAGNOSTIC_KIND, "event": event, "level": level}
    payload.update(
        {k: v for k, v in fields.items() if v is not None and v != ""}
    )
    try:
        js.self.postMessage(json.dumps(payload))
    except Exception:
        pass


def _duration_ms(started_at):
    return round(_perf_ms() - started_at)


def _safe_error_message(error):
    message = " ".join(str(error).split())
    if SENSITIVE_ERROR_PATTERN.search(message):
        return "[redacted]"
    if len(message) > ERROR_MESSAGE_MAX_CHARS:
        return f"{message[:ERROR_MESSAGE_MAX_CHARS]}..."
    return message


def _diagnostic_error_fields(error):
    return {
        "error_class": error.__class__.__name__,
        "error_message": _safe_error_message(error),
    }


async def _build_vault_state(record, *, bran: str = ""):
    storage_name = record["storageName"]
    t0 = _perf_ms()
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.build.start",
        storage_name=storage_name,
    )
    await _ensure_runtime_packages()
    js.console.log(f"[worker] _ensure_runtime_packages: {_perf_ms() - t0:.0f}ms")
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.packages.ready",
        storage_name=storage_name,
        duration_ms=round(_perf_ms() - t0),
    )

    t1 = _perf_ms()
    modules = _load_modules()
    js.console.log(f"[worker] _load_modules: {_perf_ms() - t1:.0f}ms")
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.modules.ready",
        storage_name=storage_name,
        duration_ms=round(_perf_ms() - t1),
    )

    keeper = modules["webkeeping"].WebKeeper(name=record["storageName"])
    baser = modules["webbasing"].WebBaser(name=record["storageName"])
    if KF_STATE_SUBDB not in baser.SubDbNames:
        baser.SubDbNames = [*baser.SubDbNames, KF_STATE_SUBDB]

    t2 = _perf_ms()
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.keeper.reopen.start",
        storage_name=storage_name,
    )
    try:
        await keeper.reopen()
    except Exception as exc:
        emit_runtime_diagnostic(
            "worker_phase",
            level="error",
            phase="vault_state.keeper.reopen.error",
            storage_name=storage_name,
            duration_ms=_duration_ms(t2),
            **_diagnostic_error_fields(exc),
        )
        raise
    js.console.log(f"[worker] keeper.reopen: {_perf_ms() - t2:.0f}ms")
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.keeper.reopen.end",
        storage_name=storage_name,
        duration_ms=round(_perf_ms() - t2),
    )

    t3 = _perf_ms()
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.baser.reopen.start",
        storage_name=storage_name,
    )
    try:
        await baser.reopen()
    except Exception as exc:
        emit_runtime_diagnostic(
            "worker_phase",
            level="error",
            phase="vault_state.baser.reopen.error",
            storage_name=storage_name,
            duration_ms=_duration_ms(t3),
            **_diagnostic_error_fields(exc),
        )
        raise
    js.console.log(f"[worker] baser.reopen: {_perf_ms() - t3:.0f}ms")
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.baser.reopen.end",
        storage_name=storage_name,
        duration_ms=round(_perf_ms() - t3),
    )

    try:
        t4 = _perf_ms()
        emit_runtime_diagnostic(
            "worker_phase",
            phase="vault_state.habery.start",
            storage_name=storage_name,
        )
        hby = modules["habbing"].Habery(
            name=record["storageName"],
            ks=keeper,
            db=baser,
            cf=NullConfiger(),
            temp=False,
            salt=record["rootSalt"],
            bran=bran or None,
        )
        js.console.log(f"[worker] Habery init: {_perf_ms() - t4:.0f}ms")
        emit_runtime_diagnostic(
            "worker_phase",
            phase="vault_state.habery.end",
            storage_name=storage_name,
            duration_ms=round(_perf_ms() - t4),
        )
    except Exception:
        emit_runtime_diagnostic(
            "worker_phase",
            level="error",
            phase="vault_state.habery.error",
            storage_name=storage_name,
        )
        await baser.aclose(clear=False)
        await keeper.aclose(clear=False)
        raise

    js.console.log(f"[worker] _build_vault_state total: {_perf_ms() - t0:.0f}ms")
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vault_state.build.end",
        storage_name=storage_name,
        duration_ms=round(_perf_ms() - t0),
    )
    return {
        "modules": modules,
        "vault": record,
        "hby": hby,
        "bran": bran,
    }


async def _open_vault_state(record, *, passcode=None, bran: str | None = None):
    global _STATE

    vault_id = record["id"]
    storage_name = record["storageName"]
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vaults.open.start",
        vault_id=vault_id,
        storage_name=storage_name,
        encrypted=bool(record.get("encrypted")),
    )

    if _STATE is not None and _STATE["vault"]["id"] == record["id"]:
        js.console.log("[worker] vaults.open: vault already open (cache hit)")
        emit_runtime_diagnostic(
            "worker_phase",
            phase="vaults.open.cache_hit",
            vault_id=vault_id,
            storage_name=storage_name,
        )
        return _STATE

    emit_runtime_diagnostic(
        "worker_phase",
        phase="vaults.open.close_existing.start",
        vault_id=vault_id,
        storage_name=storage_name,
    )
    await _close_state(clear=False)
    emit_runtime_diagnostic(
        "worker_phase",
        phase="vaults.open.close_existing.end",
        vault_id=vault_id,
        storage_name=storage_name,
    )

    derived_bran = bran or ""
    if record.get("encrypted"):
        if not derived_bran and not str(passcode or "").strip():
            raise RuntimeFault("VALIDATION", "Passcode is required to open this vault.")
        if not derived_bran:
            t_kdf = _perf_ms()
            emit_runtime_diagnostic(
                "worker_phase",
                phase="vaults.open.kdf.start",
                vault_id=vault_id,
                storage_name=storage_name,
            )
            derived_bran = _prepare_bran(passcode, record.get("passcodeKdf"))
            js.console.log(f"[worker] _prepare_bran (Argon2id KDF): {_perf_ms() - t_kdf:.0f}ms")
            emit_runtime_diagnostic(
                "worker_phase",
                phase="vaults.open.kdf.end",
                vault_id=vault_id,
                storage_name=storage_name,
                duration_ms=round(_perf_ms() - t_kdf),
            )

    modules = _load_modules()
    try:
        emit_runtime_diagnostic(
            "worker_phase",
            phase="vaults.open.build_state.start",
            vault_id=vault_id,
            storage_name=storage_name,
        )
        _STATE = await _build_vault_state(record, bran=derived_bran)
        emit_runtime_diagnostic(
            "worker_phase",
            phase="vaults.open.build_state.end",
            vault_id=vault_id,
            storage_name=storage_name,
        )
    except modules["kering"].AuthError as exc:
        raise RuntimeFault("AUTH_FAILED", str(exc) or "Passcode incorrect for this vault.") from exc
    except ValueError as exc:
        raise RuntimeFault("VALIDATION", str(exc)) from exc

    emit_runtime_diagnostic(
        "worker_phase",
        phase="vaults.open.end",
        vault_id=vault_id,
        storage_name=storage_name,
    )
    return _STATE


def _require_open_state(vault_id: str | None = None):
    if _STATE is None:
        raise RuntimeFault("LOCKED", "Open a vault before calling vault operations.")
    if vault_id and _STATE["vault"]["id"] != vault_id:
        raise RuntimeFault("LOCKED", f"Vault '{vault_id}' is not open.")
    return _STATE


async def _close_state(*, clear: bool = False):
    global _STATE
    if _STATE is None:
        return

    try:
        await _STATE["hby"].aclose(clear=clear)
    finally:
        _STATE = None


async def _persist_and_reload():
    state = _require_open_state()
    vault = state["vault"]
    bran = state["bran"]
    await _close_state(clear=False)
    return await _open_vault_state(vault, bran=bran)


def _require_text(value, *, field: str):
    text = str(value or "").strip()
    if not text:
        raise RuntimeFault("VALIDATION", f"{field} is required.")
    return text


def _require_blind_oobi_url(value):
    url = _require_text(value, field="OOBI URL")
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeFault(
            "VALIDATION",
            "Blind OOBI URL must be an absolute http(s) URL.",
        )

    if parsed.path != "/oobi":
        raise RuntimeFault(
            "VALIDATION",
            "Blind OOBI URL must use the blind /oobi route in this slice.",
        )

    if parsed.fragment:
        raise RuntimeFault(
            "VALIDATION",
            "Blind OOBI URL must not include a fragment.",
        )

    return url


def _sort_key(value):
    return (value or "").lower()


def _require_vault_id(params):
    return _require_text(params.get("vaultId"), field="Vault")


def _vault_summary(state):
    hby = state["hby"]
    organizer = state["modules"]["organizing"].Organizer(hby=hby)
    vault = state["vault"]
    remote_count = len(_list_remote_records(hby, organizer))
    return {
        "vault": {
            **_vault_view(vault),
            "opened": True,
            "identifierCount": len(_list_identifier_records(hby)),
            "remoteCount": remote_count,
            "contactCount": remote_count,
            "storageBackend": DEFAULT_SETTINGS["storageBackend"],
            "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
        }
    }


def _kf_state_store(hby):
    return _load_modules()["koming"].Komer(db=hby.db, subkey=KF_STATE_SUBDB, klas=KfVaultState)


def _load_kf_state(hby):
    record = _kf_state_store(hby).get(keys=(KF_STATE_KEY,))
    if record is None:
        return KfVaultState()

    if not record.boot_url:
        record.boot_url = DEFAULT_KF_BOOT_URL
    if record.witness_eids is None:
        record.witness_eids = []
    return record


def _save_kf_state(hby, record):
    if not record.created_at:
        record.created_at = _now_iso()
    if not record.boot_url:
        record.boot_url = DEFAULT_KF_BOOT_URL
    _kf_state_store(hby).pin(keys=(KF_STATE_KEY,), val=record)
    return record


def _has_kf_account(record):
    return bool(record.account_aid or record.account_alias or record.status)


def _kf_state_view(record):
    if not _has_kf_account(record):
        return None

    return {
        "bootUrl": record.boot_url,
        "accountAid": record.account_aid,
        "accountAlias": record.account_alias,
        "status": record.status,
        "createdAt": record.created_at,
        "onboardedAt": record.onboarded_at,
        "witnessProfileCode": record.witness_profile_code,
        "witnessCount": record.witness_count,
        "toad": record.toad,
        "watcherRequired": record.watcher_required,
        "regionId": record.region_id,
        "regionName": record.region_name,
        "bootServerAid": record.boot_server_aid,
        "witnessEids": list(record.witness_eids or []),
        "watcherEid": record.watcher_eid,
        "failureReason": record.failure_reason,
    }


def _normalize_boot_url(value, *, fallback: str = ""):
    text = str(value or "").strip() or str(fallback or "").strip() or DEFAULT_KF_BOOT_URL
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeFault("VALIDATION", "KF boot URL must be an absolute http(s) URL.")
    return text.rstrip("/")


def _proxy_url(url: str):
    if not url:
        return url

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    origin = _origin()
    if not origin:
        return url

    same_origin = urlparse(origin)
    if parsed.scheme == same_origin.scheme and parsed.netloc == same_origin.netloc:
        return url

    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        # External/public endpoint: the browser fetches it directly. Only
        # loopback/local dev endpoints need the same-origin dev proxy.
        return url

    path = parsed.path or "/"
    proxied = f"{origin}{KF_PROXY_PREFIX}/{parsed.scheme}/{parsed.netloc}{path}"
    if parsed.query:
        proxied = f"{proxied}?{parsed.query}"
    return proxied


async def _fetch_response(url: str, *, method: str = "GET", headers: dict | None = None, body=None):
    try:
        request_headers = js.Headers.new()
        for key, value in (headers or {}).items():
            request_headers.set(str(key), str(value))

        options = js.Object.new()
        options.method = method
        options.headers = request_headers
        if body is not None:
            options.body = body

        pending = js.fetch(_proxy_url(url), options)
        return await pending if isawaitable(pending) else pending
    except Exception as exc:
        raise RuntimeFault("NETWORK_ERROR", f"Network request failed for {url}: {exc}") from exc


async def _response_text(response):
    pending = response.text()
    text = await pending if isawaitable(pending) else pending
    return str(text or "")


async def _response_bytes(response):
    pending = response.arrayBuffer()
    buffer = await pending if isawaitable(pending) else pending
    return bytes(js.Uint8Array.new(buffer).to_py())


async def _fetch_json(url: str, *, method: str = "GET", headers: dict | None = None, body=None):
    response = await _fetch_response(url, method=method, headers=headers, body=body)
    raw_text = await _response_text(response)
    if int(response.status) >= 400:
        detail = raw_text.strip() or f"HTTP {response.status}"
        raise RuntimeFault("NETWORK_ERROR", f"{detail} from {url}")

    try:
        data = json.loads(raw_text or "{}")
    except ValueError as exc:
        raise RuntimeFault("BAD_RESPONSE", f"KF service returned malformed JSON for {url}.") from exc

    if not isinstance(data, dict):
        raise RuntimeFault("BAD_RESPONSE", f"KF service returned a non-object JSON payload for {url}.")
    return data


def _bootstrap_urls(boot_url: str):
    normalized = _normalize_boot_url(boot_url)
    base = f"{normalized}/"
    return {
        "bootUrl": normalized,
        "bootstrapUrl": urljoin(base, "/bootstrap/config"),
        "onboardingUrl": urljoin(base, "/onboarding"),
        "accountUrl": urljoin(base, "/account"),
    }


async def _fetch_bootstrap_snapshot(boot_url: str):
    urls = _bootstrap_urls(boot_url)
    body = await _fetch_json(urls["bootstrapUrl"])
    bootstrap = body.get("bootstrap", body) if isinstance(body, dict) else {}
    region = body.get("region", {}) if isinstance(body.get("region"), dict) else {}
    surfaces = body.get("surfaces", {}) if isinstance(body.get("surfaces"), dict) else {}
    onboarding_surface = (
        surfaces.get("onboarding", {}) if isinstance(surfaces.get("onboarding"), dict) else {}
    )
    account_surface = surfaces.get("account", {}) if isinstance(surfaces.get("account"), dict) else {}

    options = []
    for option in bootstrap.get("account_options", []) if isinstance(bootstrap, dict) else []:
        if not isinstance(option, dict):
            continue
        code = str(option.get("code", "") or "")
        if not code:
            continue
        options.append(
            {
                "code": code,
                "witnessCount": int(option.get("witness_count", 0) or 0),
                "toad": int(option.get("toad", 0) or 0),
            }
        )

    return {
        "bootUrl": urls["bootUrl"],
        "connection": {"ok": True},
        "bootstrap": {
            "watcherRequired": bool(bootstrap.get("watcher_required", True)),
            "accountsPerIp": int(bootstrap.get("accounts_per_ip", 0) or 0),
            "aidsPerIp": int(bootstrap.get("aids_per_ip", 0) or 0),
            "regionId": str(region.get("id", "") or ""),
            "regionName": str(region.get("name", "") or ""),
            "accountOptions": options,
        },
        "surfaces": {
            "onboardingUrl": str(onboarding_surface.get("url") or urls["onboardingUrl"]),
            "accountUrl": str(account_surface.get("url") or urls["accountUrl"]),
        },
    }


def _split_cesr_message(ims):
    modules = _load_modules()
    buf = bytearray(ims)
    serder = modules["serdering"].SerderKERI(raw=bytes(buf))
    body = bytes(buf[:serder.size])
    attachment = bytes(buf[serder.size:])
    return body, attachment


def _split_cesr_stream(ims):
    modules = _load_modules()
    serders = []
    buf = bytearray(ims)
    while buf:
        serder = modules["serdering"].SerderKERI(raw=bytes(buf))
        serders.append(serder)
        del buf[:serder.size]
        while buf and buf[0] != 0x7B:
            del buf[:1]
    return serders


async def _post_cesr(url: str, *, body: bytes | bytearray, attachment: bytes | bytearray | None = None, destination: str = ""):
    modules = _load_modules()
    headers = {
        "Content-Type": modules["httping"].CESR_CONTENT_TYPE,
        "Content-Length": str(len(body)),
    }
    if attachment:
        headers[modules["httping"].CESR_ATTACHMENT_HEADER] = bytes(attachment).decode("utf-8")
    if destination:
        headers[modules["httping"].CESR_DESTINATION_HEADER] = destination

    response = await _fetch_response(
        url,
        method="POST",
        headers=headers,
        body=bytes(body).decode("utf-8"),
    )
    raw_bytes = await _response_bytes(response)
    if int(response.status) >= 400:
        detail = raw_bytes.decode("utf-8", errors="ignore").strip() or f"HTTP {response.status}"
        raise RuntimeFault("NETWORK_ERROR", f"{detail} from {url}")
    attachment_header = str(response.headers.get(modules["httping"].CESR_ATTACHMENT_HEADER) or "")
    return raw_bytes, attachment_header


def _parse_cesr_reply(hby, raw_bytes: bytes, attachment_header: str, *, expected_route: str = "", expected_sender: str = ""):
    modules = _load_modules()
    ims = bytearray(raw_bytes or b"")
    if attachment_header:
        ims.extend(attachment_header.encode("utf-8"))
    if not ims:
        raise RuntimeFault("BAD_RESPONSE", "KF service returned an empty authenticated reply.")

    parser = modules["parsing"].Parser(kvy=hby.kvy, rvy=hby.rvy, exc=hby.exc, local=False)
    parser.parse(ims=bytearray(ims))
    hby.kvy.processEscrows()

    serders = _split_cesr_stream(ims)
    if not serders:
        raise RuntimeFault("BAD_RESPONSE", "KF service reply could not be parsed as CESR.")

    last = serders[-1]
    ilk = str(last.ked.get("t", "") or "")
    if ilk != "exn":
        raise RuntimeFault("BAD_RESPONSE", f"KF service reply had unexpected ilk '{ilk}'.")

    route = str(last.ked.get("r", "") or "")
    if expected_route and route != expected_route:
        raise RuntimeFault(
            "BAD_RESPONSE",
            f"KF service reply route '{route}' did not match expected route '{expected_route}'.",
        )

    sender = str(getattr(last, "pre", "") or last.ked.get("i", "") or "")
    if expected_sender and not sender:
        raise RuntimeFault("BAD_RESPONSE", "KF service reply did not include a verifiable sender AID.")
    if expected_sender and sender != expected_sender:
        raise RuntimeFault(
            "BAD_RESPONSE",
            f"KF service reply sender '{sender}' did not match expected boot server '{expected_sender}'.",
        )
    if sender and sender not in hby.kevers:
        raise RuntimeFault(
            "BAD_RESPONSE",
            f"KF service reply sender '{sender}' is not verifiable after parsing the prepended KEL.",
        )

    payload = last.ked.get("a", {})
    if not isinstance(payload, dict):
        payload = {}

    return {
        "ilk": ilk,
        "route": route,
        "sender": sender,
        "said": str(last.said or ""),
        "payload": payload,
    }


async def _send_kf_event(url: str, msg):
    body, attachment = _split_cesr_message(msg)
    await _post_cesr(url, body=body, attachment=attachment)


async def _send_kf_exn(
    hby,
    hab,
    *,
    surface_url: str,
    route: str,
    payload: dict,
    destination: str = "",
    expected_sender: str = "",
):
    modules = _load_modules()
    # f4b9 exposing exchangeOld (V2 default); historical module-level `exchange`
    # was removed. Kept consistent with the live transporting.send_kf_exn path.
    serder = modules["exchanging"].exchangeOld(
        route=route,
        attributes=payload,
        sender=hab.pre,
        receiver=destination or "",
    )
    end = bytearray()
    ims = hab.endorse(serder=serder, last=False)
    attachment = bytearray(ims)
    del attachment[:serder.size]
    if end:
        attachment.extend(end)

    raw_bytes, attachment_header = await _post_cesr(
        surface_url,
        body=serder.raw,
        attachment=attachment,
        destination=destination,
    )
    return _parse_cesr_reply(
        hby,
        raw_bytes,
        attachment_header,
        expected_route=route,
        expected_sender=expected_sender,
    )


def _identifier_record(hab):
    witness_count = len(hab.kever.wits)
    if witness_count == 0:
        witness_summary = "No witnesses"
        status = "Local"
        status_tone = "info"
    elif witness_count == 1:
        witness_summary = "1 witness"
        status = "Witnessed"
        status_tone = "success"
    else:
        witness_summary = f"{witness_count} witnesses"
        status = "Witnessed"
        status_tone = "success"

    return {
        "aid": hab.pre,
        "alias": hab.name,
        "prefix": hab.pre,
        "sequenceNumber": hab.kever.sn,
        "witnessCount": witness_count,
        "witnessSummary": witness_summary,
        "status": status,
        "statusTone": status_tone,
        "kelEvents": hab.kever.sn + 1,
        "lastEventDigest": hab.kever.serder.said,
        "oobi": "",
        "witnesses": [{"alias": wit, "status": "Configured"} for wit in hab.kever.wits],
    }


def _list_identifier_records(hby):
    records = []
    for pre in hby.prefixes:
        hab = hby.habByPre(pre)
        if hab is None:
            continue
        records.append(_identifier_record(hab))

    records.sort(key=lambda item: (_sort_key(item["alias"]), item["aid"]))
    return records


def _get_identifier_record(hby, aid: str):
    hab = hby.habByPre(aid)
    if hab is None:
        raise RuntimeFault("NOT_FOUND", f"Identifier '{aid}' was not found.")
    return _identifier_record(hab)


def _dedupe(values):
    seen = set()
    ordered = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _remote_roles(hby, aid: str, oobi: str):
    roles = []

    if oobi:
        try:
            query_roles = parse_qs(urlparse(oobi).query).get("tag", [])
            roles.extend(str(tag).strip().replace("-", " ").title() for tag in query_roles)
        except Exception:
            pass

    ends = getattr(hby.db, "ends", None)
    if ends is not None:
        try:
            for (cid, role, eid), end in ends.getItemIter():
                if eid != aid or not getattr(end, "allowed", False):
                    continue
                roles.append(str(role).strip().replace("-", " ").title())
        except Exception:
            pass

    return _dedupe(roles)


def _remote_mailboxes(hby, aid: str):
    mailboxes = []
    ends = getattr(hby.db, "ends", None)
    if ends is None:
        return mailboxes

    try:
        for (cid, role, eid), end in ends.getItemIter():
            if eid != aid or not getattr(end, "allowed", False):
                continue
            if str(role).strip().lower() == "mailbox":
                mailboxes.append(cid)
    except Exception:
        return []

    return _dedupe(mailboxes)


def _remote_verification_count(hby, aid: str):
    reps = getattr(hby.db, "reps", None)
    if reps is None:
        return 0

    try:
        return reps.cnt(keys=(aid,))
    except Exception:
        return 0


def _remote_keystate_updated_at(hby, oobi: str):
    if not oobi:
        return ""

    try:
        roobi = hby.db.roobi.get(keys=(oobi,))
    except Exception:
        return ""

    return str(getattr(roobi, "date", "") or "")


def _remote_record(hby, contact):
    aid = contact["id"]
    kever = hby.kevers.get(aid)
    alias = contact.get("alias") or aid[:12]
    oobi = contact.get("oobi", "")
    roles = _remote_roles(hby, aid, oobi)
    transferable = bool(kever.transferable) if kever is not None else False

    return {
        "aid": aid,
        "id": aid,
        "alias": alias,
        "prefix": aid,
        "oobi": oobi,
        "company": contact.get("company", ""),
        "org": contact.get("org", ""),
        "note": contact.get("note", ""),
        "sequenceNumber": kever.sn if kever is not None else None,
        "transferable": transferable,
        "transferability": "Transferable" if transferable else "Non-transferable",
        "roles": roles,
        "rolesLabel": ", ".join(roles) if roles else "No roles",
        "status": "Resolved" if kever is not None else "Stored",
        "statusTone": "success" if kever is not None else "info",
    }


def _is_local_identifier(hby, aid: str):
    return hby.habByPre(aid) is not None


def _has_remote_oobi(contact):
    return bool(str(contact.get("oobi") or "").strip())


def _list_remote_contacts(hby, organizer):
    contacts = []
    for contact in organizer.list():
        aid = str(contact.get("id") or "").strip()
        if not aid or _is_local_identifier(hby, aid) or not _has_remote_oobi(contact):
            continue
        contacts.append(contact)
    return contacts


def _remote_contacts_by_aid(hby, organizer):
    return {contact["id"]: dict(contact) for contact in _list_remote_contacts(hby, organizer)}


def _find_remote_contact_by_oobi(hby, organizer, url: str):
    for contact in _list_remote_contacts(hby, organizer):
        if str(contact.get("oobi") or "").strip() == url:
            return dict(contact)
    return None


def _restore_contact(organizer, aid: str, snapshot):
    if snapshot is None:
        organizer.rem(aid)
        return

    organizer.replace(
        aid,
        {field: value for field, value in snapshot.items() if field != "id"},
    )


def _clear_oobi_tracking(hby, url: str):
    for store_name in ("oobis", "coobi", "eoobi", "roobi"):
        store = getattr(hby.db, store_name, None)
        if store is None:
            continue
        try:
            store.rem(keys=(url,))
        except Exception:
            continue


def _require_remote_contact(
    hby,
    organizer,
    aid: str,
    *,
    require_oobi: bool = False,
    local_code: str = "NOT_FOUND",
    missing_oobi_code: str = "NOT_FOUND",
):
    if _is_local_identifier(hby, aid):
        if local_code == "CONFLICT":
            raise RuntimeFault(
                "CONFLICT",
                "Local identifiers cannot be managed through remotes.",
            )
        raise RuntimeFault("NOT_FOUND", f"Remote identifier '{aid}' was not found.")

    contact = organizer.get(aid)
    if contact is None:
        raise RuntimeFault("NOT_FOUND", f"Remote identifier '{aid}' was not found.")

    if require_oobi and not _has_remote_oobi(contact):
        if missing_oobi_code == "CONFLICT":
            raise RuntimeFault(
                "CONFLICT",
                "Remote identifier metadata can only be edited after blind OOBI connect in this slice.",
            )
        raise RuntimeFault(
            "NOT_FOUND",
            f"Remote identifier '{aid}' was not found.",
        )

    return contact


def _remote_detail_record(hby, organizer, aid: str):
    contact = _require_remote_contact(hby, organizer, aid, require_oobi=True)
    record = _remote_record(hby, contact)
    kever = hby.kevers.get(aid)
    record.update(
        {
            "lastEventDigest": kever.serder.said if kever is not None else "",
            "kelEvents": (kever.sn + 1) if kever is not None else 0,
            "keystateUpdatedAt": _remote_keystate_updated_at(hby, record["oobi"]),
            "mailboxes": _remote_mailboxes(hby, aid),
            "verificationCount": _remote_verification_count(hby, aid),
            "verifications": [],
        }
    )
    return record


def _list_remote_records(hby, organizer):
    records = [_remote_record(hby, contact) for contact in _list_remote_contacts(hby, organizer)]
    records.sort(key=lambda item: (_sort_key(item["alias"]), item["aid"]))
    return records


def _get_remote_record(hby, organizer, aid: str):
    return _remote_detail_record(hby, organizer, aid)


async def _await_resolution(hby, oobiery, url: str):
    oobiing = _load_modules()["oobiing"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0

    while loop.time() < deadline:
        oobiery.processFlows()
        if (roobi := hby.db.roobi.get(keys=(url,))) is not None:
            if roobi.state != oobiing.Result.resolved:
                raise RuntimeFault("RUNTIME_ERROR", f"OOBI resolution failed for {url}.")
            return roobi
        await asyncio.sleep(0.05)

    raise RuntimeFault("TIMEOUT", f"Timed out resolving OOBI '{url}'.")


def _pick_oobi(raw: dict):
    oobis = raw.get("oobis")
    if isinstance(oobis, list):
        for item in oobis:
            if isinstance(item, str) and item:
                return item
    value = raw.get("oobi", "")
    return str(value or "").strip()


def _select_account_option(snapshot: dict, code: str):
    for option in snapshot["bootstrap"]["accountOptions"]:
        if option["code"] == code:
            return option
    return None


def _require_kf_account_hab(hby, record):
    if record.status != "onboarded" or not record.account_aid:
        raise RuntimeFault("CONFLICT", "This vault does not have an onboarded KERI Foundation account yet.")

    hab = hby.habByPre(record.account_aid)
    if hab is None:
        raise RuntimeFault(
            "CONFLICT",
            f"Persisted KF account AID '{record.account_aid}' is missing from the local browser vault.",
        )
    return hab


def _create_or_load_kf_account_hab(hby, record, *, alias: str, requested_account_aid: str, witness_eids: list[str], toad: int):
    existing = None

    if requested_account_aid:
        existing = hby.habByPre(requested_account_aid)
        if existing is None:
            raise RuntimeFault(
                "NOT_FOUND",
                f"Selected local account AID '{requested_account_aid}' is missing from this browser vault.",
            )
    elif record.account_aid:
        existing = hby.habByPre(record.account_aid)
        if existing is None:
            raise RuntimeFault(
                "CONFLICT",
                f"Persisted KF account AID '{record.account_aid}' is missing from this browser vault.",
            )
    else:
        existing = hby.habByName(alias)

    if existing is not None:
        existing_wits = list(getattr(getattr(existing, "kever", None), "wits", []) or [])
        existing_toad = getattr(getattr(getattr(existing, "kever", None), "toader", None), "num", None)
        if not requested_account_aid and not record.account_aid and existing.pre != requested_account_aid:
            raise RuntimeFault(
                "CONFLICT",
                f"Identifier alias '{alias}' is already used by another local identifier in this vault.",
            )
        if existing_wits and (set(existing_wits) != set(witness_eids) or len(existing_wits) != len(witness_eids)):
            raise RuntimeFault(
                "CONFLICT",
                "The existing permanent account AID does not match the allocated hosted witness pool.",
            )
        if existing_toad is not None and toad and existing_toad != toad:
            raise RuntimeFault(
                "CONFLICT",
                "The existing permanent account AID does not match the allocated witness threshold.",
            )
        return existing

    return hby.makeHab(
        name=alias,
        algo="randy",
        icount=1,
        isith="1",
        ncount=1,
        nsith="1",
        wits=list(witness_eids),
        toad=toad,
    )


def _remove_remote_ids(organizer, eids: list[str]):
    for eid in eids:
        if not eid:
            continue
        try:
            organizer.rem(eid)
        except Exception:
            continue


async def _resolve_kf_oobi(hby, organizer, *, url: str, display_url: str, alias: str):
    modules = _load_modules()
    fetch_url = _proxy_url(url)
    _clear_oobi_tracking(hby, fetch_url)

    oobi_record = modules["recording"].OobiRecord(date=modules["oobiing"].nowIso8601())
    if alias:
        oobi_record.oobialias = alias

    hby.db.oobis.pin(keys=(fetch_url,), val=oobi_record)
    try:
        roobi = await _await_resolution(hby, modules["oobiing"].Oobiery(hby=hby), fetch_url)
        update = {"oobi": display_url}
        if alias:
            update["alias"] = alias
        organizer.update(roobi.cid, update)
        return roobi.cid
    except Exception:
        _clear_oobi_tracking(hby, fetch_url)
        raise


async def _register_with_witness(hab, witness: dict):
    modules = _load_modules()
    kel = bytearray()
    for msg in hab.db.clonePreIter(pre=hab.pre):
        kel.extend(msg)

    form = js.FormData.new()
    form.append("kel", kel.decode("utf-8"))

    if hab.kever.delegated:
        delkel = bytearray()
        for msg in hab.db.clonePreIter(hab.kever.delpre):
            delkel.extend(msg)
        form.append("delkel", delkel.decode("utf-8"))

    headers = {
        modules["httping"].CESR_DESTINATION_HEADER: witness["eid"],
    }
    witness_url = urljoin(f"{witness['witnessUrl'].rstrip('/')}/", "/aids")
    response = await _fetch_response(witness_url, method="POST", headers=headers, body=form)
    raw_text = await _response_text(response)
    if int(response.status) >= 400:
        detail = raw_text.strip() or f"HTTP {response.status}"
        raise RuntimeFault("NETWORK_ERROR", f"{detail} from {witness_url}")

    try:
        data = json.loads(raw_text or "{}")
    except ValueError as exc:
        raise RuntimeFault("BAD_RESPONSE", f"Witness {witness['eid']} returned malformed JSON.") from exc

    totp = str(data.get("totp", "") or "")
    if not totp:
        raise RuntimeFault("BAD_RESPONSE", f"Witness {witness['eid']} response did not include an encrypted TOTP seed.")

    encrypted = modules["coring"].Matter(qb64=totp)
    decrypted = modules["coring"].Matter(qb64=hab.decrypt(ser=encrypted.raw))
    return {
        "eid": witness["eid"],
        "totpSeed": decrypted.raw.decode("utf-8"),
        "oobi": str(data.get("oobi") or witness.get("oobi") or ""),
        "witnessUrl": witness["witnessUrl"],
        "name": witness.get("name", ""),
    }


def _local_connection_status(hby, organizer, aid: str):
    if hby.kevers.get(aid) is not None:
        return "Connected", "success"
    if organizer.get(aid) is not None:
        return "Stored", "info"
    return "Pending local connect", "warning"


async def _list_kf_account_witnesses(hby, organizer, record):
    hab = _require_kf_account_hab(hby, record)
    reply = await _send_kf_exn(
        hby,
        hab,
        surface_url=_bootstrap_urls(record.boot_url)["accountUrl"],
        route="/account/witnesses",
        payload={"account_aid": record.account_aid},
        destination=record.boot_server_aid,
        expected_sender=record.boot_server_aid,
    )

    rows = []
    for entry in reply["payload"].get("witnesses", []):
        if not isinstance(entry, dict):
            continue
        local_status, local_tone = _local_connection_status(hby, organizer, str(entry.get("eid", "") or ""))
        rows.append(
            {
                "eid": str(entry.get("eid", "") or ""),
                "name": str(entry.get("name", "") or ""),
                "url": str(entry.get("url") or entry.get("witness_url") or ""),
                "regionId": str(entry.get("region_id", "") or ""),
                "regionName": str(entry.get("region_name", "") or ""),
                "oobi": _pick_oobi(entry),
                "hostedStatus": str(entry.get("status", "") or "allocated"),
                "localStatus": local_status,
                "localStatusTone": local_tone,
                "createdAt": str(entry.get("created_at", "") or ""),
            }
        )
    return rows


async def _list_kf_account_watchers(hby, organizer, record):
    hab = _require_kf_account_hab(hby, record)
    reply = await _send_kf_exn(
        hby,
        hab,
        surface_url=_bootstrap_urls(record.boot_url)["accountUrl"],
        route="/account/watchers",
        payload={"account_aid": record.account_aid},
        destination=record.boot_server_aid,
        expected_sender=record.boot_server_aid,
    )

    rows = []
    for entry in reply["payload"].get("watchers", []):
        if not isinstance(entry, dict):
            continue
        local_status, local_tone = _local_connection_status(hby, organizer, str(entry.get("eid", "") or ""))
        rows.append(
            {
                "eid": str(entry.get("eid", "") or ""),
                "name": str(entry.get("name", "") or ""),
                "url": str(entry.get("url") or entry.get("watcher_url") or ""),
                "regionId": str(entry.get("region_id", "") or ""),
                "regionName": str(entry.get("region_name", "") or ""),
                "oobi": _pick_oobi(entry),
                "hostedStatus": str(entry.get("status", "") or "created"),
                "localStatus": local_status,
                "localStatusTone": local_tone,
                "createdAt": str(entry.get("created_at", "") or ""),
            }
        )
    return rows


async def _refresh_kf_watcher_status(hby, organizer, record, watcher_id: str):
    hab = _require_kf_account_hab(hby, record)
    reply = await _send_kf_exn(
        hby,
        hab,
        surface_url=_bootstrap_urls(record.boot_url)["accountUrl"],
        route="/account/watchers/status",
        payload={"account_aid": record.account_aid, "watcher_eid": watcher_id},
        destination=record.boot_server_aid,
        expected_sender=record.boot_server_aid,
    )

    watcher = reply["payload"].get("watcher", {})
    if not isinstance(watcher, dict):
        raise RuntimeFault("BAD_RESPONSE", "KF watcher status reply was malformed.")

    local_status, local_tone = _local_connection_status(hby, organizer, str(watcher.get("eid", "") or watcher_id))
    return {
        "eid": str(watcher.get("eid", "") or watcher_id),
        "name": str(watcher.get("name", "") or ""),
        "url": str(watcher.get("url") or watcher.get("watcher_url") or ""),
        "regionId": str(watcher.get("region_id", "") or ""),
        "regionName": str(watcher.get("region_name", "") or ""),
        "oobi": _pick_oobi(watcher),
        "hostedStatus": str(watcher.get("status", "") or "created"),
        "localStatus": local_status,
        "localStatusTone": local_tone,
        "createdAt": str(watcher.get("created_at", "") or ""),
    }


async def _run_kf_onboarding(hby, organizer, *, boot_url: str, alias: str, witness_profile_code: str, account_aid: str = ""):
    modules = _load_modules()
    snapshot = await _fetch_bootstrap_snapshot(boot_url)
    option = _select_account_option(snapshot, witness_profile_code)
    if option is None:
        raise RuntimeFault(
            "VALIDATION",
            f"Witness profile '{witness_profile_code}' is not supported by the current KF bootstrap config.",
        )

    record = _load_kf_state(hby)
    if record.status == "onboarded" and record.account_aid:
        raise RuntimeFault("CONFLICT", "This vault already has an onboarded KERI Foundation account.")

    record.boot_url = snapshot["bootUrl"]
    record.account_alias = alias
    record.witness_profile_code = witness_profile_code
    record.failure_reason = ""
    _save_kf_state(hby, record)

    start_reply = None
    account_hab = None
    witness_rows = []
    watcher_row = None
    resolved_remote_ids = []
    boot_server_aid = record.boot_server_aid

    with modules["habbing"].openHab(
        name=f"kf-onboarding-{uuid4().hex[:12]}",
        temp=True,
        transferable=False,
    ) as (_temp_hby, ephemeral_hab):
        try:
            await _send_kf_event(snapshot["surfaces"]["onboardingUrl"], ephemeral_hab.msgOwnInception())
            start_reply = await _send_kf_exn(
                hby,
                ephemeral_hab,
                surface_url=snapshot["surfaces"]["onboardingUrl"],
                route="/onboarding/session/start",
                payload={
                    "account_alias": alias,
                    "chosen_profile_code": witness_profile_code,
                    "region_id": snapshot["bootstrap"]["regionId"],
                    "watcher_required": snapshot["bootstrap"]["watcherRequired"],
                },
                destination=boot_server_aid,
            )
            boot_server_aid = start_reply["sender"] or boot_server_aid

            start_payload = start_reply["payload"]
            try:
                js.console.log(
                    "[worker] [ob] session/start reply account_aid="
                    + str(start_payload.get("account_aid", "") or "")
                    + " toad=" + str(start_payload.get("toad", "") or "")
                )
            except Exception:
                pass
            for entry in start_payload.get("witnesses", []):
                if not isinstance(entry, dict):
                    continue
                witness_rows.append(
                    {
                        "eid": str(entry.get("eid", "") or ""),
                        "name": str(entry.get("name", "") or ""),
                        "witnessUrl": str(entry.get("witness_url") or entry.get("url") or ""),
                        "bootUrl": str(entry.get("boot_url", "") or ""),
                        "oobi": _pick_oobi(entry),
                        "regionId": str(entry.get("region_id", "") or snapshot["bootstrap"]["regionId"]),
                        "regionName": str(entry.get("region_name", "") or snapshot["bootstrap"]["regionName"]),
                    }
                )

            raw_watcher = start_payload.get("watcher")
            if isinstance(raw_watcher, dict):
                watcher_row = {
                    "eid": str(raw_watcher.get("eid", "") or ""),
                    "name": str(raw_watcher.get("name", "") or ""),
                    "watcherUrl": str(raw_watcher.get("watcher_url") or raw_watcher.get("url") or ""),
                    "oobi": _pick_oobi(raw_watcher),
                    "regionId": str(raw_watcher.get("region_id", "") or snapshot["bootstrap"]["regionId"]),
                    "regionName": str(raw_watcher.get("region_name", "") or snapshot["bootstrap"]["regionName"]),
                }

            if len(witness_rows) != option["witnessCount"]:
                raise RuntimeFault("BAD_RESPONSE", "KF bootstrap returned a witness pool that does not match the selected witness profile.")
            if snapshot["bootstrap"]["watcherRequired"] and watcher_row is None:
                raise RuntimeFault("BAD_RESPONSE", "KF bootstrap did not return the required hosted watcher allocation.")

            account_hab = _create_or_load_kf_account_hab(
                hby,
                record,
                alias=alias,
                requested_account_aid=str(account_aid or "").strip(),
                witness_eids=[witness["eid"] for witness in witness_rows],
                toad=int(start_payload.get("toad", 0) or option["toad"]),
            )
            try:
                js.console.log(
                    "[worker] [ob] account_hab.pre=" + str(account_hab.pre or "") + " requested_account_aid=" + str(account_aid or "").strip()
                )
            except Exception:
                pass

            for witness in witness_rows:
                try:
                    js.console.log(
                        "[worker] [ob] register-with-witness dest="
                        + str(witness.get("eid", "") or "")
                        + " url=" + str(witness.get("witnessUrl", "") or "")
                    )
                except Exception:
                    pass
                registration = await _register_with_witness(account_hab, witness)
                witness["oobi"] = registration["oobi"] or witness["oobi"]
                resolved_remote_ids.append(
                    await _resolve_kf_oobi(
                        hby,
                        organizer,
                        url=witness["oobi"],
                        display_url=witness["oobi"],
                        alias=witness["name"] or f"KF Witness {witness['eid'][:12]}",
                    )
                )

            if watcher_row is not None and watcher_row["oobi"]:
                resolved_remote_ids.append(
                    await _resolve_kf_oobi(
                        hby,
                        organizer,
                        url=watcher_row["oobi"],
                        display_url=watcher_row["oobi"],
                        alias=watcher_row["name"] or f"KF Watcher {watcher_row['eid'][:12]}",
                    )
                )

            await _send_kf_exn(
                hby,
                account_hab,
                surface_url=snapshot["surfaces"]["onboardingUrl"],
                route="/onboarding/account/create",
                payload={
                    "session_id": str(start_payload.get("session_id", "") or ""),
                    "account_aid": account_hab.pre,
                    "account_alias": alias,
                    "chosen_profile_code": witness_profile_code,
                    "region_id": snapshot["bootstrap"]["regionId"],
                    "witness_eids": [witness["eid"] for witness in witness_rows],
                    "watcher_eid": watcher_row["eid"] if watcher_row is not None else "",
                },
                destination=boot_server_aid,
                expected_sender=boot_server_aid,
            )
            await _send_kf_exn(
                hby,
                account_hab,
                surface_url=snapshot["surfaces"]["onboardingUrl"],
                route="/onboarding/complete",
                payload={
                    "session_id": str(start_payload.get("session_id", "") or ""),
                    "account_aid": account_hab.pre,
                },
                destination=boot_server_aid,
                expected_sender=boot_server_aid,
            )
        except Exception as exc:
            if start_reply is not None:
                session_id = str(start_reply["payload"].get("session_id", "") or "")
                if session_id:
                    cancel_hab = account_hab or ephemeral_hab
                    try:
                        await _send_kf_exn(
                            hby,
                            cancel_hab,
                            surface_url=snapshot["surfaces"]["onboardingUrl"],
                            route="/onboarding/cancel",
                            payload={
                                "session_id": session_id,
                                "account_aid": getattr(account_hab, "pre", "") if account_hab is not None else "",
                                "reason": "client_abandoned",
                            },
                            destination=boot_server_aid,
                            expected_sender=boot_server_aid or "",
                        )
                    except Exception:
                        pass

            _remove_remote_ids(organizer, resolved_remote_ids)
            record.status = "failed"
            record.failure_reason = str(exc)
            record.boot_server_aid = boot_server_aid or record.boot_server_aid
            _save_kf_state(hby, record)
            if isinstance(exc, RuntimeFault):
                raise
            raise RuntimeFault("RUNTIME_ERROR", f"KF onboarding failed: {exc}") from exc

    record.account_aid = account_hab.pre
    record.account_alias = alias
    record.status = "onboarded"
    record.onboarded_at = _now_iso()
    record.witness_profile_code = witness_profile_code
    record.witness_count = int(start_reply["payload"].get("witness_count", 0) or len(witness_rows))
    record.toad = int(start_reply["payload"].get("toad", 0) or option["toad"])
    record.watcher_required = snapshot["bootstrap"]["watcherRequired"]
    record.region_id = str(start_reply["payload"].get("region_id", "") or snapshot["bootstrap"]["regionId"])
    record.region_name = str(start_reply["payload"].get("region_name", "") or snapshot["bootstrap"]["regionName"])
    record.boot_server_aid = boot_server_aid
    record.witness_eids = [witness["eid"] for witness in witness_rows]
    record.watcher_eid = watcher_row["eid"] if watcher_row is not None else ""
    record.failure_reason = ""
    _save_kf_state(hby, record)

    reopened = await _persist_and_reload()
    reopened_record = _load_kf_state(reopened["hby"])
    reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
    return {
        "account": _kf_state_view(reopened_record),
        "witnesses": await _list_kf_account_witnesses(reopened["hby"], reopened_organizer, reopened_record),
        "watchers": await _list_kf_account_watchers(reopened["hby"], reopened_organizer, reopened_record),
    }


async def _dispatch(method: str, params: dict):
    if method == "vaults.list":
        registry = await _ensure_registry()
        return {"vaults": [_vault_view(record) for record in _registry_records(registry)]}

    if method == "vaults.create":
        create_started_at = _perf_ms()
        emit_runtime_diagnostic("vaults.create.enter", **_runtime_contract_summary())
        try:
            ensure_started_at = _perf_ms()
            emit_runtime_diagnostic(
                "vaults.create.ensure_registry.start",
                registry_name=REGISTRY_NAME,
            )
            try:
                registry = await _ensure_registry()
            except Exception as exc:
                emit_runtime_diagnostic(
                    "vaults.create.ensure_registry.error",
                    level="error",
                    registry_name=REGISTRY_NAME,
                    duration_ms=_duration_ms(ensure_started_at),
                    **_diagnostic_error_fields(exc),
                )
                raise
            emit_runtime_diagnostic(
                "vaults.create.ensure_registry.end",
                registry_name=REGISTRY_NAME,
                duration_ms=_duration_ms(ensure_started_at),
            )

            alias = _require_text(params.get("name"), field="Vault name")

            lookup_started_at = _perf_ms()
            emit_runtime_diagnostic("registry.record.lookup.start", registry_name=REGISTRY_NAME)
            try:
                existing_record = _registry_record_by_alias(registry, alias)
            except Exception as exc:
                emit_runtime_diagnostic(
                    "registry.record.lookup.error",
                    level="error",
                    registry_name=REGISTRY_NAME,
                    duration_ms=_duration_ms(lookup_started_at),
                    **_diagnostic_error_fields(exc),
                )
                raise
            emit_runtime_diagnostic(
                "registry.record.lookup.end",
                registry_name=REGISTRY_NAME,
                found=existing_record is not None,
                duration_ms=_duration_ms(lookup_started_at),
            )

            if existing_record is not None:
                raise RuntimeFault("CONFLICT", f"Vault '{alias}' already exists.")

            vault_id = _vault_id_for_alias(alias, {record["id"] for record in _registry_records(registry)})
            passcode = str(params.get("passcode") or "")
            encrypted = bool(passcode.strip())
            record = _vault_record(
                vault_id=vault_id,
                alias=alias,
                encrypted=encrypted,
                root_salt=_generate_root_salt(),
                passcode_kdf=_normalize_passcode_kdf(),
            )
            bran = _prepare_bran(passcode, record["passcodeKdf"]) if encrypted else ""

            temp_state = await _build_vault_state(record, bran=bran)
            await temp_state["hby"].aclose(clear=False)

            write_started_at = _perf_ms()
            emit_runtime_diagnostic(
                "registry.record.write.start",
                registry_name=REGISTRY_NAME,
                vault_id=vault_id,
                storage_name=record["storageName"],
            )
            try:
                _set_registry_record(registry, record)
            except Exception as exc:
                emit_runtime_diagnostic(
                    "registry.record.write.error",
                    level="error",
                    registry_name=REGISTRY_NAME,
                    vault_id=vault_id,
                    storage_name=record["storageName"],
                    duration_ms=_duration_ms(write_started_at),
                    **_diagnostic_error_fields(exc),
                )
                raise
            emit_runtime_diagnostic(
                "registry.record.write.end",
                registry_name=REGISTRY_NAME,
                vault_id=vault_id,
                storage_name=record["storageName"],
                duration_ms=_duration_ms(write_started_at),
            )

            flush_started_at = _perf_ms()
            emit_runtime_diagnostic(
                "registry.flush.start",
                registry_name=REGISTRY_NAME,
            )
            try:
                flushed_stores = await registry["db"].flush()
            except Exception as exc:
                emit_runtime_diagnostic(
                    "registry.flush.error",
                    level="error",
                    registry_name=REGISTRY_NAME,
                    duration_ms=_duration_ms(flush_started_at),
                    **_diagnostic_error_fields(exc),
                )
                raise
            emit_runtime_diagnostic(
                "registry.flush.end",
                registry_name=REGISTRY_NAME,
                flushed_stores=flushed_stores,
                duration_ms=_duration_ms(flush_started_at),
            )
            emit_runtime_diagnostic(
                "vaults.create.success",
                vault_id=vault_id,
                storage_name=record["storageName"],
                duration_ms=_duration_ms(create_started_at),
            )
            return {"vault": _vault_view(record)}
        except Exception as exc:
            emit_runtime_diagnostic(
                "vaults.create.error",
                level="error",
                duration_ms=_duration_ms(create_started_at),
                **_diagnostic_error_fields(exc),
            )
            raise

    if method == "vaults.open":
        registry = await _ensure_registry()
        vault_id = _require_vault_id(params)
        record = _registry_record_by_id(registry, vault_id)
        if record is None:
            raise RuntimeFault("NOT_FOUND", f"Vault '{vault_id}' was not found.")
        state = await _open_vault_state(record, passcode=params.get("passcode"))
        return _vault_summary(state)

    if method == "vaults.close":
        vault_id = _require_vault_id(params)
        _require_open_state(vault_id)
        await _close_state(clear=False)
        return {"vault": {"id": vault_id, "opened": False}}

    if method == "vaults.summary":
        state = _require_open_state(_require_vault_id(params))
        return _vault_summary(state)

    state = _require_open_state(_require_vault_id(params))
    modules = state["modules"]
    hby = state["hby"]
    organizer = modules["organizing"].Organizer(hby=hby)

    if method == "identifiers.list":
        return {"identifiers": _list_identifier_records(hby)}

    if method == "identifiers.get":
        aid = _require_text(params.get("aid"), field="Identifier")
        return {"identifier": _get_identifier_record(hby, aid)}

    if method == "identifiers.create":
        alias = _require_text(params.get("alias"), field="Identifier alias")
        if hby.habByName(alias) is not None:
            raise RuntimeFault("CONFLICT", f"Identifier alias '{alias}' already exists.")

        hby.makeHab(name=alias, icount=1, isith="1", ncount=1, nsith="1")
        reopened = await _persist_and_reload()
        return {
            "identifier": _get_identifier_record(reopened["hby"], reopened["hby"].habByName(alias).pre),
        }

    if method == "remotes.list":
        return {"remotes": _list_remote_records(hby, organizer)}

    if method == "remotes.get":
        aid = _require_text(params.get("aid"), field="Remote identifier")
        return {"remote": _get_remote_record(hby, organizer, aid)}

    if method == "remotes.update":
        aid = _require_text(params.get("aid"), field="Remote identifier")
        patch = params.get("patch") or {}
        if not isinstance(patch, dict):
            raise RuntimeFault("BAD_REQUEST", "Remote update patch must be an object.")
        if "oobi" in patch:
            raise RuntimeFault(
                "BAD_REQUEST",
                "Remote OOBI changes must go through remotes.resolveOobi.",
            )

        disallowed_fields = sorted(set(patch.keys()) - set(REMOTE_METADATA_FIELDS))
        if disallowed_fields:
            raise RuntimeFault(
                "BAD_REQUEST",
                "Only remote metadata fields alias, company, org, and note can be updated.",
            )

        _require_remote_contact(
            hby,
            organizer,
            aid,
            require_oobi=True,
            local_code="CONFLICT",
            missing_oobi_code="CONFLICT",
        )
        current_remote = _get_remote_record(hby, organizer, aid)

        cleaned = {}
        for field in REMOTE_METADATA_FIELDS:
            if field not in patch:
                continue
            value = str(patch[field] or "").strip()
            if field == "alias" and not value:
                raise RuntimeFault("VALIDATION", "Remote alias is required.")
            cleaned[field] = value

        if not cleaned:
            raise RuntimeFault("VALIDATION", "No remote fields were provided.")

        organizer.update(aid, cleaned)
        reopened = await _persist_and_reload()
        reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
        remote = _get_remote_record(reopened["hby"], reopened_organizer, aid)
        if remote["aid"] != current_remote["aid"] or remote["oobi"] != current_remote["oobi"]:
            raise RuntimeFault("CONFLICT", "Remote identity state changed during metadata update.")
        return {"remote": remote}

    if method == "remotes.resolveOobi":
        url = _require_blind_oobi_url(params.get("url"))
        alias = str(params.get("alias") or "").strip()
        existing_remote = _find_remote_contact_by_oobi(hby, organizer, url)
        if existing_remote is not None:
            raise RuntimeFault(
                "CONFLICT",
                "Blind OOBI is already connected. Use remote edit for metadata changes.",
            )

        remote_contacts_before = _remote_contacts_by_aid(hby, organizer)
        _clear_oobi_tracking(hby, url)

        oobi_record = modules["recording"].OobiRecord(date=modules["oobiing"].nowIso8601())
        if alias:
            oobi_record.oobialias = alias

        resolved_aid = None
        hby.db.oobis.pin(keys=(url,), val=oobi_record)
        try:
            roobi = await _await_resolution(hby, modules["oobiing"].Oobiery(hby=hby), url)
            resolved_aid = roobi.cid
            if _is_local_identifier(hby, resolved_aid):
                raise RuntimeFault(
                    "CONFLICT",
                    "Blind OOBI connect cannot add a local identifier to remotes.",
                )
            if resolved_aid in remote_contacts_before:
                raise RuntimeFault(
                    "CONFLICT",
                    "Blind OOBI is already connected to a stored remote identifier.",
                )

            update = {"oobi": url}
            if alias:
                update["alias"] = alias
            organizer.update(resolved_aid, update)

            reopened = await _persist_and_reload()
            reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
            return {
                "remote": _get_remote_record(reopened["hby"], reopened_organizer, resolved_aid),
            }
        except RuntimeFault:
            if resolved_aid is not None:
                _restore_contact(organizer, resolved_aid, remote_contacts_before.get(resolved_aid))
            _clear_oobi_tracking(hby, url)
            raise

    if method == "kf.bootstrap.get":
        record = _load_kf_state(hby)
        boot_url = _normalize_boot_url(params.get("bootUrl"), fallback=record.boot_url)
        record.boot_url = boot_url
        _save_kf_state(hby, record)

        if _is_bundled_app_shell():
            urls = _bootstrap_urls(boot_url)
            return {
                "bootUrl": boot_url,
                "connection": {
                    "ok": False,
                    "error": "KF bootstrap network is unavailable in this embedded app shell.",
                },
                "bootstrap": None,
                "surfaces": {
                    "onboardingUrl": urls["onboardingUrl"],
                    "accountUrl": urls["accountUrl"],
                },
                "account": _kf_state_view(record),
            }

        try:
            snapshot = await _fetch_bootstrap_snapshot(boot_url)
        except RuntimeFault as exc:
            urls = _bootstrap_urls(boot_url)
            return {
                "bootUrl": boot_url,
                "connection": {"ok": False, "error": str(exc)},
                "bootstrap": None,
                "surfaces": {
                    "onboardingUrl": urls["onboardingUrl"],
                    "accountUrl": urls["accountUrl"],
                },
                "account": _kf_state_view(record),
            }

        record.boot_url = snapshot["bootUrl"]
        _save_kf_state(hby, record)
        return {
            **snapshot,
            "account": _kf_state_view(record),
        }

    if method == "kf.onboarding.start":
        alias = _require_text(params.get("alias"), field="Account alias")
        witness_profile_code = _require_text(params.get("witnessProfileCode"), field="Witness profile")
        boot_url = _normalize_boot_url(params.get("bootUrl"), fallback=_load_kf_state(hby).boot_url)
        account_aid = str(params.get("accountAid") or "").strip()
        return await _run_kf_onboarding(
            hby,
            organizer,
            boot_url=boot_url,
            alias=alias,
            witness_profile_code=witness_profile_code,
            account_aid=account_aid,
        )

    if method == "kf.account.witnesses.list":
        record = _load_kf_state(hby)
        return {
            "account": _kf_state_view(record),
            "witnesses": await _list_kf_account_witnesses(hby, organizer, record),
        }

    if method == "kf.account.watchers.list":
        record = _load_kf_state(hby)
        return {
            "account": _kf_state_view(record),
            "watchers": await _list_kf_account_watchers(hby, organizer, record),
        }

    if method == "kf.account.watchers.status":
        record = _load_kf_state(hby)
        watcher_id = _require_text(
            params.get("watcherEid") or params.get("watcherId"),
            field="Watcher",
        )
        return {
            "account": _kf_state_view(record),
            "watcher": await _refresh_kf_watcher_status(hby, organizer, record, watcher_id),
        }

    if method == "settings.get":
        return {
            "settings": {
                **DEFAULT_SETTINGS,
                "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
            }
        }

    raise RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not allowed.")


vaulting.configure_runtime(
    ensure_runtime_packages=_ensure_runtime_packages,
    load_modules=_load_modules,
    wallet_storage_prefix=WALLET_STORAGE_PREFIX,
    registry_name=REGISTRY_NAME,
    registry_store=REGISTRY_STORE,
    legacy_root_salt=LEGACY_ROOT_SALT,
    passcode_kdf_defaults=PASSCODE_KDF_DEFAULTS,
    default_settings=DEFAULT_SETTINGS,
    kf_state_subdb=KF_STATE_SUBDB,
)
transporting.configure_runtime(
    origin=_origin,
    default_boot_url=DEFAULT_KF_BOOT_URL,
    kf_proxy_prefix=KF_PROXY_PREFIX,
    bootstrap_timeout_ms=KF_BOOTSTRAP_TIMEOUT_MS,
    cesr_timeout_ms=KF_CESR_TIMEOUT_MS,
    reply_message_limit=KF_CESR_REPLY_MESSAGE_LIMIT,
    reply_step_limit=KF_CESR_REPLY_STEP_LIMIT,
)
onboarding.configure_runtime(
    kf_state_key=KF_STATE_KEY,
    kf_state_subdb=KF_STATE_SUBDB,
    onboarding_auth_namespace=KF_ONBOARDING_AUTH_NAMESPACE,
    onboarding_auth_alias_prefix=KF_ONBOARDING_AUTH_ALIAS_PREFIX,
    account_query_timeout_ms=KF_ACCOUNT_QUERY_TIMEOUT_MS,
    witness_registration_timeout_ms=KF_WITNESS_REGISTRATION_TIMEOUT_MS,
    cesr_timeout_ms=KF_CESR_TIMEOUT_MS,
)


async def _test_oobi_parse(params: dict):
    """Level-gated no-network OOBI experiments inside the real worker.

    All levels decode/verify the deterministic witness-controller fixture and run
    against the currently-open vault's browser-backed KERI DB (WebBaser/WebKeeper/
    IndexedDB), with no hosted network.

      level=1: bare Parser (Vrsn_2_0) on the fixture bytes.
      level=2: stock Oobiery.parser — the wheel's Oobiery builds its own
               version-aware Parser from the Habery version (V2 on the new
               f4b9-aligned wheel).
      level=3: stock Oobiery.processClients with a completed 200 application/cesr
               response (smallest fake client) on pending coobi state.

    Only reachable when _test_oobi_enabled() (test marker/config present).
    """
    import hashlib
    from collections import deque

    level = int(params.get("level", 1) or 1)

    state = vaulting.require_open_state()
    hby = state["hby"]
    modules = _load_modules()
    oobiing = modules["oobiing"]
    recording = modules["recording"]

    # L4: REAL BrowserClienter path (the original production regression).
    # Stock Oobiery requests the deterministic fixture from the localhost test
    # endpoint (a real js.fetch through the browser httping clienter), then
    # processFlows -> processClients parse it into verified state. No injected
    # fake response; transport is localhost-only for determinism.
    if int(params.get("level", 1) or 1) == 4:
        from urllib.parse import urlparse
        import re as _re
        import time as _time

        url = str(params.get("oobiUrl") or "").strip()
        if not url:
            raise RuntimeFault("BAD_REQUEST", "level 4 requires oobiUrl (localhost deterministic endpoint).")
        host = (urlparse(url).hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeFault("BAD_REQUEST", "level 4 oobiUrl must be localhost (no-network deterministic test).")
        match = _re.fullmatch(r"/oobi/([^/]+)/([^/]+)", urlparse(url).path or "")
        if not match:
            raise RuntimeFault("BAD_REQUEST", "level 4 oobiUrl must be /oobi/<cid>/<role>.")
        l4_aid = match.group(1)
        cid_def = next((d for d in OOBI_FIXTURES.values() if d["aid"] == l4_aid), None)
        if cid_def is None:
            raise RuntimeFault("BAD_REQUEST", "level 4 oobiUrl cid is not a registered deterministic fixture.")
        from keri.kering import Roles, Schemes  # noqa: PLC0415

        oobiery = oobiing.Oobiery(hby=hby)
        obr = recording.OobiRecord(date=oobiing.nowIso8601())
        obr.cid = l4_aid
        hby.db.oobis.pin(keys=(url,), val=obr)
        t0 = _time.monotonic()
        js.console.log("[worker] OOBI_L4_AWAIT_RESOLUTION_ENTER")
        try:
            roob = await _await_resolution(hby, oobiery, url)
        except RuntimeFault:
            raise
        finally:
            js.console.log(f"[worker] OOBI_L4_AWAIT_RESOLUTION_EXIT {(_time.monotonic() - t0) * 1000:.0f}ms")
        ender = hby.db.ends.get(keys=(l4_aid, Roles.controller, l4_aid))
        locer = hby.db.locs.get(keys=(l4_aid, Schemes.https))
        kever_mem = hby.kevers.get(l4_aid) if hasattr(hby, "kevers") else None
        kevers_ref = getattr(getattr(hby, "db", None), "kevers", None)
        kever_by_pre = kevers_ref.get(l4_aid) if isinstance(kevers_ref, dict) else None
        keystore_size = len(kevers_ref) if isinstance(kevers_ref, dict) else -1
        js.console.log("[worker] OOBI_L4_DONE")
        return {
            "level": 4,
            "result": "PASS",
            "l4": "real_browserclienter",
            "roobi_state": str(getattr(roob, "state", "") or ""),
            "roobi_present": roob is not None,
            "http_complete": True,
            "processclients_returned": True,
            "keystate": kever_mem is not None or kever_by_pre is not None,
            "keystore_size": keystore_size,
            "loc_url": locer.url if locer is not None else None,
            "controller_role": bool(ender is not None and ender.allowed),
            "expected_loc": cid_def["url"],
            "target": cid_def["label"],
            "habery_version_major": getattr(getattr(hby, "version", None), "major", None),
        }

    raw_b64 = params.get("fixtureBase64") or params.get("fixture_b64") or ""
    expected_sha = str(params.get("expectedSha") or params.get("expected_sha") or "")
    try:
        fixture = base64.b64decode(raw_b64)
    except Exception as exc:
        raise RuntimeFault("BAD_REQUEST", f"fixtureBase64 is not valid base64: {exc}") from exc
    actual_sha = hashlib.sha256(fixture).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeFault("BAD_REQUEST", f"fixture SHA mismatch (got {actual_sha}, want {expected_sha}).")

    # V1-unsupported policy: a genuine V1 hosted stream must be rejected fast by
    # the V2-only hosted-boundary guard — never hang, never resolve, never touch
    # keystore/trusted state. This probe returns PASS only if the guard rejects.
    if actual_sha == V1_OOBI_FIXTURE_SHA:
        import time as _time

        t0 = _time.monotonic()
        try:
            _require_hosted_v2(fixture)
        except RuntimeFault as exc:
            bounded_ms = int((_time.monotonic() - t0) * 1000)
            return {
                "level": int(level),
                "result": "PASS",
                "policy": "reject_v1",
                "error_code": exc.code,
                "accepted": False,
                "duration_ms": bounded_ms,
                "target": "v1-negative",
            }
        return {
            "level": int(level),
            "result": "FAIL",
            "policy": "reject_v1",
            "accepted": True,
            "error": "V1 stream was not rejected by the V2-only boundary guard.",
        }

    fixture_def = OOBI_FIXTURES.get(actual_sha)
    if fixture_def is None:
        raise RuntimeFault("BAD_REQUEST", "fixture SHA is not a registered deterministic OOBI fixture.")

    # Every accepted hosted fixture must itself pass the V2-only boundary guard.
    try:
        _require_hosted_v2(fixture)
    except RuntimeFault as exc:
        raise RuntimeFault(UNSUPPORTED_KERI_VERSION, f"Hosted fixture rejected: {exc}") from exc

    wit = fixture_def["aid"]
    target_label = fixture_def["label"]
    target_url = fixture_def["url"]
    from keri.kering import Roles, Schemes  # noqa: PLC0415

    def state_snapshot(parser=None) -> dict:
        ender = hby.db.ends.get(keys=(wit, Roles.controller, wit))
        locer = hby.db.locs.get(keys=(wit, Schemes.https))
        # Kevery stores remote kevers at db.kevers[pre] (WebBaser keeps this as an
        # in-memory dict keyed by pre string, unlike a .get(keys=(...)) store).
        kever_mem = hby.kevers.get(wit) if hasattr(hby, "kevers") else None
        kevers_ref = getattr(getattr(hby, "db", None), "kevers", None)
        kevers_kind = type(kevers_ref).__name__ if kevers_ref is not None else None
        kever_by_pre = None
        if isinstance(kevers_ref, dict):
            kever_by_pre = kevers_ref.get(wit)
        elif kevers_ref is not None:
            try:
                kever_by_pre = kevers_ref.get(keys=(wit,))
            except Exception:
                kever_by_pre = None
        keystore_size = len(kevers_ref) if isinstance(kevers_ref, dict) else -1
        hby_ver = getattr(hby, "version", None)
        parser_ver = getattr(parser, "version", None) if parser is not None else None
        return {
            "target": target_label,
            "keystate": kever_mem is not None or kever_by_pre is not None,
            "keystate_mem": kever_mem is not None,
            "keystate_db": kever_by_pre is not None,
            "kevers_kind": kevers_kind,
            "keystore_size": keystore_size,
            "loc_url": locer.url if locer is not None else None,
            "controller_role": bool(ender is not None and ender.allowed),
            "expected_loc": target_url,
            # V2-only runtime invariants (Fort Web hosted integration is V2-only).
            "habery_version_major": getattr(hby_ver, "major", None),
            "parser_version_major": getattr(parser_ver, "major", None),
        }

    if level == 1:
        from keri.core import Parser, Router, Revery, Kevery  # noqa: PLC0415
        from keri.kering import Vrsn_2_0  # noqa: PLC0415

        rtr = Router()
        rvy = Revery(db=hby.db, rtr=rtr)
        # NOTE: this wheel's Kevery (keri/core/eventing.py) takes no `cf` argument,
        # unlike the newer native f4b9e3e keripy. Match the loaded wheel's signature.
        kvy = Kevery(db=hby.db, lax=False, local=False, rvy=rvy)
        kvy.registerReplyRoutes(router=rtr)
        prs = Parser(framed=True, kvy=kvy, rvy=rvy, version=Vrsn_2_0)
        js.console.log("[worker] OOBI_L1_PARSE_ENTER")
        prs.parse(ims=bytearray(fixture))  # drains its input bytearray
        js.console.log("[worker] OOBI_L1_PARSE_EXIT")
        return {"level": 1, "result": "PASS", **state_snapshot(prs)}

    if level == 2:
        try:
            oobiery = oobiing.Oobiery(hby=hby)
        except Exception as exc:
            raise RuntimeFault("RUNTIME_ERROR", f"Oobiery construction failed: {exc}") from exc
        js.console.log("[worker] OOBI_L2_ENTER")
        try:
            oobiery.parser.parse(ims=bytearray(fixture))
        except Exception as exc:
            raise RuntimeFault("RUNTIME_ERROR", f"OOBI_L2 parser.parse failed: {exc}") from exc
        js.console.log("[worker] OOBI_L2_EXIT")
        return {"level": 2, "result": "PASS", **state_snapshot(oobiery.parser)}

    if level == 3:
        try:
            oobiery = oobiing.Oobiery(hby=hby)
        except Exception as exc:
            raise RuntimeFault("RUNTIME_ERROR", f"Oobiery construction failed: {exc}") from exc
        url = str(params.get("oobiUrl") or f"{target_url}/oobi/{wit}/controller").strip()
        obr = recording.OobiRecord(date=oobiing.nowIso8601())
        obr.cid = wit
        js.console.log("[worker] OOBI_L3_ENTER")
        # Normal pending-OOBI state exactly as Oobiery.request() leaves it:
        # a coobi row + a client in self.clients awaiting a completed response.
        hby.db.coobi.pin(keys=(url,), val=obr)
        fake_client = SimpleNamespace(
            url=url,
            responses=deque(
                [
                    {
                        "status": 200,
                        "headers": {"Content-Type": oobiing.CESR_CONTENT_TYPE},
                        "body": fixture,
                    }
                ]
            ),
        )
        oobiery.clients[url] = fake_client
        js.console.log("[worker] OOBI_L3_BEFORE_PROCESSCLIENTS")
        try:
            oobiery.processClients()
        except Exception as exc:
            raise RuntimeFault("RUNTIME_ERROR", f"OOBI_L3 processClients failed: {exc}") from exc
        js.console.log("[worker] OOBI_L3_AFTER_PROCESSCLIENTS")
        roob = hby.db.roobi.get(keys=(url,))
        still_coobi = hby.db.coobi.get(keys=(url,)) is not None
        # f4b9 processClients removes the client from its clienter/doer registry
        # (not from Oobiery.clients), then clears coobi and records roobi=resolved.
        # The durable product signals are: coobi cleared + roobi resolved + kever.
        js.console.log("[worker] OOBI_L3_EXIT")
        return {
            "level": 3,
            "result": "PASS",
            "roobi_state": str(getattr(roob, "state", "") or ""),
            "roobi_present": roob is not None,
            "coobi_still_pending": still_coobi,
            "client_handled": roob is not None and not still_coobi,
            **state_snapshot(oobiery.parser),
        }

    raise RuntimeFault("BAD_REQUEST", f"Unsupported __test.oobi.parse level: {level}.")


async def _test_oobi_p8(params: dict):
    """Test-only reopen-persistence probe (Phase 8).

    Read-only KERI state queries against the currently-open vault's browser DB.
    All reads go through the normal KERI stores (kels/states/locs/ends/roobi)
    plus the f4b9 `db.kevers` statedict whose read-through cache rebuilds a
    remote Kever from the persisted key-state record on `__contains__`/item
    access (NOT on `.get()`).

      cmd=last_close : last product close_state outcome (no open state needed).
      cmd=identities : local identifier records (account identity persistence).
      cmd=oobi_counts : persisted OOBI tracking counts (nothing re-resolved).
      cmd=inspect    : per-AID persisted + reconstructed state snapshot. Given
                       {aid, url?}, reports persisted KEL/state/loc/end/roobi,
                       whether the kever was already cached in memory, whether it
                       reconstructs via read-through, and a usable key-state
                       summary when it does.

    Only reachable when _test_oobi_enabled() (test marker/config present).
    """
    cmd = str(params.get("cmd") or "").strip()
    modules = _load_modules()

    if cmd == "last_close":
        return {
            "cmd": cmd,
            "last_close": getattr(vaulting, "_LAST_CLOSE", None),
        }

    state = vaulting.require_open_state()
    hby = state["hby"]
    from keri.kering import Roles, Schemes  # noqa: PLC0415

    if cmd == "identities":
        return {
            "cmd": cmd,
            "identifiers": [
                {"aid": rec["aid"], "alias": rec["alias"]}
                for rec in vaulting._list_identifier_records(hby)
            ],
        }

    if cmd == "oobi_counts":
        db = hby.db

        def _count(store):
            count = 0
            try:
                for _ in store.getTopItemIter():
                    count += 1
                    if count > 5000:
                        break
            except Exception:
                return -1
            return count

        return {
            "cmd": cmd,
            "coobi": _count(db.coobi),
            "oobis": _count(db.oobis),
            "roobi": _count(db.roobi),
            "esrs_local": None,
        }

    if cmd == "inspect":
        aid = str(params.get("aid") or "").strip()
        url = str(params.get("url") or "").strip()
        if not aid:
            raise RuntimeFault("BAD_REQUEST", "cmd=inspect requires aid.")
        db = hby.db
        target_label = next((d["label"] for d in OOBI_FIXTURES.values() if d["aid"] == aid), None)
        ender = db.ends.get(keys=(aid, Roles.controller, aid))
        locer = db.locs.get(keys=(aid, Schemes.https))

        kel_last = None
        try:
            kel_last = db.kels.getLast(keys=aid, on=0)
        except Exception:
            kel_last = None

        ksr = None
        try:
            ksr = db.states.get(keys=aid)
        except Exception:
            ksr = None

        # In-memory-only check first (.get does NOT read through).
        cached_hit = False
        try:
            cached_hit = db.kevers.get(aid) is not None
        except Exception:
            cached_hit = False

        # Read-through reconstruction (__contains__ / __getitem__ do read through).
        reconstructed = False
        kever = None
        try:
            reconstructed = aid in db.kevers
            if reconstructed:
                kever = db.kevers[aid]
        except Exception:
            reconstructed = False
            kever = None

        kstate = None
        usable = False
        if kever is not None:
            def _ksget(ks, key):
                # KeyStateRecord supports attribute access; tolerate both shapes.
                try:
                    value = ks.get(key)
                    if value is not None:
                        return value
                except Exception:  # noqa: BLE001 - attribute-based record
                    pass
                return getattr(ks, key, None)

            try:
                ks = kever.state()
                state_keys = _ksget(ks, "k")
                usable = bool(state_keys)
                kstate = {
                    "sn": getattr(kever, "sn", None),
                    "est": bool(getattr(kever, "est", None)),
                    "transferable": bool(getattr(kever, "transferable", None)),
                    "verfer_count": len(getattr(kever, "verfers", []) or []),
                    "state_sn": _ksget(ks, "s"),
                    "state_dig": _ksget(ks, "d"),
                    "state_keys": state_keys,
                }
            except Exception as exc:  # noqa: BLE001 - diagnostic should not raise
                kstate = {"error": str(exc)[:160]}

        roobi_state = None
        if url:
            try:
                ro = db.roobi.get(keys=(url,))
                roobi_state = str(getattr(ro, "state", "") or "") or None
            except Exception:
                roobi_state = None

        return {
            "cmd": cmd,
            "aid": aid,
            "target": target_label,
            "persisted_kel": kel_last is not None,
            "persisted_state": ksr is not None,
            "loc_url": locer.url if locer is not None else None,
            "controller_role": bool(ender is not None and ender.allowed),
            "roobi_state": roobi_state,
            "kever_cached_in_memory": cached_hit,
            "kever_reconstructed": reconstructed,
            "kever_usable": usable,
            "kever": kstate,
            "habery_version_major": getattr(getattr(hby, "version", None), "major", None),
        }

    raise RuntimeFault("BAD_REQUEST", f"Unsupported __test.oobi.p8 cmd: {cmd}.")


async def _dispatch(method: str, params: dict):
    if _test_oobi_enabled() and method == TEST_OOBI_METHOD:
        return await _test_oobi_parse(params)
    if _test_oobi_enabled() and method == TEST_P8_METHOD:
        return await _test_oobi_p8(params)
    if method.startswith("kf."):
        return await onboarding.dispatch(method, params)
    return await vaulting.dispatch(method, params)


def _error_payload(message_id: str, code: str, message: str):
    return json.dumps(
        {
            "id": message_id,
            "kind": RESPONSE_KIND,
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        }
    )


async def handle_request(raw_message):
    try:
        request = json.loads(raw_message)
    except Exception:
        return _error_payload("invalid", "BAD_REQUEST", "Runtime request payload was not valid JSON.")

    if not isinstance(request, dict):
        return _error_payload("invalid", "BAD_REQUEST", "Runtime request payload must be an object.")

    message_id = str(request.get("id") or "invalid")
    if request.get("kind") != REQUEST_KIND:
        return _error_payload(message_id, "BAD_REQUEST", "Runtime request kind was invalid.")

    method = request.get("method")
    if not isinstance(method, str) or not method:
        return _error_payload(message_id, "BAD_REQUEST", "Runtime request method was invalid.")
    test_oobi_allowed = method in (TEST_OOBI_METHOD, TEST_P8_METHOD) and _test_oobi_enabled()
    if method not in ALLOWED_METHODS and not test_oobi_allowed:
        return _error_payload(message_id, "BAD_REQUEST", f"Runtime method '{method}' is not allowed.")

    params = request.get("params")
    if params is None and "params" not in request:
        params = {}
    if not isinstance(params, dict):
        return _error_payload(message_id, "BAD_REQUEST", "Runtime request params must be an object.")

    try:
        t_dispatch = _perf_ms()
        js.console.log(f"[worker] >> {method} (id={message_id})")
        async with _REQUEST_LOCK:
            result = await _dispatch(method, params)
        js.console.log(f"[worker] << {method}: {_perf_ms() - t_dispatch:.0f}ms")
        return json.dumps(
            {
                "id": message_id,
                "kind": RESPONSE_KIND,
                "ok": True,
                "result": result,
            }
        )
    except (RuntimeFault, vaulting.RuntimeFault) as exc:
        js.console.log(f"[worker] << {method} FAULT({exc.code}): {_perf_ms() - t_dispatch:.0f}ms")
        return _error_payload(message_id, exc.code, str(exc))
    except Exception:
        try:
            js.console.error(f"[worker] << {method} ERROR: {_perf_ms() - t_dispatch:.0f}ms")
            js.console.error(traceback.format_exc())
        except Exception:
            pass
        return _error_payload(message_id, "RUNTIME_ERROR", "Runtime request failed.")


async def _preload():
    t0 = _perf_ms()
    try:
        emit_runtime_diagnostic("worker_preload_start", **_runtime_contract_summary())
        _validate_runtime_origin_contract()
        await _ensure_runtime_packages()
        _load_modules()
        dur = round(_perf_ms() - t0)
        js.console.log(f"[worker] preload complete: {dur:.0f}ms")
        emit_runtime_diagnostic(
            "worker_preload_complete",
            duration_ms=dur,
        )
    except Exception as exc:
        emit_runtime_diagnostic(
            "worker_preload_failed",
            level="error",
            duration_ms=round(_perf_ms() - t0),
            error=str(exc),
        )
        raise


asyncio.ensure_future(_preload())

sync.handle_request = handle_request
