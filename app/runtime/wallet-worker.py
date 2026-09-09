from __future__ import annotations

import asyncio
import base64
import importlib
import json
import re
import warnings
from urllib.parse import urlparse
from uuid import uuid4

import js

import onboarding
import runtime_packages
import transporting
import vaulting

_SYNC_PROBE = "fortweb.runtime.rpc.probe"
WORKER_ID = uuid4().hex

RuntimeFault = vaulting.RuntimeFault

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
}
DEFAULT_KF_BOOT_URL = "http://127.0.0.1:9723"
KF_STATE_KEY = "state"
KF_STATE_SUBDB = "kfst."
KF_PROXY_PREFIX = "/_fortweb_proxy"
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
IOS_APP_LOCAL_ORIGIN = "app://local"
IOS_LOOPBACK_HOST = "127.0.0.1"
IOS_LOOPBACK_PREFIX = "_fortios"
IOS_LOOPBACK_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_MODULES = None
_REQUEST_LOCK = asyncio.Lock()
_RUNTIME_READY_TASK = None


def _origin():
    contract_origin = _runtime_contract_text(("documentOrigin",))
    if contract_origin:
        return contract_origin.rstrip("/")

    try:
        return str(js.location.origin or "")
    except Exception:
        return ""


async def _ensure_runtime_packages():
    return await runtime_packages.ensure_runtime_packages()


def _load_modules():
    global _MODULES
    if _MODULES is None:
        _MODULES = {
            "habbing": importlib.import_module("keri.app.habbing"),
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
    return _MODULES


def _perf_ms():
    from js import performance
    return performance.now()


_DIAGNOSTIC_KIND = "fortweb.runtime.diagnostic"


def _config_dict():
    raw_config = importlib.import_module("polyscript").config
    config = raw_config.to_py()
    if not isinstance(config, dict):
        raise RuntimeFault("BAD_CONFIG", "PyWorker runtime configuration was invalid.")
    return config


class _WorkerStorage:
    """Adapt the PyWorker storage handle to the Keripy storage contract."""

    def __init__(self, handle):
        self._handle = handle

    def get(self, key, default=None):
        value = self._handle.get(key)
        return default if value is None else value

    def __setitem__(self, key, value):
        if not isinstance(value, str):
            raise TypeError(f"Worker storage requires string values, got {type(value)}")
        self._handle.set(key, value)

    async def sync(self):
        await self._handle.sync()


async def _open_worker_storage(namespace):
    module = importlib.import_module("polyscript")
    return _WorkerStorage(await module.storage(f"@fortweb/{namespace}"))


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


vaulting.configure_runtime(
    ensure_runtime_packages=_ensure_runtime_packages,
    load_modules=_load_modules,
    clienter_factory=transporting.BrowserClienter,
    storage_opener=_open_worker_storage,
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


async def _dispatch(method: str, params: dict):
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
    if raw_message == _SYNC_PROBE:
        try:
            await _ensure_runtime_ready()
            return _SYNC_PROBE
        except (RuntimeFault, vaulting.RuntimeFault) as exc:
            return json.dumps({
                "kind": "fortweb.runtime.rpc.probe.error",
                "code": exc.code,
                "message": str(exc),
            })
        except Exception:
            return json.dumps({
                "kind": "fortweb.runtime.rpc.probe.error",
                "code": "RUNTIME_ERROR",
                "message": "Runtime worker preload failed.",
            })

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
    if method not in ALLOWED_METHODS:
        return _error_payload(message_id, "BAD_REQUEST", f"Runtime method '{method}' is not allowed.")

    params = request.get("params")
    if params is None and "params" not in request:
        params = {}
    if not isinstance(params, dict):
        return _error_payload(message_id, "BAD_REQUEST", "Runtime request params must be an object.")

    try:
        await _ensure_runtime_ready()
        async with _REQUEST_LOCK:
            result = await _dispatch(method, params)
        return json.dumps(
            {
                "id": message_id,
                "kind": RESPONSE_KIND,
                "ok": True,
                "result": result,
            }
        )
    except (RuntimeFault, vaulting.RuntimeFault) as exc:
        return _error_payload(message_id, exc.code, str(exc))
    except Exception:
        return _error_payload(message_id, "RUNTIME_ERROR", "Runtime request failed.")


async def _preload():
    t0 = _perf_ms()
    try:
        emit_runtime_diagnostic("worker_preload_start", **_runtime_contract_summary())
        _validate_runtime_origin_contract()
        await _ensure_runtime_packages()
        _load_modules()
        package_evidence = runtime_packages.runtime_evidence((
            "hio",
            "keri",
            "pysodium",
            "keri.db.webdbing",
            "keri.db.webbasing",
            "keri.app.webkeeping",
            "keri.app.habbing",
            "keri.app.oobiing",
        ))
        emit_runtime_diagnostic(
            "worker_package_closure",
            worker_id=WORKER_ID,
            report_b64=base64.b64encode(
                json.dumps(package_evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).decode("ascii"),
        )
        dur = round(_perf_ms() - t0)
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


async def _ensure_runtime_ready():
    global _RUNTIME_READY_TASK

    if _RUNTIME_READY_TASK is None:
        _RUNTIME_READY_TASK = asyncio.ensure_future(_preload())
    task = _RUNTIME_READY_TASK

    try:
        await task
    except Exception:
        if _RUNTIME_READY_TASK is task:
            _RUNTIME_READY_TASK = None
        raise
    return True


__export__ = ["handle_request"]
