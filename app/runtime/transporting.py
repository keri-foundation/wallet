from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from inspect import isawaitable
from urllib.parse import urljoin, urlparse

import js

import vaulting


_CONFIG: dict = {}

# Only loopback/local dev endpoints are routed through the same-origin dev
# proxy. External/public http(s) endpoints are fetched directly by the browser.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def configure_runtime(
    *,
    origin,
    default_boot_url: str,
    kf_proxy_prefix: str,
    bootstrap_timeout_ms: int,
    cesr_timeout_ms: int,
    reply_message_limit: int,
    reply_step_limit: int,
):
    _CONFIG.clear()
    _CONFIG.update(
        origin=origin,
        default_boot_url=default_boot_url,
        kf_proxy_prefix=kf_proxy_prefix,
        bootstrap_timeout_ms=bootstrap_timeout_ms,
        cesr_timeout_ms=cesr_timeout_ms,
        reply_message_limit=reply_message_limit,
        reply_step_limit=reply_step_limit,
    )


@dataclass(frozen=True)
class KfSurfaceConfig:
    onboarding_url: str
    account_url: str
    onboarding_destination: str = ""
    account_destination: str = ""

    @property
    def bootstrap_url(self) -> str:
        return urljoin(self.onboarding_url, "/bootstrap/config")

    @property
    def health_url(self) -> str:
        return urljoin(self.onboarding_url, "/health")

    @property
    def boot_url(self) -> str:
        return _surface_root_url(self.onboarding_url or self.account_url)


def _surface_root_url(url: str):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_optional_http_url(value, *, field: str):
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise vaulting.RuntimeFault("VALIDATION", f"{field} must be an absolute http(s) URL.")
    return text.rstrip("/")


def _normalize_boot_url(value, *, fallback: str = ""):
    text = str(value or "").strip() or str(fallback or "").strip() or _CONFIG["default_boot_url"]
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise vaulting.RuntimeFault("VALIDATION", "KF boot URL must be an absolute http(s) URL.")
    return text.rstrip("/")


def _surface_config_from_boot_url(boot_url: str):
    normalized = _normalize_boot_url(boot_url)
    base = f"{normalized}/"
    return KfSurfaceConfig(
        onboarding_url=urljoin(base, "/onboarding"),
        account_url=urljoin(base, "/account"),
    )


def coerce_kf_surface_config(raw_value, *, fallback_boot_url: str = ""):
    if isinstance(raw_value, dict):
        return KfSurfaceConfig(
            onboarding_url=_normalize_optional_http_url(
                raw_value.get("onboardingUrl"),
                field="KF onboarding surface URL",
            ),
            account_url=_normalize_optional_http_url(
                raw_value.get("accountUrl"),
                field="KF account surface URL",
            ),
            onboarding_destination=str(raw_value.get("onboardingDestination") or "").strip(),
            account_destination=str(raw_value.get("accountDestination") or "").strip(),
        )

    if fallback_boot_url:
        return _surface_config_from_boot_url(fallback_boot_url)
    return _surface_config_from_boot_url(_CONFIG["default_boot_url"])


def require_kf_surface_url(surfaces: KfSurfaceConfig, surface_name: str):
    if surface_name == "account":
        if surfaces.account_url:
            return surfaces.account_url
        raise vaulting.RuntimeFault(
            "CONFIG_ERROR",
            "KF account surface URL is not configured for this Fortweb environment.",
        )

    if surfaces.onboarding_url:
        return surfaces.onboarding_url
    raise vaulting.RuntimeFault(
        "CONFIG_ERROR",
        "KF onboarding surface URL is not configured for this Fortweb environment.",
    )


def kf_surface_destination(surfaces: KfSurfaceConfig, *, surface_name: str, boot_server_aid: str = ""):
    if surface_name == "account":
        return surfaces.account_destination or boot_server_aid
    return surfaces.onboarding_destination or boot_server_aid


def proxy_url(url: str):
    if not url:
        return url

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    origin = _CONFIG["origin"]()
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
    proxied = f"{origin}{_CONFIG['kf_proxy_prefix']}/{parsed.scheme}/{parsed.netloc}{path}"
    if parsed.query:
        proxied = f"{proxied}?{parsed.query}"
    return proxied


def install_browser_clienter_proxy(httping):
    clienter = getattr(httping, "Clienter", None)
    if clienter is None or getattr(clienter, "_fortweb_proxy_patch", False):
        return

    original_request = clienter.request

    def proxied_request(self, method, url, body=None, headers=None):
        request_url = proxy_url(url) if self._useBrowserFetch(url) else url
        return original_request(self, method, request_url, body=body, headers=headers)

    clienter.request = proxied_request
    clienter._fortweb_proxy_patch = True


def _js_error_name(exc):
    try:
        return str(getattr(exc, "name", "") or "")
    except Exception:
        return ""


async def fetch_response(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body=None,
    timeout_ms: int | None = None,
):
    timeout_handle = None
    timeout_ms = int(timeout_ms or _CONFIG["bootstrap_timeout_ms"])
    try:
        request_headers = js.Headers.new()
        for key, value in (headers or {}).items():
            request_headers.set(str(key), str(value))

        options = js.Object.new()
        options.method = method
        options.headers = request_headers
        if body is not None:
            options.body = body
        if hasattr(js, "AbortController"):
            controller = js.AbortController.new()
            options.signal = controller.signal

            def _abort():
                try:
                    controller.abort()
                except Exception:
                    return

            timeout_handle = js.setTimeout(_abort, timeout_ms)

        pending = js.fetch(proxy_url(url), options)
        return await pending if isawaitable(pending) else pending
    except Exception as exc:
        if _js_error_name(exc) == "AbortError":
            raise vaulting.RuntimeFault("TIMEOUT", f"Timed out calling {url}.") from exc
        raise vaulting.RuntimeFault("NETWORK_ERROR", f"Network request failed for {url}: {exc}") from exc
    finally:
        if timeout_handle is not None:
            try:
                js.clearTimeout(timeout_handle)
            except Exception:
                pass


async def response_text(response):
    pending = response.text()
    text = await pending if isawaitable(pending) else pending
    return str(text or "")


async def response_bytes(response):
    pending = response.arrayBuffer()
    buffer = await pending if isawaitable(pending) else pending
    return bytes(js.Uint8Array.new(buffer).to_py())


async def fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body=None,
    timeout_ms: int | None = None,
):
    response = await fetch_response(url, method=method, headers=headers, body=body, timeout_ms=timeout_ms)
    raw_text = await response_text(response)
    if int(response.status) >= 400:
        detail = raw_text.strip() or f"HTTP {response.status}"
        raise vaulting.RuntimeFault("NETWORK_ERROR", f"{detail} from {url}")

    try:
        data = json.loads(raw_text or "{}")
    except ValueError as exc:
        raise vaulting.RuntimeFault("BAD_RESPONSE", f"KF service returned malformed JSON for {url}.") from exc

    if not isinstance(data, dict):
        raise vaulting.RuntimeFault("BAD_RESPONSE", f"KF service returned a non-object JSON payload for {url}.")
    return data


async def fetch_bootstrap_snapshot(surfaces: KfSurfaceConfig):
    onboarding_url = require_kf_surface_url(surfaces, "onboarding")
    account_url = require_kf_surface_url(surfaces, "account")
    body = await fetch_json(
        urljoin(onboarding_url, "/bootstrap/config"),
        timeout_ms=_CONFIG["bootstrap_timeout_ms"],
    )
    bootstrap = body.get("bootstrap", body) if isinstance(body, dict) else {}
    region = body.get("region", {}) if isinstance(body.get("region"), dict) else {}
    advertised_surfaces = body.get("surfaces", {}) if isinstance(body.get("surfaces"), dict) else {}
    onboarding_surface = (
        advertised_surfaces.get("onboarding", {})
        if isinstance(advertised_surfaces.get("onboarding"), dict)
        else {}
    )
    account_surface = (
        advertised_surfaces.get("account", {})
        if isinstance(advertised_surfaces.get("account"), dict)
        else {}
    )

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

    advertised_onboarding_url = _normalize_optional_http_url(
        onboarding_surface.get("url"),
        field="KF bootstrap onboarding surface URL",
    )
    advertised_account_url = _normalize_optional_http_url(
        account_surface.get("url"),
        field="KF bootstrap account surface URL",
    )

    return {
        "bootUrl": surfaces.boot_url or _surface_root_url(advertised_onboarding_url or advertised_account_url),
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
            "onboardingUrl": onboarding_url,
            "accountUrl": account_url,
        },
    }


def _split_cesr_message(ims):
    buf = bytearray(ims)
    serder = vaulting.load_modules()["serdering"].SerderKERI(raw=bytes(buf))
    body = bytes(buf[:serder.size])
    attachment = bytes(buf[serder.size:])
    return body, attachment


def sha256_hex(data: bytes) -> str:
    """Bounded non-sensitive digest for diagnostics (never the raw payload)."""
    return hashlib.sha256(data).hexdigest()


def _split_cesr_stream(ims):
    serders = []
    buf = bytearray(ims)
    while buf:
        serder = vaulting.load_modules()["serdering"].SerderKERI(raw=bytes(buf))
        serders.append(serder)
        del buf[:serder.size]
        while buf and buf[0] != 0x7B:
            del buf[:1]
    return serders


def _count_sender_escrow(store, sender):
    try:
        return sum(1 for _ in store.getAllItemIter(keys=sender))
    except Exception:
        return 0


def _sender_parse_diagnostics(hby, sender: str, *, stream=None):
    if not sender:
        return ""

    state_present = bool(hby.db.states.get(keys=sender))
    detail = (
        f"sender_state={state_present} "
        f"ooe={_count_sender_escrow(hby.db.ooes, sender)} "
        f"pse={_count_sender_escrow(hby.db.pses, sender)} "
        f"pwe={_count_sender_escrow(hby.db.pwes, sender)}"
    )
    if stream:
        try:
            ilks = ",".join(
                f"{s.ked.get('t', '?')}:{str(s.ked.get('i', '') or '')[:8]}:s{str(s.ked.get('s', '') or '?')}"
                for s in stream[:10]
            )
            detail += f" stream[{len(stream)}]=({ilks})"
        except Exception:
            pass
    return detail


def _restore_sender_kever_from_state(hby, sender: str):
    if not sender or sender in hby.kevers:
        return

    state = hby.db.states.get(keys=sender)
    if state is None:
        return

    try:
        kever = vaulting.load_modules()["eventing"].Kever(state=state, db=hby.db, local=False)
    except Exception:
        return

    hby.db.kevers[kever.prefixer.qb64] = kever


def _kf_reply_parser_version(ims: bytes | bytearray):
    modules = vaulting.load_modules()
    try:
        serders = _split_cesr_stream(ims)
    except Exception:
        serders = []

    version_field = str(serders[0].ked.get("v", "") or "") if serders else ""
    if version_field.startswith(("KERI10", "ACDC10")):
        return modules["kering"].Vrsn_1_0
    return modules["kering"].Vrsn_2_0


def _new_kf_reply_parser_context(hby, *, version):
    modules = vaulting.load_modules()
    rvy = modules["routing"].Revery(db=hby.db)
    exc = modules["exchanging"].Exchanger(hby=hby, handlers=[])
    kvy = modules["eventing"].Kevery(db=hby.db, lax=True, local=False, rvy=rvy)
    kvy.registerReplyRoutes(router=rvy.rtr)
    parser = modules["parsing"].Parser(
        kvy=kvy,
        rvy=rvy,
        exc=exc,
        local=False,
        version=version,
    )
    return parser, kvy, rvy, exc


async def post_cesr(
    url: str,
    *,
    body: bytes | bytearray,
    attachment: bytes | bytearray | None = None,
    destination: str = "",
    method: str = "POST",
    timeout_ms: int | None = None,
):
    modules = vaulting.load_modules()
    headers = {
        "Content-Type": str(getattr(modules["httping"], "CESR_CONTENT_TYPE", "") or "application/cesr"),
        "Content-Length": str(len(body)),
    }
    if attachment:
        headers[modules["httping"].CESR_ATTACHMENT_HEADER] = bytes(attachment).decode("utf-8")
    if destination:
        headers[modules["httping"].CESR_DESTINATION_HEADER] = destination

    response = await fetch_response(
        url,
        method=method,
        headers=headers,
        body=bytes(body).decode("utf-8"),
        timeout_ms=timeout_ms or _CONFIG["cesr_timeout_ms"],
    )
    raw_bytes = await response_bytes(response)
    if int(response.status) >= 400:
        detail = raw_bytes.decode("utf-8", errors="ignore").strip() or f"HTTP {response.status}"
        raise vaulting.RuntimeFault("NETWORK_ERROR", f"{detail} from {url}")
    attachment_header = str(response.headers.get(modules["httping"].CESR_ATTACHMENT_HEADER) or "")
    return raw_bytes, attachment_header


async def post_cesr_stream(
    url: str,
    *,
    ims: bytes | bytearray,
    destination: str = "",
    method: str = "PUT",
    timeout_ms: int | None = None,
):
    modules = vaulting.load_modules()
    raw = bytes(ims)
    headers = {
        "Content-Type": str(getattr(modules["httping"], "CESR_CONTENT_TYPE", "") or "application/cesr"),
        "Content-Length": str(len(raw)),
    }
    if destination:
        headers[modules["httping"].CESR_DESTINATION_HEADER] = destination

    response = await fetch_response(
        url,
        method=method,
        headers=headers,
        body=raw.decode("utf-8"),
        timeout_ms=timeout_ms or _CONFIG["cesr_timeout_ms"],
    )
    raw_bytes = await response_bytes(response)
    if int(response.status) >= 400:
        detail = raw_bytes.decode("utf-8", errors="ignore").strip() or f"HTTP {response.status}"
        raise vaulting.RuntimeFault("NETWORK_ERROR", f"{detail} from {url}")
    return raw_bytes


def _consume_cesr_reply(parser, *, ims: bytearray):
    if len(_split_cesr_stream(ims)) > _CONFIG["reply_message_limit"]:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "KF service reply exceeded the maximum supported message count.")
    steps = 0

    while ims:
        remaining = len(ims)
        # f4b9 Parser binds kvy/rvy/exc at construction. A KF service reply is a
        # multi-message CESR stream (e.g. prepended sender icp + exn), so it must
        # be parsed unframed. Crucially we use allParsator (not msgParsator):
        # msgParsator only *extracts* message substreams and never routes them,
        # so a prepended sender icp would never be committed to hby.db.kevers and
        # the reply sender could not be verified. allParsator routes each message
        # to the bound kvy/rvy/exc, processing the KEL and creating kevers.
        parsator = parser.allParsator(
            ims=ims,
            framed=False,
            local=False,
            version=getattr(parser, "version", None),
        )

        while True:
            try:
                next(parsator)
                steps += 1
            except StopIteration:
                break
            except Exception as exc:
                raise vaulting.RuntimeFault(
                    "BAD_RESPONSE",
                    f"KF service reply parser rejected the CESR reply stream: {exc}",
                ) from exc

            if steps >= _CONFIG["reply_step_limit"]:
                raise vaulting.RuntimeFault(
                    "BAD_RESPONSE",
                    "KF service reply parser stalled before consuming the CESR reply stream.",
                )

        if len(ims) >= remaining:
            raise vaulting.RuntimeFault(
                "BAD_RESPONSE",
                "KF service reply parser did not advance while consuming the CESR reply stream.",
            )

    if ims:
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            "KF service reply parser did not consume the full CESR reply stream.",
        )


def parse_cesr_reply(
    hby,
    raw_bytes: bytes,
    attachment_header: str,
    *,
    expected_route: str = "",
    expected_sender: str = "",
    expected_ilks: tuple[str, ...] = ("exn",),
):
    ims = bytearray(raw_bytes or b"")
    if attachment_header:
        ims.extend(attachment_header.encode("utf-8"))
    if not ims:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "KF service returned an empty authenticated reply.")

    serders = _split_cesr_stream(ims)
    if not serders:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "KF service reply could not be parsed as CESR.")

    parser, kvy, rvy, exc = _new_kf_reply_parser_context(
        hby,
        version=_kf_reply_parser_version(ims),
    )
    parser_version_major = getattr(getattr(parser, "version", None), "major", None)
    _consume_cesr_reply(parser, ims=bytearray(ims))
    kvy.processEscrows()

    last = serders[-1]
    ilk = str(last.ked.get("t", "") or "")
    if expected_ilks and ilk not in expected_ilks:
        expected = ", ".join(expected_ilks)
        raise vaulting.RuntimeFault("BAD_RESPONSE", f"KF service reply had unexpected ilk '{ilk}', expected {expected}.")

    route = str(last.ked.get("r", "") or "")
    if expected_route and route != expected_route:
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"KF service reply route '{route}' did not match expected route '{expected_route}'.",
        )

    sender = str(getattr(last, "pre", "") or last.ked.get("i", "") or "")
    if expected_sender and not sender:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "KF service reply did not include a verifiable sender AID.")
    if expected_sender and sender != expected_sender:
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"KF service reply sender '{sender}' did not match expected boot server '{expected_sender}'.",
        )
    _restore_sender_kever_from_state(hby, sender)
    if sender and sender not in hby.kevers:
        detail = _sender_parse_diagnostics(hby, sender, stream=serders)
        try:
            kel_last = hby.db.kels.getLast(keys=sender, on=0) is not None
        except Exception:
            kel_last = None
        detail += (
            f" parser_version_major={parser_version_major} "
            f"n_kevers_total={sum(1 for _ in hby.db.kevers)} "
            f"sender_kel_last={kel_last}"
        )
        # Bounded, non-sensitive diagnostics only — never dump the signed CESR
        # body/attachment (which carries controller/signer payloads).
        detail += (
            f" body_len={len(raw_bytes or b'')} "
            f"attach_len={len((attachment_header or '').encode('utf-8'))} "
            f"body_sha256={sha256_hex(raw_bytes or b'')} "
            f"attach_sha256={sha256_hex((attachment_header or '').encode('utf-8'))}"
        )
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            (
                f"KF service reply sender '{sender}' is not verifiable after parsing the prepended KEL."
                + (f" {detail}" if detail else "")
            ),
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


def _iter_surface_keystate_messages(*, hab, start_sn: int, end_sn: int):
    for sn in range(start_sn, end_sn + 1):
        yield sn, own_event_replay(hab, sn=sn, pipelined=True)


def own_event_replay(hab, *, sn: int, pipelined: bool = False):
    """Return a locally signed event plus any persisted witness receipts.

    Uses canonical KERI messagize to include both controller signatures
    (sigers) and witness receipts (wigers) in standard CESR attachment order.
    """
    modules = vaulting.load_modules()
    msg = bytearray(hab.msgOwnEvent(sn=sn))
    serder = modules["serdering"].SerderKERI(raw=bytes(msg))
    wigs = hab.db.wigs.get(keys=vaulting.dg_key(hab.pre, serder.said)) or []
    if not wigs:
        return bytes(msg)

    sigers = hab.db.sigs.get(keys=vaulting.dg_key(hab.pre, serder.said)) or []
    try:
        js.console.log(
            f"[transport] replay sn={sn} sigers={len(sigers)} wigs={len(wigs)} pipelined={pipelined}"
        )
    except Exception:
        pass
    return bytes(
        modules["eventing"].messagize(
            serder,
            sigers=sigers,
            wigers=wigs,
            framed=pipelined,
            gvrsn=getattr(serder, "gvrsn", None) or serder.pvrsn,
        )
    )


async def _ensure_surface_keystate(hab, *, surface_name: str, surface_url: str, destination: str = ""):
    current_sn = int(getattr(getattr(hab, "kever", None), "sn", 0) or 0)
    state = vaulting.require_open_state()
    cache = state.setdefault("kfSurfaceKeyState", {})
    cache_key = (surface_name, hab.pre)
    synced_sn = int(cache.get(cache_key, -1) or -1)
    if synced_sn >= current_sn:
        return

    for _, msg in _iter_surface_keystate_messages(hab=hab, start_sn=synced_sn + 1, end_sn=current_sn):
        await send_kf_event(
            surface_url,
            msg,
            destination=destination,
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )

    cache[cache_key] = current_sn


async def send_kf_event(url: str, msg, *, destination: str = "", timeout_ms: int | None = None):
    body, attachment = _split_cesr_message(msg)
    await post_cesr(
        url,
        body=body,
        attachment=attachment,
        destination=destination,
        timeout_ms=timeout_ms or _CONFIG["cesr_timeout_ms"],
    )


async def send_kf_exn(
    hby,
    hab,
    *,
    surface_name: str = "",
    surface_url: str,
    route: str,
    payload: dict,
    destination: str = "",
    expected_sender: str = "",
    expected_ilks: tuple[str, ...] = ("rpy", "exn"),
    timeout_ms: int | None = None,
):
    modules = vaulting.load_modules()
    if surface_name == "account":
        await _ensure_surface_keystate(
            hab,
            surface_name=surface_name,
            surface_url=surface_url,
            destination=destination,
        )
    # f4b9 keri.peer.exchanging exposes exchangeOld (V2 default) / specialExchange;
    # the historical module-level `exchange` was removed. exchangeOld builds a V2
    # `exn` serder from sender/receiver/route/attributes; the exchange end
    # attachment is empty for a plain exn (mirrors Hab.exchange's non-embeds path).
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

    raw_bytes, attachment_header = await post_cesr(
        surface_url,
        body=serder.raw,
        attachment=attachment,
        destination=destination,
        timeout_ms=timeout_ms or _CONFIG["cesr_timeout_ms"],
    )
    return parse_cesr_reply(
        hby,
        raw_bytes,
        attachment_header,
        expected_route=route,
        expected_sender=expected_sender,
        expected_ilks=expected_ilks,
    )
