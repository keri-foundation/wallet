"""Real Pyodide WebBaser lifecycle probe controlled by Playwright."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
import traceback
from types import SimpleNamespace
from uuid import uuid4

import js

import runtime_packages
import transporting
import vaulting


WORKER_ID = uuid4().hex
STORE = "docs."
DB_KEY = b"webbaser-db-key"
DB_VALUE = b"webbaser-db-value"
DB_PENDING_KEY = b"webbaser-pending-key"
DB_PENDING_VALUE = b"webbaser-pending-value"
SIGN_BYTES = b"fortweb-webbaser-lifecycle"
ROOT_SALT = "0AAwMTIzNDU2Nzg5YWJjZGVm"
OOBI_AID = "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW"
CONTACT_COMPANY = "KERI Foundation"
CONTACT_ORG = "FortWeb WebBaser"
CONTACT_NOTE = "resolved by BrowserClienter"
READY_PROBE = "fortweb.webbaser.ready"

RECEIPT_EVENT_RAW = (
    b'{"v":"KERICAACAAJSONAAGX.","t":"rot","d":"EJUWMsW_hKlUrs8gu9xesH73sJYq5XkGD3rfvFnifn6h",'
    b'"i":"EHvnx3Hw-Rx6cGHNDrtD7qlBktiDOD38Q5M7uVQBcnbk","s":"1",'
    b'"p":"EHvnx3Hw-Rx6cGHNDrtD7qlBktiDOD38Q5M7uVQBcnbk","kt":"1",'
    b'"k":["DCo-C98R3-Hz7e9s4PWcEyjxD-m4sSqLwnN1Qp6wIk3T"],"nt":"1",'
    b'"n":["ENrWQle1Wxv2f4GzjDuh8_tEoGIXvzQ8K4kh3TQneczo"],"bt":"1","br":[],'
    b'"ba":["BLLRWMx2FlA19DbZqP19nZbmP-LmAcFpdV1ANLOYYt2C"],"c":[],"a":[]}'
)
RECEIPT_RESPONSE_RAW = (
    b'{"v":"KERICAACAAJSONAACT.","t":"rct","d":"EJUWMsW_hKlUrs8gu9xesH73sJYq5XkGD3rfvFnifn6h",'
    b'"i":"EHvnx3Hw-Rx6cGHNDrtD7qlBktiDOD38Q5M7uVQBcnbk","s":"1"}'
    b'-CAi-MAhBLLRWMx2FlA19DbZqP19nZbmP-LmAcFpdV1ANLOYYt2C'
    b'0BAtiffBAQv9FGCDU_Tc35JCRDjPPYt-_sl04A-hhajt5jS4zYQgetlPwcroKorVTULXcTpF1CpU7TEUwxCd0JkK'
)

WEBBASER_PRE = "DND5dSPd_5tD0aUXnps5j3R1Yh6bfRmZQScbxGZ5hNJE"
WEBBASER_EST_DIG = "EGAPkzNZMtX-QiVgbRbyAIZGoXvbGv9IPb0foWTZvI_4"
WEBBASER_SN = 4
WEBBASER_FN = 7

_RUNTIME_READY = False


def _origin():
    return str(js.location.origin or "")


async def _ensure_packages():
    global _RUNTIME_READY
    await runtime_packages.ensure_runtime_packages()
    if _RUNTIME_READY:
        return
    transporting.configure_runtime(
        origin=_origin,
        default_boot_url=_origin(),
        kf_proxy_prefix="/_fortweb_proxy",
        bootstrap_timeout_ms=15_000,
        cesr_timeout_ms=15_000,
        reply_message_limit=16,
        reply_step_limit=4_096,
    )
    _RUNTIME_READY = True


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


class _HarnessStorage:
    """Adapt the raw PyWorker storage handle to the Keripy storage contract."""

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
    return _HarnessStorage(await module.storage(f"@fortweb/{namespace}"))


def _runtime_evidence():
    module_names = [
        "hio",
        "keri",
        "pysodium",
        "keri.db.webdbing",
        "keri.db.webbasing",
        "keri.app.webkeeping",
        "keri.app.habbing",
        "keri.app.oobiing",
    ]
    package_evidence = runtime_packages.runtime_evidence(module_names)
    installed = package_evidence["installed_distributions"]
    distributions = {
        name: {
            "version": installed[name]["version"],
            "requires_python": installed[name]["requires_python"],
        }
        for name in ("hio", "keri", "pysodium")
    }

    return {
        "worker_id": WORKER_ID,
        "pyodide_version": package_evidence["actual_runtime"]["pyodide"],
        "python_version": sys.version,
        "platform": sys.platform,
        "distributions": distributions,
        "module_paths": package_evidence["module_paths"],
        "sys_path": list(sys.path),
        "native_import_leaks": package_evidence["forbidden_imports"],
        "wheel_hashes": {
            row["url"]: row["sha256"]
            for row in package_evidence["wheels"].values()
        },
        "package_closure": package_evidence,
    }


async def _webdber_create(name):
    webdbing = importlib.import_module("keri.db.webdbing")
    dber = await webdbing.WebDBer.open(
        name=name,
        stores=[STORE],
        clear=True,
        storageOpener=_open_worker_storage,
    )
    docs = dber.env.open_db(STORE)
    _require(dber.setVal(docs, DB_KEY, DB_VALUE), "WebDBer initial write failed")
    await dber.flush()
    dber.close()

    dber = await webdbing.WebDBer.open(name=name, stores=[STORE], storageOpener=_open_worker_storage)
    docs = dber.env.open_db(STORE)
    _require(dber.getVal(docs, DB_KEY) == DB_VALUE, "WebDBer same-worker reopen failed")
    _require(dber.setVal(docs, DB_PENDING_KEY, DB_PENDING_VALUE), "WebDBer pending write failed")
    await dber.flush()
    dber.close()
    return {"name": name}


async def _webdber_recover(fixture, *, clear):
    webdbing = importlib.import_module("keri.db.webdbing")
    dber = await webdbing.WebDBer.open(
        name=fixture["name"],
        stores=[STORE],
        storageOpener=_open_worker_storage,
    )
    docs = dber.env.open_db(STORE)
    _require(dber.getVal(docs, DB_KEY) == DB_VALUE, "WebDBer value missing after worker restart")
    _require(
        dber.getVal(docs, DB_PENDING_KEY) == DB_PENDING_VALUE,
        "WebDBer explicitly flushed pending value missing after worker restart",
    )
    if clear:
        dber.clear()
        await dber.flush()
    dber.close()


async def _webdber_absent(fixture):
    webdbing = importlib.import_module("keri.db.webdbing")
    dber = await webdbing.WebDBer.open(
        name=fixture["name"],
        stores=[STORE],
        storageOpener=_open_worker_storage,
    )
    docs = dber.env.open_db(STORE)
    _require(dber.getVal(docs, DB_KEY) is None, "WebDBer clear did not persist")
    _require(dber.getVal(docs, DB_PENDING_KEY) is None, "WebDBer pending key survived clear")
    dber.clear()
    await dber.flush()
    dber.close()


def _make_webbaser_fixture(eventing, keri, recording, name):
    alias = f"{name}-alias"
    serder = eventing.interact(pre=WEBBASER_PRE, dig=WEBBASER_EST_DIG, sn=WEBBASER_SN)
    state_ee = eventing.StateEstEvent(s="3", d=WEBBASER_EST_DIG, br=[], ba=[])
    state = eventing.state(
        pre=WEBBASER_PRE,
        sn=WEBBASER_SN,
        pig=WEBBASER_EST_DIG,
        dig=serder.said,
        fn=WEBBASER_FN,
        eilk=keri.Ilks.ixn,
        keys=[WEBBASER_PRE],
        eevt=state_ee,
    )
    hab = recording.HabitatRecord(hid=WEBBASER_PRE, name=alias)
    return {
        "name": name,
        "alias": alias,
        "pre": WEBBASER_PRE,
        "said": serder.said,
        "sn": WEBBASER_SN,
        "serder": serder,
        "state": state,
        "hab": hab,
    }


def _check_webbaser(baser, fixture, phase):
    pre = fixture["pre"]
    said = fixture["said"]
    hab = baser.habs.get(keys=pre)
    _require(hab is not None and hab.name == fixture["alias"], f"{phase}: WebBaser habitat missing")
    state = baser.states.get(keys=pre)
    _require(state is not None and state.d == said, f"{phase}: WebBaser state missing")
    _require(baser.names.get(keys=("", fixture["alias"])) == pre, f"{phase}: WebBaser name missing")
    _require(pre in baser.prefixes, f"{phase}: WebBaser prefix missing")
    kever = baser.kevers.get(pre)
    _require(kever is not None and kever.sn == fixture["sn"], f"{phase}: WebBaser Kever missing")
    _require(kever.serder.said == said and kever.state().d == said, f"{phase}: WebBaser Kever state mismatch")


async def _webbaser_create(name):
    webbasing = importlib.import_module("keri.db.webbasing")
    eventing = importlib.import_module("keri.core.eventing")
    keri = importlib.import_module("keri")
    recording = importlib.import_module("keri.recording")
    fixture = _make_webbaser_fixture(eventing, keri, recording, name)
    baser = webbasing.WebBaser(name=name)
    await baser.reopen(clear=True, storageOpener=_open_worker_storage)
    baser.evts.put(keys=(fixture["pre"], fixture["said"]), val=fixture["serder"])
    baser.states.pin(keys=fixture["pre"], val=fixture["state"])
    baser.habs.put(keys=fixture["pre"], val=fixture["hab"])
    baser.names.put(keys=("", fixture["alias"]), val=fixture["pre"])
    baser.reload()
    _check_webbaser(baser, fixture, "create")
    await baser.aclose(clear=False)

    await baser.reopen(storageOpener=_open_worker_storage)
    _check_webbaser(baser, fixture, "same-worker-reopen")
    await baser.aclose(clear=False)
    return {key: fixture[key] for key in ("name", "alias", "pre", "said", "sn")}


async def _webbaser_recover(fixture, *, clear):
    webbasing = importlib.import_module("keri.db.webbasing")
    baser = webbasing.WebBaser(name=fixture["name"])
    await baser.reopen(storageOpener=_open_worker_storage)
    _check_webbaser(baser, fixture, "new-worker")
    await baser.aclose(clear=clear)


async def _webbaser_absent(fixture):
    webbasing = importlib.import_module("keri.db.webbasing")
    baser = webbasing.WebBaser(name=fixture["name"])
    await baser.reopen(storageOpener=_open_worker_storage)
    _require(baser.habs.get(keys=fixture["pre"]) is None, "WebBaser habitat survived clear")
    _require(baser.states.get(keys=fixture["pre"]) is None, "WebBaser state survived clear")
    _require(baser.names.get(keys=("", fixture["alias"])) is None, "WebBaser name survived clear")
    await baser.aclose(clear=True)


def _keeper_current_verfers(keeper, pre):
    sit = keeper.sits.get(pre)
    _require(sit is not None and sit.new.pubs, f"WebKeeper current key state missing for {pre}")
    verfers = []
    for pub in sit.new.pubs:
        signer = keeper.pris.get(pub.encode("utf-8"))
        _require(signer is not None, f"WebKeeper private signer missing for {pub}")
        verfers.append(signer.verfer)
    return verfers


def _keeper_sign(keeper, pre):
    basekeeping = importlib.import_module("keri.app.basekeeping")
    manager = basekeeping.Manager(ks=keeper)
    verfers = _keeper_current_verfers(keeper, pre)
    signature = manager.sign(ser=SIGN_BYTES, verfers=verfers)[0]
    _require(verfers[0].verify(signature.raw, SIGN_BYTES), "WebKeeper signature did not verify")
    return signature.qb64, [verfer.qb64 for verfer in verfers]


async def _webkeeper_create(name):
    basekeeping = importlib.import_module("keri.app.basekeeping")
    webkeeping = importlib.import_module("keri.app.webkeeping")
    signing = importlib.import_module("keri.core.signing")
    keeper = webkeeping.WebKeeper(name=name)
    await keeper.reopen(clear=True, storageOpener=_open_worker_storage)
    salt = signing.Salter(raw=b"0123456789abcdef").qb64
    manager = basekeeping.Manager(ks=keeper, salt=salt)
    verfers, _ = manager.incept(salt=salt, temp=True)
    pre = verfers[0].qb64
    first_signature = manager.sign(ser=SIGN_BYTES, verfers=verfers)[0]
    _require(verfers[0].verify(first_signature.raw, SIGN_BYTES), "WebKeeper initial signature failed")
    manager.rotate(pre=pre)
    rotated_signature, rotated_pubs = _keeper_sign(keeper, pre)
    await keeper.aclose(clear=False)

    await keeper.reopen(storageOpener=_open_worker_storage)
    same_signature, same_pubs = _keeper_sign(keeper, pre)
    _require(
        same_signature == rotated_signature and same_pubs == rotated_pubs,
        "WebKeeper same-worker reopen changed current keys",
    )
    await keeper.aclose(clear=False)
    return {
        "name": name,
        "pre": pre,
        "first_signature": first_signature.qb64,
        "rotated_signature": rotated_signature,
        "rotated_pubs": rotated_pubs,
    }


async def _webkeeper_recover(fixture, *, clear):
    webkeeping = importlib.import_module("keri.app.webkeeping")
    keeper = webkeeping.WebKeeper(name=fixture["name"])
    await keeper.reopen(storageOpener=_open_worker_storage)
    signature, pubs = _keeper_sign(keeper, fixture["pre"])
    _require(signature == fixture["rotated_signature"], "WebKeeper rotated signature changed after worker restart")
    _require(pubs == fixture["rotated_pubs"], "WebKeeper rotated pubs changed after worker restart")
    await keeper.aclose(clear=clear)


async def _webkeeper_absent(fixture):
    webkeeping = importlib.import_module("keri.app.webkeeping")
    keeper = webkeeping.WebKeeper(name=fixture["name"])
    await keeper.reopen(storageOpener=_open_worker_storage)
    _require(keeper.prms.get(fixture["pre"]) is None, "WebKeeper params survived clear")
    _require(keeper.sits.get(fixture["pre"]) is None, "WebKeeper state survived clear")
    await keeper.aclose(clear=True)


async def _open_habery(name, *, clear=False):
    habbing = importlib.import_module("keri.app.habbing")
    webkeeping = importlib.import_module("keri.app.webkeeping")
    webbasing = importlib.import_module("keri.db.webbasing")
    keeper = webkeeping.WebKeeper(name=name)
    baser = webbasing.WebBaser(name=name)
    await keeper.reopen(clear=clear, storageOpener=_open_worker_storage)
    await baser.reopen(clear=clear, storageOpener=_open_worker_storage)
    hby = habbing.Habery(
        name=name,
        ks=keeper,
        db=baser,
        cf=vaulting.NullConfiger(),
        temp=False,
        salt=ROOT_SALT,
    )
    return habbing, hby


async def _open_v2_habery(name, *, clear=False):
    habbing = importlib.import_module("keri.app.habbing")
    keri = importlib.import_module("keri")
    webkeeping = importlib.import_module("keri.app.webkeeping")
    webbasing = importlib.import_module("keri.db.webbasing")
    keeper = webkeeping.WebKeeper(name=name)
    baser = webbasing.WebBaser(name=name)
    await keeper.reopen(clear=clear, storageOpener=_open_worker_storage)
    await baser.reopen(clear=clear, storageOpener=_open_worker_storage)
    hby = habbing.Habery(
        name=name,
        ks=keeper,
        db=baser,
        cf=vaulting.NullConfiger(),
        temp=False,
        salt=ROOT_SALT,
        version=keri.Vrsn_2_0,
    )
    return habbing, hby


def _nested_identity(message, *, expected):
    keri = importlib.import_module("keri")
    parsing = importlib.import_module("keri.core.parsing")
    parsed = parsing.Parser(version=keri.Vrsn_2_0).parse(
        ims=bytearray(message),
        framed=True,
        processive=False,
    )
    _require(len(parsed) == 1, "nested EXN serialization did not contain one outer message")
    outer = parsed[0]
    _require(len(outer.nests) == 1, "nested EXN serialization did not contain one child")
    child = outer.nests[0].serder
    identity = {
        "outer_said": outer.serder.said,
        "route": outer.serder.ked["r"],
        "sender": outer.serder.pre,
        "child_said": child.said,
        "child_prefix": child.pre,
        "child_ilk": child.ilk,
    }
    _require(identity == expected, f"nested EXN identity mismatch: {identity}")
    return identity


async def _nested_create(name):
    keri = importlib.import_module("keri")
    eventing = importlib.import_module("keri.core.eventing")
    parsing = importlib.import_module("keri.core.parsing")
    exchanging = importlib.import_module("keri.peer.exchanging")
    _, hby = await _open_v2_habery(name, clear=True)
    alias = f"{name}-sender"
    replay_name = f"{name}-replay"
    hab = hby.makeHab(
        name=alias,
        icount=1,
        isith="1",
        ncount=1,
        nsith="1",
        transferable=True,
        version=keri.Vrsn_2_0,
        kind=keri.Kinds.json,
    )

    child_stream = hab.msgOwnEvent(sn=0, framed=True, gvrsn=keri.Vrsn_2_0)
    children = parsing.Parser(version=keri.Vrsn_2_0).parse(
        ims=bytearray(child_stream),
        framed=True,
        processive=False,
    )
    _require(len(children) == 1, "sender inception did not parse as one nested child")
    child = children[0]
    nest = exchanging.serializeParsedSubstream(child, gvrsn=keri.Vrsn_2_0)
    route = "/fortweb/test/nested-v2"
    outer = eventing.exchange(
        sender=hab.pre,
        route=route,
        attributes={"purpose": "webbaser-v2-worker-replay"},
        version=keri.Vrsn_2_0,
        gvrsn=keri.Vrsn_2_0,
        kind=keri.Kinds.json,
    )
    signed = hab.endorse(
        outer,
        framed=False,
        gvrsn=keri.Vrsn_2_0,
        nests=[nest],
    )
    exchanger = exchanging.Exchanger(hby=hby, handlers=[])
    parsing.Parser(version=keri.Vrsn_2_0).parse(
        ims=bytearray(signed),
        kvy=hby.kvy,
        exc=exchanger,
    )
    stored_outer = hby.db.exns.get(keys=(outer.said,))
    stored_nests = hby.db.enst.get(keys=(outer.said,))
    _require(stored_outer is not None, "accepted nested EXN was not stored")
    _require(len(stored_nests) == 1, "accepted nested EXN did not store one child")

    expected = {
        "outer_said": outer.said,
        "route": route,
        "sender": hab.pre,
        "child_said": child.serder.said,
        "child_prefix": child.serder.pre,
        "child_ilk": child.serder.ilk,
    }
    rebuilt = exchanging.serializeMessage(hby, outer.said, framed=True)
    _require(rebuilt is not None, "accepted nested EXN could not be serialized from storage")
    _nested_identity(rebuilt, expected=expected)
    fixture = {
        "name": name,
        "replay_name": replay_name,
        "alias": alias,
        **expected,
        "stored_sha256": hashlib.sha256(rebuilt).hexdigest(),
    }
    await vaulting.close_habery(hby, clear=False)
    return fixture


async def _nested_recover(fixture):
    keri = importlib.import_module("keri")
    parsing = importlib.import_module("keri.core.parsing")
    exchanging = importlib.import_module("keri.peer.exchanging")
    _, hby = await _open_v2_habery(fixture["name"])
    replay_hby = None
    try:
        hab = hby.habByName(fixture["alias"])
        _require(hab is not None and hab.pre == fixture["sender"], "nested sender habitat did not survive worker death")
        _require(hby.db.exns.get(keys=(fixture["outer_said"],)) is not None, "nested outer EXN did not survive worker death")
        stored_nests = hby.db.enst.get(keys=(fixture["outer_said"],))
        _require(len(stored_nests) == 1, "nested child did not survive worker death")
        rebuilt = exchanging.serializeMessage(hby, fixture["outer_said"], framed=True)
        _require(rebuilt is not None, "recovered nested EXN could not be serialized")
        _require(hashlib.sha256(rebuilt).hexdigest() == fixture["stored_sha256"], "recovered nested EXN bytes changed")
        expected = {key: fixture[key] for key in (
            "outer_said", "route", "sender", "child_said", "child_prefix", "child_ilk"
        )}
        _nested_identity(rebuilt, expected=expected)

        sender_inception = hab.msgOwnEvent(sn=0, framed=True, gvrsn=keri.Vrsn_2_0)
        _, replay_hby = await _open_v2_habery(fixture["replay_name"], clear=True)
        parsing.Parser(version=keri.Vrsn_2_0).parse(
            ims=bytearray(sender_inception),
            kvy=replay_hby.kvy,
            local=True,
        )
        _require(fixture["sender"] in replay_hby.kevers, "replay Habery did not accept recovered sender inception")
        replay_exchanger = exchanging.Exchanger(hby=replay_hby, handlers=[])
        parsing.Parser(version=keri.Vrsn_2_0).parse(
            ims=bytearray(rebuilt),
            framed=True,
            kvy=replay_hby.kvy,
            exc=replay_exchanger,
        )
        _require(replay_hby.db.exns.get(keys=(fixture["outer_said"],)) is not None, "replayed outer EXN was not stored")
        _require(len(replay_hby.db.enst.get(keys=(fixture["outer_said"],))) == 1, "replayed nested child was not stored")
        replayed = exchanging.serializeMessage(replay_hby, fixture["outer_said"], framed=True)
        _require(replayed is not None, "replayed nested EXN could not be serialized")
        _require(hashlib.sha256(replayed).hexdigest() == fixture["stored_sha256"], "replayed nested EXN bytes changed")
        _nested_identity(replayed, expected=expected)
    finally:
        if replay_hby is not None:
            await vaulting.close_habery(replay_hby, clear=True)
        await vaulting.close_habery(hby, clear=True)
    return fixture


async def _nested_absent(fixture):
    for name, original in ((fixture["name"], True), (fixture["replay_name"], False)):
        _, hby = await _open_v2_habery(name)
        try:
            _require(hby.db.exns.get(keys=(fixture["outer_said"],)) is None, f"{name}: nested outer EXN survived clear")
            _require(not hby.db.enst.get(keys=(fixture["outer_said"],)), f"{name}: nested child survived clear")
            _require(fixture["sender"] not in hby.kevers, f"{name}: sender Kever survived clear")
            _require(hby.db.evts.get(keys=(fixture["sender"], fixture["child_said"])) is None, f"{name}: sender KEL survived clear")
            if original:
                _require(hby.habByName(fixture["alias"]) is None, "nested sender habitat survived clear")
                _require(hby.ks.prms.get(fixture["sender"]) is None, "nested sender keeper params survived clear")
                _require(hby.ks.sits.get(fixture["sender"]) is None, "nested sender keeper state survived clear")
        finally:
            await vaulting.close_habery(hby, clear=True)


def _capture_habery(hby, name, alias):
    hab = hby.habByName(alias)
    _require(hab is not None, f"Habery missing alias {alias}")
    cigar = hab.sign(SIGN_BYTES, indexed=False)[0]
    _require(hab.kever.verfers[0].verify(cigar.raw, SIGN_BYTES), "Habery signature failed")
    said = hab.kever.serder.said
    _require(hby.db.evts.get(keys=(hab.pre, said)) is not None, "Habery latest KEL event missing")
    _require(hby.ks.prms.get(hab.pre) is not None, "Habery keeper params missing")
    _require(hby.ks.sits.get(hab.pre) is not None, "Habery keeper state missing")
    return {"name": name, "alias": alias, "pre": hab.pre, "said": said, "sn": hab.kever.sn, "signature": cigar.qb64}


def _check_habery(hby, fixture, phase):
    hab = hby.habByName(fixture["alias"])
    _require(hab is not None and hab.pre == fixture["pre"], f"{phase}: Habery identifier missing")
    _require(hab.kever.sn == fixture["sn"], f"{phase}: Habery KEL sequence mismatch")
    _require(hab.kever.serder.said == fixture["said"], f"{phase}: Habery latest SAID mismatch")
    _require(hab.kever.state().d == fixture["said"], f"{phase}: Habery key state mismatch")
    _require(hby.db.evts.get(keys=(fixture["pre"], fixture["said"])) is not None, f"{phase}: Habery KEL event missing")
    _require(hby.ks.prms.get(fixture["pre"]) is not None, f"{phase}: Habery keeper params missing")
    _require(hby.ks.sits.get(fixture["pre"]) is not None, f"{phase}: Habery keeper state missing")
    cigar = hab.sign(SIGN_BYTES, indexed=False)[0]
    _require(hab.kever.verfers[0].verify(cigar.raw, SIGN_BYTES), f"{phase}: Habery recovered signature failed")
    _require(cigar.qb64 == fixture["signature"], f"{phase}: Habery recovered signature changed")


async def _habery_create(name):
    _, hby = await _open_habery(name, clear=True)
    alias = f"{name}-aid"
    hab = hby.makeHab(name=alias, icount=1, isith="1", ncount=1, nsith="1")
    first_said = hab.kever.serder.said
    hab.rotate()
    fixture = _capture_habery(hby, name, alias)
    _require(fixture["sn"] == 1, f"Habery expected rotation sequence 1, got {fixture['sn']}")
    _require(fixture["said"] != first_said, "Habery rotation did not change SAID")
    await vaulting.close_habery(hby, clear=False)

    _, hby = await _open_habery(name)
    _check_habery(hby, fixture, "same-worker-reopen")
    await vaulting.close_habery(hby, clear=False)
    return fixture


async def _habery_recover(fixture, *, clear):
    _, hby = await _open_habery(fixture["name"])
    _check_habery(hby, fixture, "new-worker")
    await vaulting.close_habery(hby, clear=clear)


async def _habery_absent(fixture):
    _, hby = await _open_habery(fixture["name"])
    _require(hby.habByName(fixture["alias"]) is None, "Habery identifier survived clear")
    _require(fixture["pre"] not in hby.kevers, "Habery Kever survived clear")
    await vaulting.close_habery(hby, clear=True)


async def _check_partial_close_retry():
    class ProbeStore:
        temp = False

        def __init__(self, name, calls, *, fail=False):
            self.name = name
            self.calls = calls
            self.fail = fail

        async def aclose(self, *, clear=False):
            self.calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} close failed")

    class ProbeConfig:
        temp = False

        def __init__(self, calls, *, fail=False):
            self.calls = calls
            self.fail = fail

        def close(self, *, clear=False):
            self.calls.append("config")
            if self.fail:
                raise RuntimeError("config close failed")

    original_state = vaulting._STATE
    expected_calls = ["keeper", "baser", "config"]
    try:
        for failure in expected_calls:
            calls = []
            keeper = ProbeStore("keeper", calls, fail=failure == "keeper")
            baser = ProbeStore("baser", calls, fail=failure == "baser")
            config = ProbeConfig(calls, fail=failure == "config")
            state = {"hby": SimpleNamespace(ks=keeper, db=baser, cf=config)}
            vaulting._STATE = state

            try:
                await vaulting.close_state()
            except RuntimeError as error:
                _require(str(error) == f"{failure} close failed", "close_state surfaced the wrong persistence error")
            else:
                raise AssertionError(f"close_state swallowed the {failure} persistence error")

            _require(calls == expected_calls, f"close_state skipped a store after {failure} failed: {calls}")
            _require(vaulting._STATE is state, f"close_state discarded state after {failure} failed")

            keeper.fail = False
            baser.fail = False
            config.fail = False
            await vaulting.close_state()
            _require(calls == expected_calls * 2, f"close_state did not retry every store after {failure} failed")
            _require(vaulting._STATE is None, "close_state retained state after a successful retry")
    finally:
        vaulting._STATE = original_state


def _check_witness_receipt_ingestion():
    onboarding = importlib.import_module("onboarding")
    eventing = importlib.import_module("keri.core.eventing")
    kering = importlib.import_module("keri.kering")
    serdering = importlib.import_module("keri.core.serdering")
    event = serdering.SerderKERI(raw=bytearray(RECEIPT_EVENT_RAW))
    witness = "BLLRWMx2FlA19DbZqP19nZbmP-LmAcFpdV1ANLOYYt2C"

    class EventStore:
        def get(self, keys):
            return event

    class WitnessReceiptStore:
        def __init__(self):
            self.rows = []

        def add(self, keys, val):
            self.rows.append((keys, val))
            return True

    def make_hab():
        db = SimpleNamespace(evts=EventStore(), wigs=WitnessReceiptStore())
        return SimpleNamespace(
            pre=event.pre,
            db=db,
            kever=SimpleNamespace(wits=[witness]),
        )

    prior_config = dict(vaulting._CONFIG)
    try:
        vaulting._CONFIG["load_modules"] = lambda: {
            "eventing": eventing,
            "kering": kering,
            "serdering": serdering,
        }
        hab = make_hab()
        _require(
            onboarding._ingest_witness_receipt_fallback(
                hab,
                {"eid": witness},
                RECEIPT_RESPONSE_RAW,
            ),
            "valid mixed-version witness receipt was not ingested",
        )
        _require(len(hab.db.wigs.rows) == 1, "valid witness receipt was not stored exactly once")
        _require(hab.db.wigs.rows[0][1].index == 0, "stored witness receipt used the wrong witness index")

        invalid = RECEIPT_RESPONSE_RAW.replace(b"0BAtiff", b"0BBtiff", 1)
        invalid_hab = make_hab()
        _require(
            not onboarding._ingest_witness_receipt_fallback(
                invalid_hab,
                {"eid": witness},
                invalid,
            ),
            "invalid witness signature was accepted",
        )
        _require(not invalid_hab.db.wigs.rows, "invalid witness signature was stored")
    finally:
        vaulting._CONFIG.clear()
        vaulting._CONFIG.update(prior_config)


async def _check_witness_receipt_aggregation():
    onboarding = importlib.import_module("onboarding")
    kering = importlib.import_module("keri.kering")
    eventing = importlib.import_module("keri.core.eventing")
    serdering = importlib.import_module("keri.core.serdering")
    signing = importlib.import_module("keri.core.signing")
    _, hby = await _open_habery(f"receipt-aggregation-{uuid4().hex}", clear=True)
    original_config = dict(vaulting._CONFIG)
    original_onboarding_config = dict(onboarding._CONFIG)
    original_fetch = transporting.fetch_response
    original_response_bytes = transporting.response_bytes
    original_promote = onboarding._promote_non_witness_receipt
    original_fallback = onboarding._ingest_witness_receipt_fallback

    def reject_fallback(*args, **kwargs):
        raise AssertionError("Valid receipts must reach WebBaser through the normal parser")

    async def response_bytes(response):
        return response.raw

    def indices(hab, said):
        return [s.index for s in onboarding._get_witness_receipts(hab.db, hab.pre, said)]

    async def check_recipients(hab, event, msg, signers, raw_receipts, encoding):
        recipients = {}
        locations = {}
        rows = []
        opened = []

        def receive(recipient, raw, *, local=False):
            recipient.psr.parse(
                ims=bytearray(raw), local=local,
                version=transporting._kf_reply_parser_version(raw),
            )
            recipient.kvy.processEscrows()
            recipient.rvy.processEscrowReply()

        def check_state(recipient, label):
            kever = recipient.kevers.get(hab.pre)
            _require(kever is not None and kever.serder.said == event.said, f"{label}: account rotation missing")
            _require(kever.wits == witnesses and kever.toader.num == 3, f"{label}: witness policy changed")
            wigs = onboarding._get_witness_receipts(recipient.db, hab.pre, event.said)
            _require(sorted(wig.index for wig in wigs) == [0, 1, 2, 3], f"{label}: peer receipts missing or duplicated")
            for eid, (serder, url) in locations.items():
                loc = recipient.db.locs.get(keys=(eid, "http"))
                said = recipient.db.lans.get(keys=(eid, "http"))
                _require(loc is not None and loc.url == url, f"{label}: witness endpoint missing")
                _require(said is not None and said.qb64 == serder.said, f"{label}: original endpoint reply was replaced")
                cigars = recipient.db.scgs.get(keys=(serder.said,))
                _require(len(cigars) == 1, f"{label}: signed endpoint proof missing")
                verfer, cigar = cigars[0]
                _require(verfer.qb64 == eid and verfer.verify(cigar.raw, serder.raw), f"{label}: endpoint signer changed")

        try:
            for index, signer in enumerate(signers):
                _, recipient = await _open_habery(f"{encoding}-witness-{index}-{uuid4().hex}", clear=True)
                opened.append(recipient)
                whab = recipient.makeHab(
                    name=f"witness-{index}", transferable=False,
                    secrecies=[[signer.qb64]],
                )
                _require(whab.pre == witnesses[index], "Recipient witness key does not match allocation")
                recipients[whab.pre] = recipient
                url = f"http://localhost:{5700 + index}"
                rows.append({"eid": whab.pre, "witnessUrl": url, "totpSeed": "JBSWY3DPEHPK3PXP"})
                location = eventing.reply(
                    pre=whab.pre, route="/loc/scheme", data=dict(eid=whab.pre, scheme="http", url=url),
                    version=event.pvrsn, kind=event.kind,
                )
                raw = eventing.messagize(
                    location, cigars=[signer.sign(ser=location.raw, indexed=False)],
                    gvrsn=event.pvrsn,
                )
                receive(hby, raw)
                receive(recipient, raw)
                locations[whab.pre] = (location, url)
                receive(recipient, onboarding._msg_own_inception(hab))
                receive(recipient, msg, local=True)
                whab.witness(event, kind=event.kind, version=event.pvrsn, gvrsn=event.pvrsn)
                wigs = onboarding._get_witness_receipts(recipient.db, hab.pre, event.said)
                _require([wig.index for wig in wigs] == [index], "Witness fixture must start with only its own receipt")
                _require(sum(recipient.db.locs.get(keys=(eid, "http")) is not None for eid in witnesses) == 1,
                         "Witness fixture must start without peer endpoints")

            _, watcher = await _open_habery(f"{encoding}-watcher-{uuid4().hex}", clear=True)
            opened.append(watcher)
            watcher_hab = watcher.makeHab(name="watcher", transferable=False, algo="randy")
            watcher_row = {"eid": watcher_hab.pre, "watcherUrl": "http://localhost:5704"}
            recipients[watcher_hab.pre] = watcher

            async def fetch(url, *, method="GET", headers=None, body=None, **kwargs):
                destination = headers[transporting.CESR_DESTINATION_HEADER]
                recipient = recipients[destination]
                raw = body.encode("utf-8")
                attachment = headers.get(transporting.CESR_ATTACHMENT_HEADER, "")
                if destination in witnesses:
                    if method == "PUT":
                        return SimpleNamespace(status=204, raw=b"")
                    _require(method == "POST", "Witness transport requires POST")
                    _require(json.loads(raw)["t"] in {"rot", "rct", "rpy"}, "Witness POST body must be one event")
                    receive(recipient, raw + attachment.encode("utf-8"), local=url.endswith("/receipts"))
                    if url.endswith("/receipts"):
                        response = raw_receipts[witnesses.index(destination)]
                        return SimpleNamespace(status=200, raw=response)
                    return SimpleNamespace(status=204, raw=b"")
                _require(method == "PUT", "Watcher transport requires PUT")
                receive(recipient, raw)
                return SimpleNamespace(status=204, raw=b"")

            transporting.fetch_response = fetch
            await onboarding._rotate_kf_account_to_witnesses(hab, rows, toad=3)
            for eid in witnesses:
                check_state(recipients[eid], f"{encoding} witness {eid}")
            await onboarding._introduce_account_to_watcher(hab, watcher_row, rows)
            check_state(watcher, f"{encoding} watcher")
            observed = watcher.db.obvs.get(keys=(hab.pre, watcher_hab.pre, hab.pre))
            _require(observed is not None and observed.enabled, "Watcher did not accept the watch request")
        finally:
            for recipient in reversed(opened):
                await vaulting.close_habery(recipient, clear=True)

    try:
        vaulting._CONFIG["load_modules"] = lambda: {
            "kering": kering, "eventing": eventing, "serdering": serdering,
        }
        onboarding._CONFIG["witness_registration_timeout_ms"] = 1000
        onboarding._CONFIG["cesr_timeout_ms"] = 1000
        transporting.response_bytes = response_bytes
        onboarding._promote_non_witness_receipt = reject_fallback
        onboarding._ingest_witness_receipt_fallback = reject_fallback
        signers = [
            signing.Signer(
                raw=hashlib.sha256(f"receipt-witness-{i}".encode()).digest(),
                transferable=False,
            )
            for i in range(4)
        ]
        witnesses = [signer.verfer.qb64 for signer in signers]

        for encoding in ("modern", "legacy", "mixed"):
            raw_receipts = {}
            hab = onboarding._create_or_load_kf_account_hab(
                hby, onboarding.KfVaultState(), alias=encoding, requested_account_aid="",
            )
            msg = bytes(hab.rotate(adds=witnesses, toad=3))
            event = hab.kever.serder
            _require(hab.psr.kvy.fetchWitnessState(hab.pre, 0) == [], "Inception gained later witnesses")
            _require(
                [wit.qb64 for wit in hab.psr.kvy.fetchWitnessState(hab.pre, 1)] == witnesses,
                "Rotation witness state does not match the allocated pool",
            )

            async def submit(index, *, valid=True):
                pvrsn = kering.Vrsn_1_0 if encoding == "legacy" else kering.Vrsn_2_0
                receipt = eventing.receipt(
                    pre=hab.pre, sn=event.sn, said=event.said,
                    version=pvrsn, kind=kering.Kinds.json,
                )
                signer = signers[index]
                cigar = signer.sign(ser=event.raw if valid else event.raw + b"invalid", indexed=False)
                if encoding == "mixed":
                    inner = (
                        eventing.Counter.makeGVC(version=kering.Vrsn_1_0)
                        + eventing.Counter(eventing.Codens.NonTransReceiptCouples, count=1, version=kering.Vrsn_1_0).qb64b
                        + signer.verfer.qb64b + cigar.qb64b
                    )
                    raw = receipt.raw + eventing.Counter.enclose(
                        qb64=inner, code=eventing.Codens.AttachmentGroup, version=kering.Vrsn_2_0,
                    )
                else:
                    raw = bytes(eventing.messagize(receipt, cigars=[cigar], gvrsn=pvrsn))

                if valid:
                    raw_receipts[index] = raw

                async def fetch(*args, **kwargs):
                    return SimpleNamespace(status=200, raw=raw)

                transporting.fetch_response = fetch
                await onboarding._submit_witness_rotation_receipt(
                    hab, {"eid": witnesses[index], "witnessUrl": "http://localhost:5632"}, "test", msg,
                )

            for index in range(3):
                await submit(index)
                _require(indices(hab, event.said) == list(range(index + 1)), f"{encoding}: witness {index} receipt missing")
            _require(len(indices(hab, event.said)) >= hab.kever.toader.num, f"{encoding}: receipts below TOAD")
            await submit(3, valid=False)
            _require(indices(hab, event.said) == [0, 1, 2], f"{encoding}: invalid signature was counted")
            await submit(3)
            _require(indices(hab, event.said) == [0, 1, 2, 3], f"{encoding}: fourth receipt missing")
            await submit(3)
            _require(indices(hab, event.said) == [0, 1, 2, 3], f"{encoding}: repeated receipt was counted twice")
            if encoding == "modern":
                await check_recipients(hab, event, msg, signers, raw_receipts, encoding)
            hab.interact()
            _require(
                [wit.qb64 for wit in hab.psr.kvy.fetchWitnessState(hab.pre, 2)] == witnesses,
                f"{encoding}: interaction did not inherit establishment witnesses",
            )
    finally:
        vaulting._CONFIG.clear()
        vaulting._CONFIG.update(original_config)
        onboarding._CONFIG.clear()
        onboarding._CONFIG.update(original_onboarding_config)
        transporting.fetch_response = original_fetch
        transporting.response_bytes = original_response_bytes
        onboarding._promote_non_witness_receipt = original_promote
        onboarding._ingest_witness_receipt_fallback = original_fallback
        await vaulting.close_habery(hby, clear=True)


async def _await_oobi(hby, oobiery, url):
    oobiing = importlib.import_module("keri.app.oobiing")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    while loop.time() < deadline:
        oobiery.processFlows()
        record = hby.db.roobi.get(keys=(url,))
        if record is not None:
            _require(record.state == oobiing.Result.resolved, f"OOBI failed with state {record.state}")
            return record
        await asyncio.sleep(0.05)
    raise AssertionError(f"BrowserClienter OOBI resolution timed out for {url}")


def _check_oobi(hby, fixture, phase, *, require_kever=True):
    oobiing = importlib.import_module("keri.app.oobiing")
    organizing = importlib.import_module("keri.app.organizing")
    record = hby.db.roobi.get(keys=(fixture["url"],))
    _require(record is not None and record.state == oobiing.Result.resolved, f"{phase}: resolved OOBI missing")
    _require(record.cid == fixture["remote_pre"], f"{phase}: OOBI cid mismatch")
    organizer = organizing.Organizer(hby=hby)
    remote = vaulting._remote_detail_record(hby, organizer, fixture["remote_pre"])
    _require(remote["status"] == "Resolved", f"{phase}: persisted remote shown as unresolved")
    _require(remote["sequenceNumber"] == fixture["sn"], f"{phase}: remote sequence changed")
    _require(remote["transferable"] == fixture["transferable"], f"{phase}: remote transferability changed")
    _require(remote["lastEventDigest"] == fixture["digest"], f"{phase}: remote event digest changed")
    if require_kever:
        _require(fixture["remote_pre"] in hby.kevers, f"{phase}: remote Kever missing")
    contact = organizer.get(fixture["remote_pre"])
    _require(contact is not None, f"{phase}: organizer contact missing")
    for field in ("alias", "oobi", "company", "org", "note"):
        _require(contact.get(field) == fixture[field], f"{phase}: organizer {field} mismatch")


async def _oobi_create(name):
    oobiing = importlib.import_module("keri.app.oobiing")
    organizing = importlib.import_module("keri.app.organizing")
    recording = importlib.import_module("keri.recording")
    _, hby = await _open_habery(name, clear=True)
    alias = f"{name}-remote"
    original_config = dict(vaulting._CONFIG)
    vaulting._CONFIG["load_modules"] = lambda: {"oobiing": oobiing}
    try:
        url = vaulting.require_oobi_url(f"{_origin()}/oobi/{OOBI_AID}/controller?name={alias}")
    finally:
        vaulting._CONFIG.clear()
        vaulting._CONFIG.update(original_config)
    record = recording.OobiRecord(date=oobiing.nowIso8601(), oobialias=alias)
    hby.db.oobis.pin(keys=(url,), val=record)
    oobiery = oobiing.Oobiery(hby=hby, clienter=transporting.BrowserClienter())
    resolved = await _await_oobi(hby, oobiery, url)
    kever = hby.kevers[resolved.cid]
    fixture = {
        "name": name,
        "url": url,
        "remote_pre": resolved.cid,
        "sn": kever.sn,
        "transferable": kever.transferable,
        "digest": kever.serder.said,
        "alias": alias,
        "oobi": url,
        "company": CONTACT_COMPANY,
        "org": CONTACT_ORG,
        "note": CONTACT_NOTE,
    }
    organizing.Organizer(hby=hby).update(
        resolved.cid,
        {key: fixture[key] for key in ("alias", "oobi", "company", "org", "note")},
    )
    _check_oobi(hby, fixture, "create")
    await vaulting.close_habery(hby, clear=False)
    return fixture


async def _oobi_recover(fixture, *, clear):
    _, hby = await _open_habery(fixture["name"])
    _check_oobi(hby, fixture, "new-worker")
    await vaulting.close_habery(hby, clear=clear)


async def _oobi_absent(fixture):
    organizing = importlib.import_module("keri.app.organizing")
    _, hby = await _open_habery(fixture["name"])
    _require(hby.db.roobi.get(keys=(fixture["url"],)) is None, "resolved OOBI survived clear")
    _require(organizing.Organizer(hby=hby).get(fixture["remote_pre"]) is None, "organizer contact survived clear")
    _require(fixture["remote_pre"] not in hby.kevers, "remote Kever survived clear")
    await vaulting.close_habery(hby, clear=True)


async def _create(names):
    await _check_partial_close_retry()
    _check_witness_receipt_ingestion()
    await _check_witness_receipt_aggregation()
    fixtures = {
        "webdber": await _webdber_create(names["webdber"]),
        "webbaser": await _webbaser_create(names["webbaser"]),
        "webkeeper": await _webkeeper_create(names["webkeeper"]),
        "habery": await _habery_create(names["habery"]),
        "oobi": await _oobi_create(names["oobi"]),
        "nested": await _nested_create(names["nested"]),
    }
    return {
        "fixtures": fixtures,
        "checks": [
            "same-worker-close-reopen",
            "pending-write-awaited-close",
            "webkeeper-create-sign-rotate",
            "habery-create-rotate",
            "browserclienter-http-oobi",
            "aggregate-close-partial-failure-retry",
            "mixed-version-witness-receipt-ingestion",
            "multi-witness-receipts-from-parser",
            "nested-v2-exn-stored-from-parser",
        ],
    }


async def _recover(fixtures):
    await _webdber_recover(fixtures["webdber"], clear=True)
    await _webbaser_recover(fixtures["webbaser"], clear=True)
    await _webkeeper_recover(fixtures["webkeeper"], clear=True)
    await _habery_recover(fixtures["habery"], clear=True)
    await _oobi_recover(fixtures["oobi"], clear=True)
    await _nested_recover(fixtures["nested"])
    return {
        "fixtures": fixtures,
        "checks": [
            "new-worker-recovery",
            "webkeeper-sign-after-rotation",
            "habery-kel-key-state-recovery",
            "oobi-contact-recovery",
            "clear-awaited",
            "nested-v2-exn-storage-replay",
        ],
    }


async def _verify_clear(fixtures):
    await _webdber_absent(fixtures["webdber"])
    await _webbaser_absent(fixtures["webbaser"])
    await _webkeeper_absent(fixtures["webkeeper"])
    await _habery_absent(fixtures["habery"])
    await _oobi_absent(fixtures["oobi"])
    await _nested_absent(fixtures["nested"])
    return {"checks": ["third-worker-clear-absence", "nested-v2-exn-clear-absence"]}


async def run_phase(raw_request):
    try:
        request = json.loads(raw_request)
        phase = request["phase"]
        if phase == "probe":
            return json.dumps({"ok": True, "ready": READY_PROBE})

        await _ensure_packages()
        if phase == "create":
            result = await _create(request["names"])
        elif phase == "recover":
            result = await _recover(request["fixtures"])
        elif phase == "verify-clear":
            result = await _verify_clear(request["fixtures"])
        else:
            raise ValueError(f"unknown WebBaser phase {phase!r}")
        return json.dumps(
            {
                "ok": True,
                "phase": phase,
                "worker_id": WORKER_ID,
                "runtime": _runtime_evidence(),
                **result,
            }
        )
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "worker_id": WORKER_ID,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })


__export__ = ["run_phase"]
