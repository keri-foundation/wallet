from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urljoin, urlparse
from uuid import uuid4

import transporting
import vaulting


_CONFIG: dict = {}


def configure_runtime(
    *,
    kf_state_key: str,
    kf_state_subdb: str,
    onboarding_auth_namespace: str,
    onboarding_auth_alias_prefix: str,
    account_query_timeout_ms: int,
    witness_registration_timeout_ms: int,
    cesr_timeout_ms: int,
):
    _CONFIG.clear()
    _CONFIG.update(
        kf_state_key=kf_state_key,
        kf_state_subdb=kf_state_subdb,
        onboarding_auth_namespace=onboarding_auth_namespace,
        onboarding_auth_alias_prefix=onboarding_auth_alias_prefix,
        account_query_timeout_ms=account_query_timeout_ms,
        witness_registration_timeout_ms=witness_registration_timeout_ms,
        cesr_timeout_ms=cesr_timeout_ms,
    )


@dataclass
class KfVaultState:
    boot_url: str = ""
    account_aid: str = ""
    account_alias: str = ""
    status: str = ""
    created_at: str = ""
    onboarded_at: str = ""
    witness_profile_code: str = ""
    witness_count: int = 0
    toad: int = 0
    watcher_required: bool = True
    region_id: str = ""
    region_name: str = ""
    boot_server_aid: str = ""
    onboarding_session_id: str = ""
    onboarding_auth_alias: str = ""
    witness_eids: list[str] = field(default_factory=list)
    witness_auths: list[dict] = field(default_factory=list)
    watcher_eid: str = ""
    failure_reason: str = ""


def _kf_state_store(hby):
    return vaulting.load_modules()["koming"].Komer(
        db=hby.db,
        subkey=_CONFIG["kf_state_subdb"],
        klas=KfVaultState,
    )


def _load_kf_state(hby):
    record = _kf_state_store(hby).get(keys=(_CONFIG["kf_state_key"],))
    if record is None:
        return KfVaultState()

    if record.witness_eids is None:
        record.witness_eids = []
    if record.witness_auths is None:
        record.witness_auths = []
    return record


def _save_kf_state(hby, record: KfVaultState):
    if not record.created_at:
        record.created_at = vaulting.now_iso()
    _kf_state_store(hby).pin(keys=(_CONFIG["kf_state_key"],), val=record)
    return record


def _delete_kf_onboarding_hab(hby, alias: str):
    if not alias:
        return
    try:
        hby.deleteHab(alias, ns=_CONFIG["onboarding_auth_namespace"])
    except Exception:
        pass


def _clear_kf_onboarding_session(hby, record: KfVaultState, *, delete_auth_hab: bool):
    auth_alias = str(record.onboarding_auth_alias or "")
    record.onboarding_session_id = ""
    record.onboarding_auth_alias = ""
    _save_kf_state(hby, record)
    if delete_auth_hab and auth_alias:
        _delete_kf_onboarding_hab(hby, auth_alias)


def _load_or_create_kf_onboarding_hab(hby, record: KfVaultState):
    auth_alias = str(record.onboarding_auth_alias or "").strip()
    if auth_alias:
        existing = hby.habByName(auth_alias, ns=_CONFIG["onboarding_auth_namespace"])
        if existing is not None:
            return existing
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The saved KF onboarding session is missing its hidden auth identifier.",
        )

    alias = f"{_CONFIG['onboarding_auth_alias_prefix']}-{uuid4().hex[:12]}"
    return hby.makeHab(
        name=alias,
        ns=_CONFIG["onboarding_auth_namespace"],
        transferable=False,
        icount=1,
        isith="1",
        ncount=0,
        nsith="0",
        wits=[],
        toad=0,
    )


def _has_kf_account(record: KfVaultState):
    return bool(record.account_aid or record.account_alias or record.status)


def _kf_state_view(record: KfVaultState):
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
        "witnessAuthPanels": _witness_auth_panels(record),
        "watcherEid": record.watcher_eid,
        "failureReason": record.failure_reason,
    }


def _select_account_option(snapshot: dict, code: str):
    for option in snapshot["bootstrap"]["accountOptions"]:
        if option["code"] == code:
            return option
    return None


def _session_state(payload: dict) -> str:
    return str(payload.get("state", "") or "")


def _raise_for_closed_session(hby, payload: dict, record: KfVaultState):
    session_state = _session_state(payload)
    if session_state not in {"failed", "cancelled", "expired"}:
        return

    failure_reason = str(payload.get("failure_reason", "") or "").strip()
    _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)
    raise vaulting.RuntimeFault(
        "CONFLICT",
        failure_reason or f"The saved KF onboarding session is {session_state}.",
    )


def _raise_for_failed_provisioning(payload: dict):
    operation = payload.get("session_provision_operation")
    if not isinstance(operation, dict) or str(operation.get("state", "") or "") != "failed":
        return

    raise vaulting.RuntimeFault(
        "CONFLICT",
        str(operation.get("last_error", "") or "Hosted resource provisioning failed for this session."),
    )


def _require_kf_account_hab(hby, record: KfVaultState):
    if record.status != "onboarded" or not record.account_aid:
        raise vaulting.RuntimeFault("CONFLICT", "This vault does not have an onboarded KERI Foundation account yet.")

    hab = hby.habByPre(record.account_aid)
    if hab is None:
        raise vaulting.RuntimeFault(
            "CONFLICT",
            f"Persisted KF account AID '{record.account_aid}' is missing from the local browser vault.",
        )
    return hab


def _create_or_load_kf_account_hab(hby, record: KfVaultState, *, alias: str, requested_account_aid: str):
    existing = None

    if requested_account_aid:
        existing = hby.habByPre(requested_account_aid)
        if existing is None:
            raise vaulting.RuntimeFault(
                "NOT_FOUND",
                f"Selected local account AID '{requested_account_aid}' is missing from this browser vault.",
            )
    elif record.account_aid:
        existing = hby.habByPre(record.account_aid)
        if existing is None:
            raise vaulting.RuntimeFault(
                "CONFLICT",
                f"Persisted KF account AID '{record.account_aid}' is missing from this browser vault.",
            )
    else:
        existing = hby.habByName(alias)

    if existing is not None:
        if not requested_account_aid and not record.account_aid and existing.pre != requested_account_aid:
            raise vaulting.RuntimeFault(
                "CONFLICT",
                f"Identifier alias '{alias}' is already used by another local identifier in this vault.",
            )
        return existing

    return hby.makeHab(
        name=alias,
        algo="randy",
        icount=1,
        isith="1",
        ncount=1,
        nsith="1",
        wits=[],
        toad=0,
    )


def _validate_kf_account_witness_profile(
    hab,
    *,
    witness_eids: list[str],
    toad: int,
    allow_reconfiguration: bool = False,
):
    existing_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    existing_toad = getattr(getattr(getattr(hab, "kever", None), "toader", None), "num", None)
    if (
        existing_wits
        and not allow_reconfiguration
        and (set(existing_wits) != set(witness_eids) or len(existing_wits) != len(witness_eids))
    ):
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The existing permanent account AID does not match the allocated hosted witness pool.",
        )
    if existing_wits and not allow_reconfiguration and existing_toad is not None and toad and existing_toad != toad:
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The existing permanent account AID does not match the allocated witness threshold.",
        )


def _msg_own_event(hab, *, sn: int):
    msg_own_event = getattr(hab, "msgOwnEvent", None)
    if callable(msg_own_event):
        return msg_own_event(sn=sn)
    return hab.makeOwnEvent(sn=sn)


def _msg_own_inception(hab):
    msg_own_inception = getattr(hab, "msgOwnInception", None)
    if callable(msg_own_inception):
        return msg_own_inception()
    return hab.makeOwnInception()


def _iter_hab_kel_messages(hab):
    messages = {}
    for msg in hab.db.clonePreIter(pre=hab.pre):
        raw = bytes(msg)
        serder = vaulting.load_modules()["serdering"].SerderKERI(raw=raw)
        sn = int(getattr(serder, "sn", serder.ked.get("s", 0)) or 0)
        if sn in messages:
            continue
        messages[sn] = raw

    last_sn = int(getattr(getattr(hab, "kever", None), "sn", -1))
    for sn in range(last_sn + 1):
        if sn in messages:
            yield messages[sn]
        else:
            yield bytes(_msg_own_event(hab, sn=sn))


def _totp_code(seed: str, *, period: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(seed.upper(), casefold=True)
    counter = int(time.time() // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def _witness_auth_header(seed: str) -> str:
    return f"{_totp_code(seed)}#{vaulting.now_iso()}"


def _create_totp_uri(secret: str, *, vault_name: str, issuer: str = "KERI Foundation") -> str:
    label = quote(f"{issuer}:{vault_name}", safe="")
    query = urlencode({"secret": secret, "issuer": issuer})
    return f"otpauth://totp/{label}?{query}"


def _witness_rows_from_payload(payload: dict, snapshot: dict):
    witness_rows = []
    for entry in payload.get("witnesses", []):
        if not isinstance(entry, dict):
            continue
        witness_rows.append(
            {
                "eid": str(entry.get("eid", "") or ""),
                "name": str(entry.get("name", "") or ""),
                "witnessUrl": str(entry.get("witness_url") or entry.get("url") or ""),
                "bootUrl": str(entry.get("boot_url", "") or ""),
                "oobi": vaulting.pick_oobi(entry),
                "regionId": str(entry.get("region_id", "") or snapshot["bootstrap"]["regionId"]),
                "regionName": str(entry.get("region_name", "") or snapshot["bootstrap"]["regionName"]),
            }
        )
    return witness_rows


def _watcher_row_from_payload(payload: dict, snapshot: dict):
    raw_watcher = payload.get("watcher")
    if not isinstance(raw_watcher, dict):
        return None

    return {
        "eid": str(raw_watcher.get("eid", "") or ""),
        "name": str(raw_watcher.get("name", "") or ""),
        "watcherUrl": str(raw_watcher.get("watcher_url") or raw_watcher.get("url") or ""),
        "oobi": vaulting.pick_oobi(raw_watcher),
        "regionId": str(raw_watcher.get("region_id", "") or snapshot["bootstrap"]["regionId"]),
        "regionName": str(raw_watcher.get("region_name", "") or snapshot["bootstrap"]["regionName"]),
    }


async def _await_session_resources(
    hby,
    ephemeral_hab,
    *,
    surfaces: transporting.KfSurfaceConfig,
    record: KfVaultState,
    boot_server_aid: str,
    start_payload: dict,
    snapshot: dict,
    option: dict,
):
    payload = start_payload
    watcher_required = bool(snapshot["bootstrap"]["watcherRequired"])
    deadline = time.monotonic() + max(float(_CONFIG["cesr_timeout_ms"]) / 1000.0, 1.0)

    while True:
        _raise_for_closed_session(hby, payload, record)
        _raise_for_failed_provisioning(payload)
        witness_rows = _witness_rows_from_payload(payload, snapshot)
        watcher_row = _watcher_row_from_payload(payload, snapshot)
        resources_complete = len(witness_rows) == option["witnessCount"] and (
            not watcher_required or watcher_row is not None
        )
        if resources_complete:
            return boot_server_aid, payload, witness_rows, watcher_row

        if time.monotonic() >= deadline:
            raise vaulting.RuntimeFault(
                "TIMEOUT",
                "Timed out waiting for hosted witness and watcher provisioning.",
            )

        await asyncio.sleep(0.5)
        destination = transporting.kf_surface_destination(
            surfaces,
            surface_name="onboarding",
            boot_server_aid=boot_server_aid,
        )
        status_reply = await transporting.send_kf_exn(
            hby,
            ephemeral_hab,
            surface_name="onboarding",
            surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
            route="/onboarding/session/status",
            payload={"session_id": record.onboarding_session_id},
            destination=destination,
            expected_sender=destination or boot_server_aid or "",
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )
        boot_server_aid = status_reply["sender"] or boot_server_aid
        payload = status_reply["payload"]


def _qr_svg_data_uri(value: str) -> str:
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(value)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    module_count = len(matrix)
    size = module_count * 8
    rects = []
    for y, row in enumerate(matrix):
        for x, filled in enumerate(row):
            if not filled:
                continue
            rects.append(f'<rect x="{x * 8}" y="{y * 8}" width="8" height="8"/>')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        'shape-rendering="crispEdges" role="img" aria-hidden="true">'
        f'<rect width="{size}" height="{size}" fill="#f8f9ff"/>'
        '<g fill="#111827">'
        f'{"".join(rects)}'
        "</g></svg>"
    )
    return f"data:image/svg+xml;utf8,{quote(svg, safe='')}"


def _witness_auth_panels(record: KfVaultState):
    groups: dict[str, dict] = {}
    order: list[str] = []

    for entry in list(record.witness_auths or []):
        if not isinstance(entry, dict):
            continue
        seed = str(entry.get("totpSeed", "") or "").strip()
        eid = str(entry.get("eid", "") or "").strip()
        if not seed or not eid:
            continue

        group = groups.get(seed)
        if group is None:
            group = {"totpSeed": seed, "eids": [], "names": []}
            groups[seed] = group
            order.append(seed)

        group["eids"].append(eid)
        group["names"].append(str(entry.get("name", "") or ""))

    panels = []
    controller_alias = record.account_alias or "Account"
    controller_aid = record.account_aid or ""

    for index, seed in enumerate(order, start=1):
        group = groups[seed]
        batch_mode = len(group["eids"]) > 1
        title = "Batch TOTP" if batch_mode else (group["names"][0] or f"KF Witness {group['eids'][0][:12]}")
        vault_name = f"KF-Batch-{controller_aid[:8]}" if batch_mode else f"KF-{group['eids'][0][:12]}"
        uri = _create_totp_uri(group["totpSeed"], vault_name=vault_name)
        panels.append(
            {
                "number": str(index),
                "title": title,
                "description": (
                    f"Shared across {len(group['eids'])} hosted witnesses"
                    if batch_mode
                    else f"Use for witness {group['eids'][0]}"
                ),
                "controllerAlias": controller_alias,
                "controllerAid": controller_aid,
                "witnessEids": list(group["eids"]),
                "witnessNames": [name for name in group["names"] if name],
                "uri": uri,
                "qrSvgDataUri": _qr_svg_data_uri(uri),
            }
        )

    return panels


def _controller_oobi_url(account_aid: str, witnesses: list[dict]):
    for witness in witnesses:
        base = str(witness.get("witnessUrl", "") or "").strip()
        if base:
            return urljoin(f"{base.rstrip('/')}/", f"/oobi/{account_aid}/controller")
        oobi = str(witness.get("oobi", "") or "").strip()
        parsed = urlparse(oobi)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/oobi/{account_aid}/controller"
    raise vaulting.RuntimeFault("CONFLICT", "Allocated witnesses did not provide a usable controller OOBI base URL.")


def _remove_remote_ids(organizer, eids: list[str]):
    for eid in eids:
        if not eid:
            continue
        try:
            organizer.rem(eid)
        except Exception:
            continue


async def _resolve_kf_oobi(hby, organizer, *, url: str, display_url: str, alias: str, expected_aid: str = ""):
    modules = vaulting.load_modules()
    vaulting.clear_oobi_tracking(hby, url)

    oobi_record = modules["recording"].OobiRecord(date=modules["oobiing"].nowIso8601())
    if alias:
        oobi_record.oobialias = alias

    hby.db.oobis.pin(keys=(url,), val=oobi_record)
    try:
        roobi = await vaulting.await_resolution(
            hby,
            modules["oobiing"].Oobiery(
                hby=hby,
                clienter=transporting.BrowserClienter(),
            ),
            url,
            expected_aid=expected_aid,
        )
        update = {"oobi": display_url}
        if alias:
            update["alias"] = alias
        organizer.update(roobi.cid, update)
        return roobi.cid
    except Exception:
        vaulting.clear_oobi_tracking(hby, url)
        raise


def _encode_multipart_form(fields: dict[str, str]):
    boundary = f"----fortweb-{uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n")
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        chunks.append(f"{value}\r\n")
    chunks.append(f"--{boundary}--\r\n")
    body = "".join(chunks)
    return body, boundary


def _get_witness_receipts(db, pre: str, said: str):
    dgkey = vaulting.dg_key(pre, said)
    getter = getattr(db, "getWigs", None)
    if getter is not None:
        return getter(dgkey)
    wigs = getattr(db, "wigs", None)
    if wigs is None:
        return []
    candidates = (
        dgkey,
        (pre, said),
        (pre.encode("utf-8"), said.encode("utf-8")),
    )
    for keys in candidates:
        rows = wigs.get(keys=keys) or []
        if rows:
            return rows
    return []


def _get_non_witness_receipts(db, pre: str, said: str):
    rcts = getattr(db, "rcts", None)
    if rcts is None:
        return []

    dgkey = vaulting.dg_key(pre, said)
    candidates = (
        dgkey,
        (pre, said),
        (pre.encode("utf-8"), said.encode("utf-8")),
    )
    for keys in candidates:
        rows = rcts.get(keys=keys) or []
        if rows:
            return rows
    return []


def _promote_non_witness_receipt(hab, witness: dict, said: str):
    rows = _get_non_witness_receipts(hab.db, hab.pre, said)
    if not rows:
        return False

    event = hab.db.evts.get(keys=(hab.pre.encode("utf-8"), said.encode("utf-8")))
    if event is None:
        return False

    witness_eid = str(witness.get("eid", "") or "")
    wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    if not witness_eid or witness_eid not in wits:
        return False

    modules = vaulting.load_modules()
    index = wits.index(witness_eid)
    for prefixer, cigar in rows:
        if getattr(prefixer, "qb64", "") != witness_eid:
            continue
        cigar.verfer = modules["eventing"].Verfer(qb64=prefixer.qb64)
        if not cigar.verfer.verify(cigar.raw, event.raw):
            continue

        wiger = modules["eventing"].Siger(raw=cigar.raw, index=index, verfer=cigar.verfer)
        return bool(hab.db.wigs.add(keys=vaulting.dg_key(hab.pre, said), val=wiger))

    return False


def _ingest_witness_receipt_fallback(hab, witness: dict, raw_bytes: bytes):
    modules = vaulting.load_modules()
    receipt = modules["serdering"].SerderKERI(raw=raw_bytes)
    if receipt.ilk != "rct":
        return False

    said = receipt.said
    event = hab.db.evts.get(keys=(hab.pre.encode("utf-8"), said.encode("utf-8")))
    if event is None:
        return False

    witness_eid = str(witness.get("eid", "") or "")
    wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    if witness_eid not in wits:
        return False

    ims = bytearray(raw_bytes[receipt.size:])
    if not ims:
        return False

    index = wits.index(witness_eid)
    version = transporting._kf_reply_parser_version(raw_bytes)
    counter = modules["eventing"].Counter(
        qb64b=ims,
        strip=True,
        version=version,
    )

    attachment_groups = {
        modules["eventing"].Codens.AttachmentGroup,
        modules["eventing"].Codens.BigAttachmentGroup,
    }
    while counter.name in attachment_groups:
        group_size = counter.byteCount()
        if len(ims) < group_size:
            return False
        ims = bytearray(ims[:group_size])
        counter = modules["eventing"].Counter(
            qb64b=ims,
            strip=True,
            version=version,
        )
        if counter.name == modules["eventing"].Codens.KERIACDCGenusVersion:
            version = modules["eventing"].Counter.b64ToVer(counter.countToB64(l=3))
            counter = modules["eventing"].Counter(
                qb64b=ims,
                strip=True,
                version=version,
            )

    added = False
    if counter.name == modules["eventing"].Codens.WitnessIdxSigs:
        if version.major >= 2:
            group_size = counter.byteCount()
            if len(ims) < group_size:
                return False
            group = ims[:group_size]
            wigers = []
            while group:
                wigers.append(
                    modules["eventing"].Siger(
                        qb64b=group,
                        strip=True,
                        version=version,
                    )
                )
        else:
            wigers = [
                modules["eventing"].Siger(
                    qb64b=ims,
                    strip=True,
                    version=version,
                )
                for _ in range(counter.count)
            ]

        for wiger in wigers:
            if wiger.index != index:
                continue
            verfer = modules["eventing"].Verfer(qb64=witness_eid)
            if not verfer.verify(wiger.raw, event.raw):
                continue
            wiger.verfer = verfer
            added = hab.db.wigs.add(keys=vaulting.dg_key(hab.pre, said), val=wiger) or added

        return added

    if counter.name == modules["eventing"].Codens.NonTransReceiptCouples:
        if version.major >= 2:
            group_size = counter.byteCount()
            if len(ims) < group_size:
                return False
            ims = ims[:group_size]
            remaining = bool(ims)
        else:
            remaining = counter.count

        while remaining:
            verfer = modules["eventing"].Verfer(qb64b=ims, strip=True, version=version)
            cigar = modules["eventing"].Cigar(qb64b=ims, strip=True, version=version)
            if verfer.qb64 == witness_eid and verfer.verify(cigar.raw, event.raw):
                wiger = modules["eventing"].Siger(raw=cigar.raw, index=index, verfer=verfer)
                added = hab.db.wigs.add(keys=vaulting.dg_key(hab.pre, said), val=wiger) or added
            remaining = bool(ims) if version.major >= 2 else remaining - 1

    return added


def _receipt_state_detail(hab, witness: dict, said: str):
    modules = vaulting.load_modules()
    preb = hab.pre.encode("utf-8")
    saidb = said.encode("utf-8")
    event = hab.db.evts.get(keys=(preb, saidb))
    kels_last = hab.db.kels.getLast(keys=preb, on=hab.kever.sn)
    event_wits = [
        getattr(prefixer, "qb64", str(prefixer))
        for prefixer in (hab.db.wits.get(keys=(preb, saidb)) or [])
    ]
    current_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    pwes = hab.db.pwes.get(keys=preb, on=hab.kever.sn) or []
    uwes = hab.db.uwes.get(keys=(hab.pre,), on=hab.kever.sn) or []
    sn_key = modules["eventing"].Number(num=hab.kever.sn, code=modules["eventing"].NumDex.Huge).qb64
    ures = hab.db.ures.get(keys=(hab.pre, sn_key)) or []
    rcts = _get_non_witness_receipts(hab.db, hab.pre, said)
    wigs = _get_witness_receipts(hab.db, hab.pre, said)
    return (
        f"event_present={event is not None} "
        f"kels_last={kels_last or '-'} "
        f"event_wits={event_wits} "
        f"current_wits={current_wits} "
        f"rcts={len(rcts)} "
        f"wigs={len(wigs)} "
        f"pwes={len(pwes)} "
        f"uwes={len(uwes)} "
        f"ures={len(ures)} "
        f"witness={str(witness.get('eid', '') or '')}"
    )


async def _register_with_witness(hab, witness: dict):
    kel = bytearray()
    for msg in hab.db.clonePreIter(pre=hab.pre):
        kel.extend(msg)

    form_fields = {
        "kel": kel.decode("utf-8"),
    }

    if hab.kever.delegated:
        delkel = bytearray()
        for msg in hab.db.clonePreIter(hab.kever.delpre):
            delkel.extend(msg)
        form_fields["delkel"] = delkel.decode("utf-8")

    body, boundary = _encode_multipart_form(form_fields)

    headers = {
        transporting.CESR_DESTINATION_HEADER: witness["eid"],
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body.encode("utf-8"))),
    }
    witness_url = urljoin(f"{witness['witnessUrl'].rstrip('/')}/", "/aids")
    response = await transporting.fetch_response(
        witness_url,
        method="POST",
        headers=headers,
        body=body,
        timeout_ms=_CONFIG["witness_registration_timeout_ms"],
    )
    raw_text = await transporting.response_text(response)
    if int(response.status) >= 400:
        detail = raw_text.strip() or f"HTTP {response.status}"
        raise vaulting.RuntimeFault("NETWORK_ERROR", f"{detail} from {witness_url}")

    try:
        data = json.loads(raw_text or "{}")
    except ValueError as exc:
        raise vaulting.RuntimeFault("BAD_RESPONSE", f"Witness {witness['eid']} returned malformed JSON.") from exc

    totp = str(data.get("totp", "") or "")
    if not totp:
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"Witness {witness['eid']} response did not include an encrypted TOTP seed.",
        )

    encrypted = vaulting.load_modules()["coring"].Matter(qb64=totp)
    decrypted = vaulting.load_modules()["coring"].Matter(qb64=hab.decrypt(ser=encrypted.raw))
    return {
        "eid": witness["eid"],
        "totpSeed": decrypted.raw.decode("utf-8"),
        "oobi": str(data.get("oobi") or witness.get("oobi") or ""),
        "witnessUrl": witness["witnessUrl"],
        "name": witness.get("name", ""),
    }


async def _submit_witness_rotation_receipt(hab, witness: dict, auth_header: str, msg: bytes):
    body, attachment = transporting._split_cesr_message(msg)
    headers = {
        "Content-Type": transporting.CESR_CONTENT_TYPE,
        "Content-Length": str(len(body)),
        transporting.CESR_DESTINATION_HEADER: witness["eid"],
        "Authorization": auth_header,
    }
    if attachment:
        headers[transporting.CESR_ATTACHMENT_HEADER] = bytes(attachment).decode("utf-8")

    witness_url = urljoin(f"{witness['witnessUrl'].rstrip('/')}/", "/receipts")
    response = await transporting.fetch_response(
        witness_url,
        method="POST",
        headers=headers,
        body=bytes(body).decode("utf-8"),
        timeout_ms=_CONFIG["witness_registration_timeout_ms"],
    )
    raw_bytes = await transporting.response_bytes(response)
    if int(response.status) != 200:
        detail = raw_bytes.decode("utf-8", errors="ignore").strip() or f"HTTP {response.status}"
        raise vaulting.RuntimeFault(
            "NETWORK_ERROR",
            f"Witness {witness['eid']} rejected the rotation event: {detail}",
        )

    hab.psr.parseOne(
        ims=bytearray(raw_bytes),
        version=transporting._kf_reply_parser_version(raw_bytes),
    )
    if getattr(hab.psr, "kvy", None) is not None:
        hab.psr.kvy.processEscrows()
    if not _get_witness_receipts(hab.db, hab.pre, hab.kever.serder.said):
        _promote_non_witness_receipt(hab, witness, hab.kever.serder.said)
    if not _get_witness_receipts(hab.db, hab.pre, hab.kever.serder.said):
        _ingest_witness_receipt_fallback(hab, witness, raw_bytes)


async def _rotate_kf_account_to_witnesses(
    hab,
    witnesses: list[dict],
    *,
    toad: int,
    allow_reconfiguration: bool = False,
):
    allocated_wits = [witness["eid"] for witness in witnesses]
    current_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    current_toad = getattr(getattr(getattr(hab, "kever", None), "toader", None), "num", None)

    already_configured = current_wits == allocated_wits and (current_toad is None or current_toad == toad)
    if not already_configured:
        if current_wits and not allow_reconfiguration:
            raise vaulting.RuntimeFault(
                "CONFLICT",
                "The existing permanent account AID already has a different witness configuration.",
            )

        hab.rotate(
            toad=toad,
            cuts=[witness for witness in current_wits if witness not in allocated_wits],
            adds=[witness for witness in allocated_wits if witness not in current_wits],
        )

    rotation_msg = bytes(_msg_own_event(hab, sn=hab.kever.sn))

    for witness in witnesses:
        auth_header = _witness_auth_header(str(witness.get("totpSeed", "") or ""))
        await _submit_witness_rotation_receipt(hab, witness, auth_header, rotation_msg)

    wigs = _get_witness_receipts(hab.db, hab.pre, hab.kever.serder.said)
    if len(wigs) < hab.kever.toader.num:
        detail = _receipt_state_detail(hab, witness, hab.kever.serder.said)
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"Insufficient witness receipts after rotation: got {len(wigs)}, need {hab.kever.toader.num}. {detail}",
        )

    # Match native Receiptor: each witness needs its peers' receipts and endpoints.
    modules = vaulting.load_modules()
    event = hab.kever.serder
    event_wits = list(hab.kever.wits)
    receipts = {event_wits[wiger.index]: wiger for wiger in wigs}
    for witness in witnesses:
        peers = [eid for eid in event_wits if eid != witness["eid"] and eid in receipts]
        if not peers:
            continue
        for eid in peers:
            for scheme, _ in hab.fetchUrls(eid=eid).firsts():
                locations = hab.loadLocScheme(eid=eid, scheme=scheme, gvrsn=event.pvrsn)
                await _send_witness_message(witness, locations)
        receipt = modules["eventing"].receipt(
            pre=hab.pre, sn=event.sn, said=event.said,
            version=event.pvrsn, kind=event.kind,
        )
        msg = modules["eventing"].messagize(
            serder=receipt, wigers=[receipts[eid] for eid in peers],
            framed=True, gvrsn=event.pvrsn,
        )
        await _send_witness_message(witness, msg)


async def _send_witness_message(witness: dict, msg):
    # The witness POST endpoint accepts one event with attachments in its header.
    body, attachment = transporting._split_cesr_message(msg)
    headers = {
        "Content-Type": transporting.CESR_CONTENT_TYPE,
        "Content-Length": str(len(body)),
        transporting.CESR_DESTINATION_HEADER: witness["eid"],
    }
    if attachment:
        headers[transporting.CESR_ATTACHMENT_HEADER] = attachment.decode("utf-8")
    response = await transporting.fetch_response(
        witness["witnessUrl"], method="POST", headers=headers,
        body=body.decode("utf-8"), timeout_ms=_CONFIG["cesr_timeout_ms"],
    )
    if int(response.status) >= 400:
        detail = await transporting.response_text(response)
        raise vaulting.RuntimeFault(
            "NETWORK_ERROR",
            f"Witness {witness['eid']} rejected the receipt propagation: {detail or response.status}",
        )


async def _send_direct_cesr(url: str, msg, *, destination: str = "", method: str = "PUT"):
    await transporting.post_cesr_stream(
        url,
        ims=bytes(msg),
        destination=destination,
        method=method,
        timeout_ms=_CONFIG["cesr_timeout_ms"],
    )


async def _introduce_account_to_watcher(hab, watcher: dict, witnesses: list[dict]):
    watcher_eid = str(watcher.get("eid", "") or "")
    watcher_url = str(watcher.get("watcherUrl", "") or watcher.get("url", "") or "")
    if not watcher_eid or not watcher_url:
        raise vaulting.RuntimeFault("CONFLICT", "Hosted watcher allocation did not include a usable endpoint.")

    for witness in witnesses:
        locations = hab.loadLocScheme(eid=witness["eid"], gvrsn=hab.kever.serder.pvrsn)
        if locations:
            await _send_direct_cesr(watcher_url, locations, destination=watcher_eid)

    ender = hab.db.ends.get(keys=(hab.pre, "watcher", watcher_eid))
    if not ender or not ender.allowed:
        end_role = hab.reply(
            route="/end/role/add",
            data=dict(cid=hab.pre, role="watcher", eid=watcher_eid),
        )
        hab.psr.parseOne(ims=bytes(end_role))
        await _send_direct_cesr(watcher_url, end_role, destination=watcher_eid)

    for msg in _iter_hab_kel_messages(hab):
        await _send_direct_cesr(watcher_url, msg, destination=watcher_eid)

    add_reply = hab.reply(
        route=f"/watcher/{watcher_eid}/add",
        data=dict(
            cid=hab.pre,
            oid=hab.pre,
            oobi=_controller_oobi_url(hab.pre, witnesses),
        ),
    )
    hab.psr.parseOne(ims=bytes(add_reply))
    await _send_direct_cesr(watcher_url, add_reply, destination=watcher_eid)


def _local_connection_status(hby, organizer, aid: str):
    if aid in hby.kevers:
        return "Connected", "success"
    if organizer.get(aid) is not None:
        return "Stored", "info"
    return "Pending local connect", "warning"


async def _list_kf_account_witnesses(hby, organizer, record: KfVaultState, surfaces: transporting.KfSurfaceConfig):
    hab = _require_kf_account_hab(hby, record)
    destination = transporting.kf_surface_destination(
        surfaces,
        surface_name="account",
        boot_server_aid=record.boot_server_aid,
    )
    reply = await transporting.send_kf_exn(
        hby,
        hab,
        surface_name="account",
        surface_url=transporting.require_kf_surface_url(surfaces, "account"),
        route="/account/witnesses",
        payload={"account_aid": record.account_aid},
        destination=destination,
        expected_sender=destination or record.boot_server_aid,
        timeout_ms=_CONFIG["account_query_timeout_ms"],
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
                "oobi": vaulting.pick_oobi(entry),
                "hostedStatus": str(entry.get("status", "") or "allocated"),
                "localStatus": local_status,
                "localStatusTone": local_tone,
                "createdAt": str(entry.get("created_at", "") or ""),
            }
        )
    return rows


async def _list_kf_account_watchers(hby, organizer, record: KfVaultState, surfaces: transporting.KfSurfaceConfig):
    hab = _require_kf_account_hab(hby, record)
    destination = transporting.kf_surface_destination(
        surfaces,
        surface_name="account",
        boot_server_aid=record.boot_server_aid,
    )
    reply = await transporting.send_kf_exn(
        hby,
        hab,
        surface_name="account",
        surface_url=transporting.require_kf_surface_url(surfaces, "account"),
        route="/account/watchers",
        payload={"account_aid": record.account_aid},
        destination=destination,
        expected_sender=destination or record.boot_server_aid,
        timeout_ms=_CONFIG["account_query_timeout_ms"],
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
                "oobi": vaulting.pick_oobi(entry),
                "hostedStatus": str(entry.get("status", "") or "created"),
                "localStatus": local_status,
                "localStatusTone": local_tone,
                "createdAt": str(entry.get("created_at", "") or ""),
            }
        )
    return rows


async def _refresh_kf_watcher_status(
    hby,
    organizer,
    record: KfVaultState,
    watcher_id: str,
    surfaces: transporting.KfSurfaceConfig,
):
    hab = _require_kf_account_hab(hby, record)
    destination = transporting.kf_surface_destination(
        surfaces,
        surface_name="account",
        boot_server_aid=record.boot_server_aid,
    )
    reply = await transporting.send_kf_exn(
        hby,
        hab,
        surface_name="account",
        surface_url=transporting.require_kf_surface_url(surfaces, "account"),
        route="/account/watchers/status",
        payload={"account_aid": record.account_aid, "watcher_eid": watcher_id},
        destination=destination,
        expected_sender=destination or record.boot_server_aid,
        timeout_ms=_CONFIG["account_query_timeout_ms"],
    )

    watcher = reply["payload"].get("watcher", {})
    if not isinstance(watcher, dict):
        raise vaulting.RuntimeFault("BAD_RESPONSE", "KF watcher status reply was malformed.")

    local_status, local_tone = _local_connection_status(hby, organizer, str(watcher.get("eid", "") or watcher_id))
    return {
        "eid": str(watcher.get("eid", "") or watcher_id),
        "name": str(watcher.get("name", "") or ""),
        "url": str(watcher.get("url") or watcher.get("watcher_url") or ""),
        "regionId": str(watcher.get("region_id", "") or ""),
        "regionName": str(watcher.get("region_name", "") or ""),
        "oobi": vaulting.pick_oobi(watcher),
        "hostedStatus": str(watcher.get("status", "") or "created"),
        "localStatus": local_status,
        "localStatusTone": local_tone,
        "createdAt": str(watcher.get("created_at", "") or ""),
    }


async def _run_kf_onboarding(
    hby,
    organizer,
    *,
    surfaces: transporting.KfSurfaceConfig,
    alias: str,
    witness_profile_code: str,
    account_aid: str = "",
):
    snapshot = await transporting.fetch_bootstrap_snapshot(surfaces)
    option = _select_account_option(snapshot, witness_profile_code)
    if option is None:
        raise vaulting.RuntimeFault(
            "VALIDATION",
            f"Witness profile '{witness_profile_code}' is not supported by the current KF bootstrap config.",
        )

    record = _load_kf_state(hby)
    if record.status == "onboarded" and record.account_aid:
        raise vaulting.RuntimeFault("CONFLICT", "This vault already has an onboarded KERI Foundation account.")

    record.boot_url = snapshot["bootUrl"]
    record.account_alias = alias
    record.status = "pending_onboarding"
    record.witness_profile_code = witness_profile_code
    record.failure_reason = ""
    record.witness_auths = []

    start_reply = None
    witness_rows = []
    watcher_row = None
    resolved_remote_ids = []
    boot_server_aid = record.boot_server_aid
    start_payload = {}
    account_hab = _create_or_load_kf_account_hab(
        hby,
        record,
        alias=alias,
        requested_account_aid=str(account_aid or "").strip(),
    )
    record.account_aid = account_hab.pre
    ephemeral_hab = _load_or_create_kf_onboarding_hab(hby, record)
    record.onboarding_auth_alias = getattr(ephemeral_hab, "name", "")
    _save_kf_state(hby, record)

    try:
        if record.onboarding_session_id:
            destination = transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            )
            try:
                start_reply = await transporting.send_kf_exn(
                    hby,
                    ephemeral_hab,
                    surface_name="onboarding",
                    surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
                    route="/onboarding/session/status",
                    payload={"session_id": record.onboarding_session_id},
                    destination=destination,
                    expected_sender=destination or boot_server_aid or "",
                    timeout_ms=_CONFIG["cesr_timeout_ms"],
                )
            except vaulting.RuntimeFault as exc:
                if "Session not found" not in str(exc):
                    raise
                record.onboarding_session_id = ""
                _save_kf_state(hby, record)
            else:
                boot_server_aid = start_reply["sender"] or boot_server_aid
                start_payload = start_reply["payload"]
                session_state = str(start_payload.get("state", "") or "")
                if start_payload.get("account_aid") and start_payload["account_aid"] != account_hab.pre:
                    raise vaulting.RuntimeFault(
                        "CONFLICT",
                        "The saved KF onboarding session is bound to a different permanent account AID.",
                    )
                if session_state in {"failed", "cancelled", "expired"}:
                    failure_reason = str(start_payload.get("failure_reason", "") or "").strip()
                    _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)
                    raise vaulting.RuntimeFault(
                        "CONFLICT",
                        failure_reason or f"The saved KF onboarding session is {session_state}.",
                    )

        if not record.onboarding_session_id:
            destination = transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            )
            start_reply = await transporting.send_kf_exn(
                hby,
                ephemeral_hab,
                surface_name="onboarding",
                surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
                route="/onboarding/session/start",
                payload={
                    "account_aid": account_hab.pre,
                    "account_alias": alias,
                    "chosen_profile_code": witness_profile_code,
                    "region_id": snapshot["bootstrap"]["regionId"],
                    "watcher_required": snapshot["bootstrap"]["watcherRequired"],
                },
                destination=destination,
                timeout_ms=_CONFIG["cesr_timeout_ms"],
            )
            boot_server_aid = start_reply["sender"] or boot_server_aid
            start_payload = start_reply["payload"]
        record.boot_server_aid = boot_server_aid
        record.onboarding_session_id = str(start_payload.get("session_id", "") or "")
        record.onboarding_auth_alias = getattr(ephemeral_hab, "name", "")
        _save_kf_state(hby, record)

        if not record.onboarding_session_id:
            raise vaulting.RuntimeFault("BAD_RESPONSE", "KF bootstrap did not return an onboarding session id.")

        boot_server_aid, start_payload, witness_rows, watcher_row = await _await_session_resources(
            hby,
            ephemeral_hab,
            surfaces=surfaces,
            record=record,
            boot_server_aid=boot_server_aid,
            start_payload=start_payload,
            snapshot=snapshot,
            option=option,
        )
        record.boot_server_aid = boot_server_aid
        _save_kf_state(hby, record)

        if len(witness_rows) != option["witnessCount"]:
            raise vaulting.RuntimeFault(
                "BAD_RESPONSE",
                "KF bootstrap returned a witness pool that does not match the selected witness profile.",
            )
        if snapshot["bootstrap"]["watcherRequired"] and watcher_row is None:
            raise vaulting.RuntimeFault(
                "BAD_RESPONSE",
                "KF bootstrap did not return the required hosted watcher allocation.",
            )

        _validate_kf_account_witness_profile(
            account_hab,
            witness_eids=[witness["eid"] for witness in witness_rows],
            toad=int(start_payload.get("toad", 0) or option["toad"]),
            allow_reconfiguration=not bool(str(account_aid or "").strip()),
        )

        for witness in witness_rows:
            registration = await _register_with_witness(account_hab, witness)
            witness["oobi"] = registration["oobi"] or witness["oobi"]
            witness["totpSeed"] = registration["totpSeed"]
            resolved_remote_ids.append(
                await _resolve_kf_oobi(
                    hby,
                    organizer,
                    url=witness["oobi"],
                    display_url=witness["oobi"],
                    alias=witness["name"] or f"KF Witness {witness['eid'][:12]}",
                    expected_aid=witness["eid"],
                )
            )

        await _rotate_kf_account_to_witnesses(
            account_hab,
            witness_rows,
            toad=int(start_payload.get("toad", 0) or option["toad"]),
            allow_reconfiguration=not bool(str(account_aid or "").strip()),
        )

        if watcher_row is not None and watcher_row["oobi"]:
            resolved_remote_ids.append(
                await _resolve_kf_oobi(
                    hby,
                    organizer,
                    url=watcher_row["oobi"],
                    display_url=watcher_row["oobi"],
                    alias=watcher_row["name"] or f"KF Watcher {watcher_row['eid'][:12]}",
                    expected_aid=watcher_row["eid"],
                )
            )
            await _introduce_account_to_watcher(account_hab, watcher_row, witness_rows)

        await transporting.send_kf_exn(
            hby,
            ephemeral_hab,
            surface_name="onboarding",
            surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
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
            destination=transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            ),
            expected_sender=transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            ) or boot_server_aid,
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )
        await transporting.send_kf_exn(
            hby,
            ephemeral_hab,
            surface_name="onboarding",
            surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
            route="/onboarding/complete",
            payload={
                "session_id": str(start_payload.get("session_id", "") or ""),
                "account_aid": account_hab.pre,
            },
            destination=transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            ),
            expected_sender=transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            ) or boot_server_aid,
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )
    except Exception as exc:
        allocated_wits = [witness["eid"] for witness in witness_rows]
        current_wits = list(getattr(getattr(account_hab, "kever", None), "wits", []) or []) if account_hab is not None else []
        preserve_session = bool(allocated_wits) and current_wits == allocated_wits

        if start_reply is not None and not preserve_session:
            session_id = str(start_reply["payload"].get("session_id", "") or "")
            if session_id:
                try:
                    destination = transporting.kf_surface_destination(
                        surfaces,
                        surface_name="onboarding",
                        boot_server_aid=boot_server_aid,
                    )
                    await transporting.send_kf_exn(
                        hby,
                        ephemeral_hab,
                        surface_name="onboarding",
                        surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
                        route="/onboarding/cancel",
                        payload={
                            "session_id": session_id,
                            "account_aid": getattr(account_hab, "pre", "") if account_hab is not None else "",
                            "reason": "client_abandoned",
                        },
                        destination=destination,
                        expected_sender=destination or boot_server_aid or "",
                        timeout_ms=_CONFIG["cesr_timeout_ms"],
                    )
                except Exception:
                    pass

        if not preserve_session:
            _remove_remote_ids(organizer, resolved_remote_ids)
            _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)

        record.status = "pending_onboarding" if preserve_session else "failed"
        record.failure_reason = str(exc)
        record.boot_server_aid = boot_server_aid or record.boot_server_aid
        if preserve_session and start_payload.get("session_id"):
            record.onboarding_session_id = str(start_payload.get("session_id", "") or "")
            record.onboarding_auth_alias = getattr(ephemeral_hab, "name", "") or record.onboarding_auth_alias
        _save_kf_state(hby, record)
        try:
            await vaulting.persist_and_reload()
        except Exception as persistence_error:
            raise vaulting.RuntimeFault(
                "RUNTIME_ERROR",
                "KF onboarding failed and its failure state could not be persisted: "
                f"{persistence_error}",
            ) from exc
        if isinstance(exc, vaulting.RuntimeFault):
            raise
        raise vaulting.RuntimeFault("RUNTIME_ERROR", f"KF onboarding failed: {exc}") from exc

    record.account_aid = account_hab.pre
    record.account_alias = alias
    record.status = "onboarded"
    record.onboarded_at = vaulting.now_iso()
    record.witness_profile_code = witness_profile_code
    record.witness_count = int(start_reply["payload"].get("witness_count", 0) or len(witness_rows))
    record.toad = int(start_reply["payload"].get("toad", 0) or option["toad"])
    record.watcher_required = snapshot["bootstrap"]["watcherRequired"]
    record.region_id = str(start_reply["payload"].get("region_id", "") or snapshot["bootstrap"]["regionId"])
    record.region_name = str(start_reply["payload"].get("region_name", "") or snapshot["bootstrap"]["regionName"])
    record.boot_server_aid = boot_server_aid
    record.witness_eids = [witness["eid"] for witness in witness_rows]
    record.witness_auths = [
        {
            "eid": str(witness.get("eid", "") or ""),
            "name": str(witness.get("name", "") or ""),
            "totpSeed": str(witness.get("totpSeed", "") or ""),
        }
        for witness in witness_rows
        if str(witness.get("totpSeed", "") or "")
    ]
    record.watcher_eid = watcher_row["eid"] if watcher_row is not None else ""
    record.failure_reason = ""
    _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)

    reopened = await vaulting.persist_and_reload()
    reopened_record = _load_kf_state(reopened["hby"])
    reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
    return {
        "account": _kf_state_view(reopened_record),
        "witnesses": await _list_kf_account_witnesses(
            reopened["hby"],
            reopened_organizer,
            reopened_record,
            surfaces,
        ),
        "watchers": await _list_kf_account_watchers(
            reopened["hby"],
            reopened_organizer,
            reopened_record,
            surfaces,
        ),
    }


async def dispatch(method: str, params: dict):
    state = vaulting.require_open_state(vaulting.require_text(params.get("vaultId"), field="Vault"))
    modules = state["modules"]
    hby = state["hby"]
    organizer = modules["organizing"].Organizer(hby=hby)

    if method == "kf.bootstrap.get":
        record = _load_kf_state(hby)
        raw_surface_config = params.get("surfaceConfig")
        surface_config = transporting.coerce_kf_surface_config(
            raw_surface_config,
            fallback_boot_url=record.boot_url,
        )

        try:
            snapshot = await transporting.fetch_bootstrap_snapshot(surface_config)
        except vaulting.RuntimeFault as exc:
            return {
                "bootUrl": surface_config.boot_url or ("" if isinstance(raw_surface_config, dict) else record.boot_url),
                "connection": {"ok": False, "error": str(exc)},
                "bootstrap": None,
                "surfaces": {
                    "onboardingUrl": surface_config.onboarding_url,
                    "accountUrl": surface_config.account_url,
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
        alias = vaulting.require_text(params.get("alias"), field="Account alias")
        witness_profile_code = vaulting.require_text(params.get("witnessProfileCode"), field="Witness profile")
        record = _load_kf_state(hby)
        surface_config = transporting.coerce_kf_surface_config(
            params.get("surfaceConfig"),
            fallback_boot_url=record.boot_url,
        )
        account_aid = str(params.get("accountAid") or "").strip()
        return await _run_kf_onboarding(
            hby,
            organizer,
            surfaces=surface_config,
            alias=alias,
            witness_profile_code=witness_profile_code,
            account_aid=account_aid,
        )

    if method == "kf.account.witnesses.list":
        record = _load_kf_state(hby)
        surface_config = transporting.coerce_kf_surface_config(
            params.get("surfaceConfig"),
            fallback_boot_url=record.boot_url,
        )
        return {
            "account": _kf_state_view(record),
            "witnesses": await _list_kf_account_witnesses(hby, organizer, record, surface_config),
        }

    if method == "kf.account.watchers.list":
        record = _load_kf_state(hby)
        surface_config = transporting.coerce_kf_surface_config(
            params.get("surfaceConfig"),
            fallback_boot_url=record.boot_url,
        )
        return {
            "account": _kf_state_view(record),
            "watchers": await _list_kf_account_watchers(hby, organizer, record, surface_config),
        }

    if method == "kf.account.watchers.status":
        record = _load_kf_state(hby)
        surface_config = transporting.coerce_kf_surface_config(
            params.get("surfaceConfig"),
            fallback_boot_url=record.boot_url,
        )
        watcher_id = vaulting.require_text(
            params.get("watcherEid") or params.get("watcherId"),
            field="Watcher",
        )
        return {
            "account": _kf_state_view(record),
            "watcher": await _refresh_kf_watcher_status(hby, organizer, record, watcher_id, surface_config),
        }

    raise vaulting.RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not allowed.")
