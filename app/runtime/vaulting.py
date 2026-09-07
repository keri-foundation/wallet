from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


class NullConfiger:
    def __init__(self):
        self.opened = True
        self.temp = False

    def get(self, human=None):
        return {}

    def close(self, clear=False):
        self.opened = False
        return True


class RuntimeFault(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_CONFIG: dict = {}
_STATE = None
_REGISTRY = None
# Test-only: last product close_state outcome (set after Habery.aclose returns).
# Read by the reserved __test.oobi.p8 last_close probe. Not part of any product
# response and harmless when unset.
_LAST_CLOSE = None


def configure_runtime(
    *,
    ensure_runtime_packages,
    load_modules,
    wallet_storage_prefix: str,
    registry_name: str,
    registry_store: str,
    legacy_root_salt: str,
    passcode_kdf_defaults: dict,
    default_settings: dict,
    kf_state_subdb: str,
):
    _CONFIG.clear()
    _CONFIG.update(
        ensure_runtime_packages=ensure_runtime_packages,
        load_modules=load_modules,
        wallet_storage_prefix=wallet_storage_prefix,
        registry_name=registry_name,
        registry_store=registry_store,
        legacy_root_salt=legacy_root_salt,
        passcode_kdf_defaults=dict(passcode_kdf_defaults),
        default_settings=dict(default_settings),
        kf_state_subdb=kf_state_subdb,
    )


def load_modules():
    return _CONFIG["load_modules"]()


async def _ensure_runtime_packages():
    await _CONFIG["ensure_runtime_packages"]()


def default_settings():
    return dict(_CONFIG["default_settings"])


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sort_key(value):
    return (value or "").lower()


def _storage_name_for(vault_id: str):
    return f"{_CONFIG['wallet_storage_prefix']}{vault_id}"


def _generate_root_salt():
    return load_modules()["signing"].Salter().qb64


def dg_key(pre, dig):
    if hasattr(pre, "encode"):
        pre = pre.encode("utf-8")
    if hasattr(dig, "encode"):
        dig = dig.encode("utf-8")
    return b"%s.%s" % (pre, dig)


def _coerce_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_passcode_kdf(raw_value=None):
    defaults = _CONFIG["passcode_kdf_defaults"]
    raw_value = raw_value if isinstance(raw_value, dict) else {}
    return {
        "algorithm": str(raw_value.get("algorithm") or defaults["algorithm"]),
        "salt": str(raw_value.get("salt") or defaults["salt"]),
        "opslimit": _coerce_int(raw_value.get("opslimit"), defaults["opslimit"]),
        "memlimit": _coerce_int(raw_value.get("memlimit"), defaults["memlimit"]),
        "outlen": _coerce_int(raw_value.get("outlen"), defaults["outlen"]),
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
        "createdAt": created_at or now_iso(),
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
    created_at = str(record.get("createdAt") or "").strip() or now_iso()
    root_salt = str(record.get("rootSalt") or "").strip() or _CONFIG["legacy_root_salt"]
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


async def ensure_registry():
    global _REGISTRY

    if _REGISTRY is not None:
        return _REGISTRY

    await _ensure_runtime_packages()
    modules = load_modules()
    db = await modules["webdbing"].WebDBer.open(
        name=_CONFIG["registry_name"],
        stores=[_CONFIG["registry_store"]],
    )
    store = db.env.open_db(_CONFIG["registry_store"])
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


async def _build_vault_state(record, *, bran: str = ""):
    await _ensure_runtime_packages()
    modules = load_modules()
    keeper = modules["webkeeping"].WebKeeper(name=record["storageName"])
    baser = modules["webbasing"].WebBaser(name=record["storageName"])
    kf_state_subdb = _CONFIG["kf_state_subdb"]
    if kf_state_subdb not in baser.SubDbNames:
        baser.SubDbNames = [*baser.SubDbNames, kf_state_subdb]
    await keeper.reopen()
    await baser.reopen()

    try:
        hby = modules["habbing"].Habery(
            name=record["storageName"],
            ks=keeper,
            db=baser,
            cf=NullConfiger(),
            temp=False,
            salt=record["rootSalt"],
            bran=bran or None,
        )
    except Exception:
        await baser.aclose(clear=False)
        await keeper.aclose(clear=False)
        raise

    return {
        "modules": modules,
        "vault": record,
        "hby": hby,
        "bran": bran,
        "kfSurfaceKeyState": {},
    }


async def open_vault_state(record, *, passcode=None, bran: str | None = None):
    global _STATE

    if _STATE is not None and _STATE["vault"]["id"] == record["id"]:
        return _STATE

    await close_state(clear=False)

    derived_bran = bran or ""
    if record.get("encrypted"):
        if not derived_bran and not str(passcode or "").strip():
            raise RuntimeFault("VALIDATION", "Passcode is required to open this vault.")
        if not derived_bran:
            derived_bran = _prepare_bran(passcode, record.get("passcodeKdf"))

    modules = load_modules()
    try:
        _STATE = await _build_vault_state(record, bran=derived_bran)
    except modules["kering"].AuthError as exc:
        raise RuntimeFault("AUTH_FAILED", str(exc) or "Passcode incorrect for this vault.") from exc
    except ValueError as exc:
        raise RuntimeFault("VALIDATION", str(exc)) from exc

    return _STATE


def require_open_state(vault_id: str | None = None):
    if _STATE is None:
        raise RuntimeFault("LOCKED", "Open a vault before calling vault operations.")
    if vault_id and _STATE["vault"]["id"] != vault_id:
        raise RuntimeFault("LOCKED", f"Vault '{vault_id}' is not open.")
    return _STATE


async def close_state(*, clear: bool = False):
    global _STATE, _LAST_CLOSE
    if _STATE is None:
        return

    state = _STATE
    try:
        await state["hby"].aclose(clear=clear)
        # aclose is the guaranteed-flush close path: WebBaser/WebKeeper set
        # .opened=False only after their async flush completes. Recording that
        # here is the product-close proof (probe __test.oobi.p8 last_close).
        _LAST_CLOSE = {
            "returned": True,
            "clear": bool(clear),
            "baser_opened": bool(getattr(getattr(state.get("hby"), "db", None), "opened", None)),
            "keeper_opened": bool(getattr(getattr(state.get("hby"), "ks", None), "opened", None)),
        }
    except Exception as exc:  # noqa: BLE001 - record failure then re-raise
        _LAST_CLOSE = {
            "returned": False,
            "clear": bool(clear),
            "error": str(exc)[:200],
        }
        raise
    finally:
        _STATE = None


async def persist_and_reload():
    state = require_open_state()
    vault = state["vault"]
    bran = state["bran"]
    await close_state(clear=False)
    return await open_vault_state(vault, bran=bran)


def require_text(value, *, field: str):
    text = str(value or "").strip()
    if not text:
        raise RuntimeFault("VALIDATION", f"{field} is required.")
    return text


def require_blind_oobi_url(value):
    url = require_text(value, field="OOBI URL")
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


def _require_vault_id(params):
    return require_text(params.get("vaultId"), field="Vault")


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


def remote_contacts_by_aid(hby, organizer):
    return {contact["id"]: dict(contact) for contact in _list_remote_contacts(hby, organizer)}


def find_remote_contact_by_oobi(hby, organizer, url: str):
    for contact in _list_remote_contacts(hby, organizer):
        if str(contact.get("oobi") or "").strip() == url:
            return dict(contact)
    return None


def restore_contact(organizer, aid: str, snapshot):
    if snapshot is None:
        organizer.rem(aid)
        return

    organizer.replace(
        aid,
        {field: value for field, value in snapshot.items() if field != "id"},
    )


def clear_oobi_tracking(hby, url: str):
    for store_name in ("oobis", "coobi", "eoobi", "roobi", "moobi"):
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


async def await_resolution(hby, oobiery, url: str, *, expected_aid: str = ""):
    modules = load_modules()
    oobiing = modules["oobiing"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0

    while loop.time() < deadline:
        oobiery.processFlows()
        if (roobi := hby.db.roobi.get(keys=(url,))) is not None:
            if roobi.state != oobiing.Result.resolved:
                raise RuntimeFault("RUNTIME_ERROR", f"OOBI resolution failed for {url}.")
            return roobi
        if expected_aid and expected_aid in hby.kevers:
            roobi = modules["recording"].OobiRecord(
                cid=expected_aid,
                date=oobiing.nowIso8601(),
                state=oobiing.Result.resolved,
            )
            hby.db.roobi.put(keys=(url,), val=roobi)
            return roobi
        await asyncio.sleep(0.05)

    roobi = hby.db.roobi.get(keys=(url,))
    coobi = hby.db.coobi.get(keys=(url,))
    eoobi = hby.db.eoobi.get(keys=(url,))
    moobi = hby.db.moobi.get(keys=(url,))
    detail = (
        f"roobi={getattr(roobi, 'state', '-')}"
        f" coobi={coobi is not None}"
        f" eoobi={eoobi is not None}"
        f" moobi={moobi is not None}"
        f" expected_aid_known={bool(expected_aid and expected_aid in hby.kevers)}"
    )
    if moobi is not None and getattr(moobi, "urls", None):
        detail = f"{detail} moobi_urls={list(moobi.urls)}"

    raise RuntimeFault("TIMEOUT", f"Timed out resolving OOBI '{url}'. {detail}")


def pick_oobi(raw: dict):
    oobis = raw.get("oobis")
    if isinstance(oobis, list):
        for item in oobis:
            if isinstance(item, str) and item:
                return item
    value = raw.get("oobi", "")
    return str(value or "").strip()


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
            "storageBackend": _CONFIG["default_settings"]["storageBackend"],
            "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
        }
    }


async def dispatch(method: str, params: dict):
    if method == "vaults.list":
        registry = await ensure_registry()
        return {"vaults": [_vault_view(record) for record in _registry_records(registry)]}

    if method == "vaults.create":
        registry = await ensure_registry()
        alias = require_text(params.get("name"), field="Vault name")

        if _registry_record_by_alias(registry, alias) is not None:
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

        _set_registry_record(registry, record)
        await registry["db"].flush()
        return {"vault": _vault_view(record)}

    if method == "vaults.open":
        registry = await ensure_registry()
        vault_id = _require_vault_id(params)
        record = _registry_record_by_id(registry, vault_id)
        if record is None:
            raise RuntimeFault("NOT_FOUND", f"Vault '{vault_id}' was not found.")
        state = await open_vault_state(record, passcode=params.get("passcode"))
        return _vault_summary(state)

    if method == "vaults.close":
        vault_id = _require_vault_id(params)
        require_open_state(vault_id)
        await close_state(clear=False)
        return {"vault": {"id": vault_id, "opened": False}}

    if method == "vaults.summary":
        state = require_open_state(_require_vault_id(params))
        return _vault_summary(state)

    state = require_open_state(_require_vault_id(params))
    modules = state["modules"]
    hby = state["hby"]
    organizer = modules["organizing"].Organizer(hby=hby)

    if method == "identifiers.list":
        return {"identifiers": _list_identifier_records(hby)}

    if method == "identifiers.get":
        aid = require_text(params.get("aid"), field="Identifier")
        return {"identifier": _get_identifier_record(hby, aid)}

    if method == "identifiers.create":
        alias = require_text(params.get("alias"), field="Identifier alias")
        if hby.habByName(alias) is not None:
            raise RuntimeFault("CONFLICT", f"Identifier alias '{alias}' already exists.")

        hby.makeHab(name=alias, icount=1, isith="1", ncount=1, nsith="1")
        reopened = await persist_and_reload()
        return {
            "identifier": _get_identifier_record(reopened["hby"], reopened["hby"].habByName(alias).pre),
        }

    if method == "remotes.list":
        return {"remotes": _list_remote_records(hby, organizer)}

    if method == "remotes.get":
        aid = require_text(params.get("aid"), field="Remote identifier")
        return {"remote": _get_remote_record(hby, organizer, aid)}

    if method == "remotes.update":
        aid = require_text(params.get("aid"), field="Remote identifier")
        patch = params.get("patch") or {}
        if not isinstance(patch, dict):
            raise RuntimeFault("BAD_REQUEST", "Remote update patch must be an object.")
        if "oobi" in patch:
            raise RuntimeFault(
                "BAD_REQUEST",
                "Remote OOBI changes must go through remotes.resolveOobi.",
            )

        disallowed_fields = sorted(set(patch.keys()) - {"alias", "company", "org", "note"})
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
        for field in ("alias", "company", "org", "note"):
            if field not in patch:
                continue
            value = str(patch[field] or "").strip()
            if field == "alias" and not value:
                raise RuntimeFault("VALIDATION", "Remote alias is required.")
            cleaned[field] = value

        if not cleaned:
            raise RuntimeFault("VALIDATION", "No remote fields were provided.")

        organizer.update(aid, cleaned)
        reopened = await persist_and_reload()
        reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
        remote = _get_remote_record(reopened["hby"], reopened_organizer, aid)
        if remote["aid"] != current_remote["aid"] or remote["oobi"] != current_remote["oobi"]:
            raise RuntimeFault("CONFLICT", "Remote identity state changed during metadata update.")
        return {"remote": remote}

    if method == "remotes.resolveOobi":
        url = require_blind_oobi_url(params.get("url"))
        alias = str(params.get("alias") or "").strip()
        existing_remote = find_remote_contact_by_oobi(hby, organizer, url)
        if existing_remote is not None:
            raise RuntimeFault(
                "CONFLICT",
                "Blind OOBI is already connected. Use remote edit for metadata changes.",
            )

        remote_contacts_before = remote_contacts_by_aid(hby, organizer)
        clear_oobi_tracking(hby, url)

        oobi_record = modules["recording"].OobiRecord(date=modules["oobiing"].nowIso8601())
        if alias:
            oobi_record.oobialias = alias

        resolved_aid = None
        hby.db.oobis.pin(keys=(url,), val=oobi_record)
        try:
            roobi = await await_resolution(hby, modules["oobiing"].Oobiery(hby=hby), url)
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

            reopened = await persist_and_reload()
            reopened_organizer = reopened["modules"]["organizing"].Organizer(hby=reopened["hby"])
            return {
                "remote": _get_remote_record(reopened["hby"], reopened_organizer, resolved_aid),
            }
        except RuntimeFault:
            if resolved_aid is not None:
                restore_contact(organizer, resolved_aid, remote_contacts_before.get(resolved_aid))
            clear_oobi_tracking(hby, url)
            raise

    if method == "settings.get":
        return {
            "settings": {
                **default_settings(),
                "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
            }
        }

    raise RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not allowed.")
