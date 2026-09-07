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
    watcher_url: str = ""
    # Verified direct-service milestones (proven during the hosted onboarding
    # run). Persisted so a fresh worker can render truthful direct state
    # without re-deriving protocol facts from Kf Boot management.
    witness_url: str = ""
    witness_oobi_verified: bool = False
    witness_registered: bool = False
    witness_receipt_verified: bool = False
    watcher_oobi_verified: bool = False
    watcher_introduced: bool = False
    watcher_query_verified: bool = False
    watcher_observed_sn: int = 0
    # Bounded, truthful direct-query error context for a required watcher that
    # could not be verified during onboarding (empty on success / no watcher).
    watcher_query_error: str = ""
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
        "watcherUrl": record.watcher_url,
        "witnessUrl": record.witness_url,
        "witnessOobiVerified": record.witness_oobi_verified,
        "witnessRegistered": record.witness_registered,
        "witnessReceiptVerified": record.witness_receipt_verified,
        "watcherOobiVerified": record.watcher_oobi_verified,
        "watcherIntroduced": record.watcher_introduced,
        "watcherQueryVerified": record.watcher_query_verified,
        "watcherObservedSn": record.watcher_observed_sn,
        "watcherQueryError": record.watcher_query_error,
        "failureReason": record.failure_reason,
    }


def _select_account_option(snapshot: dict, code: str):
    for option in snapshot["bootstrap"]["accountOptions"]:
        if option["code"] == code:
            return option
    return None


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


def _validate_kf_account_witness_profile(hab, *, witness_eids: list[str], toad: int):
    existing_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    existing_toad = getattr(getattr(getattr(hab, "kever", None), "toader", None), "num", None)
    if existing_wits and (set(existing_wits) != set(witness_eids) or len(existing_wits) != len(witness_eids)):
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The existing permanent account AID does not match the allocated hosted witness pool.",
        )
    if existing_wits and existing_toad is not None and toad and existing_toad != toad:
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The existing permanent account AID does not match the allocated witness threshold.",
        )


def _iter_hab_kel_messages(hab):
    last_sn = int(getattr(getattr(hab, "kever", None), "sn", -1) or -1)
    for sn in range(last_sn + 1):
        # watcher-hk on_post parses one plain KERI message per request (no
        # AttachmentGroup pipelined counter), so send the unpipelined replay.
        yield transporting.own_event_replay(hab, sn=sn, pipelined=False)


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
            modules["oobiing"].Oobiery(hby=hby),
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

    # Receipt events use ``d`` for the receipted event digest; SerderKERI
    # exposes that field as ``said`` for rct messages.
    said = receipt.said
    if not said:
        return False
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
    counter = modules["eventing"].Counter(
        qb64b=ims,
        strip=True,
        version=transporting._kf_reply_parser_version(raw_bytes),
    )
    witness["_receipt_fallback"] = f"counter={counter.name}:{counter.count} remaining={len(ims)}"
    if counter.name == modules["eventing"].Codens.AttachmentGroup:
        group_size = counter.byteCount(cold=modules["kering"].Colds.txt)
        if len(ims) < group_size:
            return False
        ims = bytearray(ims[:group_size])
        counter = modules["eventing"].Counter(
            qb64b=ims,
            strip=True,
            version=transporting._kf_reply_parser_version(raw_bytes),
        )
        witness["_receipt_fallback"] += f" inner={counter.name}:{counter.count}"
    if counter.name == modules["eventing"].Codens.NonTransReceiptCouples:
        group_size = counter.byteCount(cold=modules["kering"].Colds.txt)
        if len(ims) < group_size:
            return False
        couples = bytearray(ims[:group_size])
        added = False
        while couples:
            verfer = modules["eventing"].Verfer(qb64b=couples, strip=True)
            cigar = modules["eventing"].Cigar(qb64b=couples, strip=True)
            if verfer.qb64 != witness_eid or not verfer.verify(cigar.raw, event.raw):
                continue
            wiger = modules["eventing"].Siger(raw=cigar.raw, index=index, verfer=verfer)
            added = hab.db.wigs.add(keys=vaulting.dg_key(hab.pre, said), val=wiger) or added
        witness["_receipt_fallback"] += f" couples_added={added}"
        return added

    if counter.name != modules["eventing"].Codens.WitnessIdxSigs:
        return False

    group_size = counter.byteCount(cold=modules["kering"].Colds.txt)
    witness["_receipt_fallback"] += f" group_size={group_size}"
    if len(ims) < group_size:
        return False
    signatures = bytearray(ims[:group_size])
    added = False
    verfer = modules["eventing"].Verfer(qb64=witness_eid)
    while signatures:
        wiger = modules["eventing"].Siger(qb64b=signatures, strip=True, verfer=verfer)
        if wiger.index != index:
            witness["_receipt_fallback"] += f" index={wiger.index}!={index}"
            continue
        if not verfer.verify(wiger.raw, event.raw):
            witness["_receipt_fallback"] += " signature_invalid"
            continue
        added = hab.db.wigs.add(keys=vaulting.dg_key(hab.pre, said), val=wiger) or added

    witness["_receipt_fallback"] += f" added={added}"
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
        f"witness={str(witness.get('eid', '') or '')} "
        f"receipt_meta={str(witness.get('_receipt_meta', '') or '')} "
        f"receipt_fallback={str(witness.get('_receipt_fallback', '') or '')}"
    )


async def _register_with_witness(hab, witness: dict):
    # /aids validates a single *signed* inception message. clonePreIter yields
    # stored event bodies, which omit CESR controller-signature attachments;
    # msgOwnEvent supplies the complete event that witness-hk parses.
    kel = bytearray(hab.msgOwnEvent(sn=0))

    inception_pre = ""
    try:
        inception_pre = str(vaulting.load_modules()["serdering"].SerderKERI(raw=bytes(kel)).pre or "")
    except Exception:
        inception_pre = "unknown"

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
        vaulting.load_modules()["httping"].CESR_DESTINATION_HEADER: witness["eid"],
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
        raise vaulting.RuntimeFault(
            "NETWORK_ERROR",
            f"{detail} from {witness_url} "
            f"(account_aid={hab.pre} inception_pre={inception_pre} witness_eid={witness['eid']})",
        )

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
    modules = vaulting.load_modules()
    body, attachment = transporting._split_cesr_message(msg)
    headers = {
        "Content-Type": str(getattr(modules["httping"], "CESR_CONTENT_TYPE", "") or "application/cesr"),
        "Content-Length": str(len(body)),
        modules["httping"].CESR_DESTINATION_HEADER: witness["eid"],
        "Authorization": auth_header,
    }
    if attachment:
        headers[modules["httping"].CESR_ATTACHMENT_HEADER] = bytes(attachment).decode("utf-8")

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

    receipt = modules["serdering"].SerderKERI(raw=raw_bytes)
    # Bounded receipt metadata for failure diagnostics — never the signed
    # attachment or full KED (which can carry controller digests/seals).
    witness["_receipt_meta"] = (
        f"said={receipt.said} body={receipt.size} attachment={len(raw_bytes) - receipt.size}"
    )
    hab.psr.parseOne(ims=bytearray(raw_bytes), version=receipt.pvrsn)
    if getattr(hab.psr, "kvy", None) is not None:
        hab.psr.kvy.processEscrows()
    if not _get_witness_receipts(hab.db, hab.pre, hab.kever.serder.said):
        _promote_non_witness_receipt(hab, witness, hab.kever.serder.said)
    if not _get_witness_receipts(hab.db, hab.pre, hab.kever.serder.said):
        _ingest_witness_receipt_fallback(hab, witness, raw_bytes)


async def _rotate_kf_account_to_witnesses(hab, witnesses: list[dict], *, toad: int):
    allocated_wits = [witness["eid"] for witness in witnesses]
    current_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    current_toad = getattr(getattr(getattr(hab, "kever", None), "toader", None), "num", None)

    if current_wits == allocated_wits and (current_toad is None or current_toad == toad):
        return

    if current_wits:
        raise vaulting.RuntimeFault(
            "CONFLICT",
            "The existing permanent account AID already has a different witness configuration.",
        )

    hab.rotate(toad=toad, cuts=[], adds=allocated_wits)
    rotation_msg = bytes(hab.msgOwnEvent(sn=hab.kever.sn))

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


async def _send_direct_cesr(
    url: str,
    msg,
    *,
    destination: str = "",
    method: str = "POST",
    purpose: str = "direct CESR message",
):
    # watcher-hk on_post accepts one plain KERI message per request as a CESR
    # body + CESR-Attachment header (single-message browser transport). The
    # raw-stream PUT path is only for pipelined attachment groups, which the
    # ordinary browser introduction does not use.
    body, attachment = transporting._split_cesr_message(msg)
    try:
        await transporting.post_cesr(
            url,
            body=body,
            attachment=attachment,
            destination=destination,
            method=method,
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )
    except vaulting.RuntimeFault as exc:
        raise vaulting.RuntimeFault(exc.code, f"Watcher rejected {purpose}: {exc}") from exc


async def _introduce_account_to_watcher(hab, watcher: dict, witnesses: list[dict]):
    watcher_eid = str(watcher.get("eid", "") or "")
    watcher_url = str(watcher.get("watcherUrl", "") or watcher.get("url", "") or "")
    if not watcher_eid or not watcher_url:
        raise vaulting.RuntimeFault("CONFLICT", "Hosted watcher allocation did not include a usable endpoint.")

    ender = hab.db.ends.get(keys=(hab.pre, "watcher", watcher_eid))
    if not ender or not ender.allowed:
        end_role = hab.reply(
            route="/end/role/add",
            data=dict(cid=hab.pre, role="watcher", eid=watcher_eid),
        )
        hab.psr.parseOne(ims=bytes(end_role))
        await _send_direct_cesr(
            watcher_url,
            end_role,
            destination=watcher_eid,
            purpose="the controller watcher endpoint authorization",
        )

    for sn, msg in enumerate(_iter_hab_kel_messages(hab)):
        await _send_direct_cesr(
            watcher_url,
            msg,
            destination=watcher_eid,
            purpose=f"the controller key event at sequence number {sn}",
        )

    add_reply = hab.reply(
        route=f"/watcher/{watcher_eid}/add",
        data=dict(
            cid=hab.pre,
            oid=hab.pre,
            oobi=_controller_oobi_url(hab.pre, witnesses),
        ),
    )
    hab.psr.parseOne(ims=bytes(add_reply))
    await _send_direct_cesr(
        watcher_url,
        add_reply,
        destination=watcher_eid,
        purpose="the controller watcher-add request",
    )


def _local_connection_status(hby, organizer, aid: str):
    if hby.kevers.get(aid) is not None:
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


async def _query_kf_watcher_direct(
    hby,
    hab,
    *,
    watcher_eid: str,
    watcher_url: str,
):
    """Send a controller-signed KERI ``ksn`` query directly to the hosted watcher
    over its public HTTPS CESR listener and return the parsed key-state reply.

    This exercises the actual Fort Web -> watcher query path (watcher USE), not
    the Kf Boot management surface. The watcher validates that the query source
    is this account's controller AID and replies with an endorsed ``/ksn`` reply
    whose ``a`` payload is the controller's key state as the watcher holds it.

    Verification scope: the reply is parsed (``rpy`` ilk, ``/ksn/`` route) and
    its reported key state is cross-checked byte-for-byte against this
    controller's own authoritative local key state (AID, SN, digest, witness
    set, toad). Because only the controller holds its own keys, an exact match
    proves the watcher is answering with the controller's true current state.
    The reply travels over the pinned public HTTPS endpoint; this routine does
    NOT independently parse/verify the watcher's separate response-signature
    attachment, so callers must not describe this as watcher-signature proof.
    """
    modules = vaulting.load_modules()
    kering = modules["kering"]
    eventing = modules["eventing"]
    serdering = modules["serdering"]

    watcher_url = str(watcher_url or "").strip().rstrip("/")
    if not watcher_url:
        raise vaulting.RuntimeFault("CONFLICT", "This account has no hosted watcher endpoint to query.")

    qserder = eventing.query(
        pre=hab.pre,
        route="ksn",
        query={"i": hab.pre, "src": watcher_eid},
        version=kering.Vrsn_2_0,
        pvrsn=kering.Vrsn_2_0,
        kind=eventing.Kinds.json,
    )
    # Query messages must be endorsed with SealLast (lsgs / TransLastIdxSigGroups)
    # so the receiving parser can recover the querier source. hab.endorse's
    # documented rule is "Query messages should always use SealLast."
    endorsed = hab.endorse(serder=qserder, last=True)
    body, attachment = transporting._split_cesr_message(endorsed)

    raw_bytes, _ = await transporting.post_cesr(
        urljoin(f"{watcher_url}/", "/"),
        body=body,
        attachment=attachment,
        destination=watcher_eid,
        method="POST",
        timeout_ms=_CONFIG["account_query_timeout_ms"],
    )
    if not raw_bytes:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "Watcher returned an empty reply to the key-state query.")

    reply_serder = serdering.SerderKERI(raw=raw_bytes)
    reply_ked = reply_serder.ked
    reply_route = str(reply_ked.get("r", "") or "")
    if str(reply_ked.get("t", "") or "") != "rpy":
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"Watcher query reply had unexpected ilk '{reply_ked.get('t', '')}', expected rpy.",
        )
    if not reply_route.startswith("/ksn/"):
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            f"Watcher query reply route '{reply_route}' did not match /ksn/.",
        )

    data = reply_ked.get("a", {})
    if not isinstance(data, dict):
        raise vaulting.RuntimeFault("BAD_RESPONSE", "Watcher query reply did not include a key-state payload.")

    # Cross-check the reply's reported key state against this controller's own
    # authoritative local key state. The controller holds its own keys, so an
    # exact match proves the watcher answered with the controller's true state.
    local_sn = int(getattr(getattr(hab, "kever", None), "sn", 0) or 0)
    local_digest = str(getattr(getattr(getattr(hab, "kever", None), "serder", None), "said", "") or "")
    local_wits = list(getattr(getattr(hab, "kever", None), "wits", []) or [])
    local_toad = int(getattr(getattr(getattr(hab, "kever", None), "toader", None), "num", 0) or 0)

    reply_sn_hex = str(data.get("s", "") or "")
    reply_digest = str(data.get("d", "") or "")
    reply_wits = list(data.get("b", []) or [])
    reply_toad_hex = str(data.get("bt", "") or "")
    try:
        reply_sn = int(reply_sn_hex, 16) if reply_sn_hex else -1
        reply_toad = int(reply_toad_hex, 16) if reply_toad_hex else 0
    except ValueError:
        raise vaulting.RuntimeFault("BAD_RESPONSE", "Watcher query reply carried a non-hex SN or toad.")
    state_match = (
        str(data.get("i", "") or "") == hab.pre
        and reply_sn == local_sn
        and bool(local_digest)
        and reply_digest == local_digest
        and reply_wits == local_wits
        and reply_toad == local_toad
    )
    if not state_match:
        raise vaulting.RuntimeFault(
            "BAD_RESPONSE",
            (
                "Watcher query reply key state does not match this controller's authoritative "
                f"key state (sn={reply_sn} vs {local_sn}, digest={reply_digest[:12] if reply_digest else ''} "
                f"vs {local_digest[:12] if local_digest else ''}, wits={reply_wits} vs {local_wits}, "
                f"toad={reply_toad} vs {local_toad})."
            ),
        )

    return {
        "eid": watcher_eid,
        "url": watcher_url,
        "querySaid": qserder.said,
        "replySaid": str(reply_serder.said or ""),
        "replyRoute": reply_route,
        "protocolMajor": int(reply_serder.pvrsn.major),
        "controller": str(data.get("i", "") or ""),
        "sn": reply_sn_hex,
        "digest": reply_digest,
        "kind": str(reply_ked.get("et", "") or ""),
        "witnesses": reply_wits,
        "toad": reply_toad_hex,
        "stateMatch": True,
        "stateMatchDetail": "reply key state equals the controller's authoritative local key state",
    }


def _kf_services_overview(hby, organizer, record: KfVaultState) -> dict:
    """Return the normalized hosted-service connection view model.

    This is the domain-layer boundary the UI consumes. It carries NO protocol
    machinery: it derives direct service state purely from milestones persisted
    during the hosted onboarding run (OOBI resolution, registration, receipt,
    introduction, direct query with controller-state cross-check), kept
    independent from Kf Boot account-management synchronization.
    """
    has_account = _has_kf_account(record)

    witness_eid = record.witness_eids[0] if record.witness_eids else ""
    witness_endpoint = record.witness_url or ""
    witness_oobi = record.witness_oobi_verified and bool(witness_eid)
    witness_registered = record.witness_registered and bool(witness_eid)
    witness_receipt = record.witness_receipt_verified and bool(witness_eid)
    if has_account and witness_eid and witness_oobi and witness_registered and witness_receipt:
        witness_direct = "connected"
    elif has_account and witness_eid:
        witness_direct = "partial"
    else:
        witness_direct = "not_connected"

    watcher_eid = record.watcher_eid or ""
    watcher_endpoint = record.watcher_url or ""
    watcher_oobi = record.watcher_oobi_verified and bool(watcher_eid)
    watcher_introduced = record.watcher_introduced and bool(watcher_eid)
    watcher_query = record.watcher_query_verified and bool(watcher_eid)
    watcher_sn = record.watcher_observed_sn
    if has_account and watcher_eid and watcher_oobi and watcher_introduced and watcher_query and watcher_sn >= 1:
        watcher_direct = "connected"
    elif has_account and watcher_eid and watcher_introduced:
        watcher_direct = "partial"
    else:
        watcher_direct = "not_connected"

    # Kf Boot account-management synchronization is a separate surface from the
    # direct service proof. It is not derived here; it is reported independently
    # (the /account management 409 remains a known follow-up).
    management_sync = "pending" if has_account else "none"

    return {
        "accountAid": record.account_aid or "",
        "status": record.status,
        "witness": {
            "eid": witness_eid,
            "endpoint": witness_endpoint,
            "oobiVerified": witness_oobi,
            "registered": witness_registered,
            "receiptVerified": witness_receipt,
            "directStatus": witness_direct,
            "managementSyncStatus": management_sync,
        },
        "watcher": {
            "eid": watcher_eid,
            "endpoint": watcher_endpoint,
            "oobiVerified": watcher_oobi,
            "introduced": watcher_introduced,
            "queryVerified": watcher_query,
            "observedSn": watcher_sn,
            "queryError": record.watcher_query_error,
            "directStatus": watcher_direct,
            "managementSyncStatus": management_sync,
        },
    }


async def _await_kf_session_provisioned(
    hby,
    ephemeral_hab,
    *,
    surfaces: transporting.KfSurfaceConfig,
    destination: str,
    boot_server_aid: str,
    session_id: str,
    expected_witness_count: int,
    watcher_required: bool,
):
    """Poll the Kf Boot onboarding session until its hosted witness pool (and,
    when required, the hosted watcher) is allocated, or the session reaches a
    terminal state. Returns the final session/status payload."""
    if not session_id:
        return {}
    last_payload = {}
    for _ in range(30):
        reply = await transporting.send_kf_exn(
            hby,
            ephemeral_hab,
            surface_name="onboarding",
            surface_url=transporting.require_kf_surface_url(surfaces, "onboarding"),
            route="/onboarding/session/status",
            payload={"session_id": session_id},
            destination=destination,
            expected_sender=destination or boot_server_aid or "",
            timeout_ms=_CONFIG["cesr_timeout_ms"],
        )
        last_payload = reply.get("payload") or {}
        state = str(last_payload.get("state", "") or "")
        if state in {"failed", "cancelled", "expired"}:
            failure_reason = str(last_payload.get("failure_reason", "") or "").strip()
            raise vaulting.RuntimeFault(
                "CONFLICT",
                failure_reason or f"The KF onboarding session is {state}.",
            )
        witnesses = last_payload.get("witnesses") or []
        watcher = last_payload.get("watcher")
        if len(witnesses) >= expected_witness_count and (not watcher_required or isinstance(watcher, dict)):
            return last_payload
        await asyncio.sleep(1.0)
    state = str(last_payload.get("state", "") or "")
    raise vaulting.RuntimeFault(
        "TIMEOUT",
        f"KF onboarding did not provision its hosted witness pool in time (session state '{state}').",
    )


async def _verify_kf_watcher_during_onboarding(
    hby,
    hab,
    record: KfVaultState,
    *,
    watcher_eid: str,
    watcher_url: str,
):
    """Run the direct watcher key-state query as part of normal hosted onboarding.

    The product onboarding path (not a separate driver command) invokes the same
    ``_query_kf_watcher_direct`` operation the live acceptance drives, so the
    verified milestone is produced by the product flow itself.

    Resume/idempotency rule: a current verified milestone is reused - no query is
    re-issued when ``watcher_query_verified`` is already true and the stored
    observed SN equals the account's current key-state SN. Otherwise (missing or
    stale) the query runs again and the durable milestone is refreshed.

    Never raises: a required watcher that is momentarily unverifiable does not
    strand an otherwise-onboarded account. Returns
    ``(verified, observed_sn, error, query)`` so the caller can persist truthful
    direct-service state (the UI derives Connected only from a verified match).
    """
    account_sn = int(getattr(getattr(hab, "kever", None), "sn", 0) or 0)
    if record.watcher_query_verified and record.watcher_observed_sn == account_sn:
        return True, record.watcher_observed_sn, "", None
    try:
        query = await _query_kf_watcher_direct(
            hby,
            hab,
            watcher_eid=watcher_eid,
            watcher_url=watcher_url,
        )
    except Exception as exc:  # noqa: BLE001 - bounded failure, never aborts account onboarding
        return False, account_sn, str(exc), None
    observed_hex = str(query.get("sn", "") or "")
    try:
        observed_sn = int(observed_hex, 16) if observed_hex else account_sn
    except ValueError:
        observed_sn = account_sn
    return True, observed_sn, "", query


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
            boot_server_aid = start_reply["sender"] or boot_server_aid
            start_payload = start_reply["payload"]
            if start_payload.get("account_aid") and start_payload["account_aid"] != account_hab.pre:
                raise vaulting.RuntimeFault(
                    "CONFLICT",
                    "The saved KF onboarding session is bound to a different permanent account AID.",
                )
            session_state = str(start_payload.get("state", "") or "")
            if session_state in {"failed", "cancelled", "expired"}:
                failure_reason = str(start_payload.get("failure_reason", "") or "").strip()
                _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)
                raise vaulting.RuntimeFault(
                    "CONFLICT",
                    failure_reason or f"The saved KF onboarding session is {session_state}.",
                )
        else:
            destination = transporting.kf_surface_destination(
                surfaces,
                surface_name="onboarding",
                boot_server_aid=boot_server_aid,
            )
            await transporting.send_kf_event(
                transporting.require_kf_surface_url(surfaces, "onboarding"),
                ephemeral_hab.msgOwnInception(),
                destination=destination,
                timeout_ms=_CONFIG["cesr_timeout_ms"],
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

        # Kf Boot provisions hosted witnesses/watcher asynchronously after
        # session/start returns, so poll session/status until the witness pool
        # (and required watcher) is allocated before continuing onboarding.
        destination = transporting.kf_surface_destination(
            surfaces,
            surface_name="onboarding",
            boot_server_aid=boot_server_aid,
        )
        start_payload = await _await_kf_session_provisioned(
            hby,
            ephemeral_hab,
            surfaces=surfaces,
            destination=destination,
            boot_server_aid=boot_server_aid,
            session_id=str(start_payload.get("session_id", "") or ""),
            expected_witness_count=int(option["witnessCount"]),
            watcher_required=bool(snapshot["bootstrap"]["watcherRequired"]),
        )

        for entry in start_payload.get("witnesses", []):
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

        raw_watcher = start_payload.get("watcher")
        if isinstance(raw_watcher, dict):
            watcher_row = {
                "eid": str(raw_watcher.get("eid", "") or ""),
                "name": str(raw_watcher.get("name", "") or ""),
                "watcherUrl": str(raw_watcher.get("watcher_url") or raw_watcher.get("url") or ""),
                "oobi": vaulting.pick_oobi(raw_watcher),
                "regionId": str(raw_watcher.get("region_id", "") or snapshot["bootstrap"]["regionId"]),
                "regionName": str(raw_watcher.get("region_name", "") or snapshot["bootstrap"]["regionName"]),
            }

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
    record.watcher_url = watcher_row["watcherUrl"] if watcher_row is not None else ""
    # Persist the direct-service milestones proven during this run. Reaching
    # this point means: witness OOBI resolved + registered at POST /aids + a
    # real witness receipt persisted after the witnessed rotation, and the
    # watcher OOBI resolved + the end-role/icp/rot/watcher-add introduction was
    # accepted. The domain view model renders these as direct service state.
    record.witness_url = witness_rows[0]["witnessUrl"] if witness_rows else ""
    record.witness_oobi_verified = True
    record.witness_registered = True
    record.witness_receipt_verified = True
    record.watcher_oobi_verified = watcher_row is not None and bool(watcher_row.get("oobi"))
    record.watcher_introduced = watcher_row is not None
    watcher_query_detail = None
    if watcher_row is not None:
        # Normal product onboarding performs the direct watcher key-state query
        # itself (the same runtime operation the live acceptance drives). The
        # reply is cross-checked against this controller's authoritative local
        # key state, so a matching reply persists the verified milestone through
        # the product path - a test driver is not required to create it.
        verified, observed_sn, watcher_error, watcher_query_detail = (
            await _verify_kf_watcher_during_onboarding(
                hby,
                account_hab,
                record,
                watcher_eid=str(watcher_row.get("eid", "") or ""),
                watcher_url=str(watcher_row.get("watcherUrl", "") or ""),
            )
        )
        record.watcher_query_verified = verified
        record.watcher_observed_sn = observed_sn
        record.watcher_query_error = watcher_error
    else:
        record.watcher_query_verified = False
        record.watcher_observed_sn = int(getattr(getattr(account_hab, "kever", None), "sn", 0) or 0)
        record.watcher_query_error = ""
    record.failure_reason = ""
    _clear_kf_onboarding_session(hby, record, delete_auth_hab=True)

    reopened = await vaulting.persist_and_reload()
    reopened_record = _load_kf_state(reopened["hby"])
    reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
    return {
        "account": _kf_state_view(reopened_record),
        "watcherQuery": watcher_query_detail,
        "watcherQueryError": str(reopened_record.watcher_query_error or ""),
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
            fallback_boot_url=params.get("bootUrl") or record.boot_url,
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
            fallback_boot_url=params.get("bootUrl") or record.boot_url,
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

    if method == "kf.account.watchers.query":
        record = _load_kf_state(hby)
        hab = _require_kf_account_hab(hby, record)
        watcher_id = str(params.get("watcherEid") or record.watcher_eid or "").strip()
        if not watcher_id:
            raise vaulting.RuntimeFault("CONFLICT", "This account has no hosted watcher AID to query.")
        watcher_url = str(params.get("watcherUrl") or record.watcher_url or "").strip()
        query = await _query_kf_watcher_direct(
            hby,
            hab,
            watcher_eid=watcher_id,
            watcher_url=watcher_url,
        )
        local_status, local_tone = _local_connection_status(hby, organizer, watcher_id)
        query["localStatus"] = local_status
        query["localStatusTone"] = local_tone
        # A successful direct query whose reply key state matches this
        # controller's own authoritative local key state is the strongest
        # persisted proof that the watcher accepted and can answer the
        # controller over its public path. Persist the durable milestone BEFORE
        # reporting success; if the durable write fails, the query must fail
        # closed (never report a verified milestone that was not persisted).
        record.watcher_query_verified = True
        observed = str(query.get("sn", "") or "")
        try:
            record.watcher_observed_sn = int(observed, 16) if observed else 0
        except ValueError:
            record.watcher_observed_sn = 0
        try:
            _save_kf_state(hby, record)
        except Exception as exc:  # noqa: BLE001 - surface as a product fault
            raise vaulting.RuntimeFault(
                "RUNTIME_ERROR",
                f"Watcher query succeeded but its verified milestone could not be persisted: {exc}",
            ) from exc
        return {
            "account": _kf_state_view(record),
            "watcher": query,
        }

    if method == "kf.services.overview":
        record = _load_kf_state(hby)
        return {
            "account": _kf_state_view(record),
            "services": _kf_services_overview(hby, organizer, record),
        }

    raise vaulting.RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not allowed.")
