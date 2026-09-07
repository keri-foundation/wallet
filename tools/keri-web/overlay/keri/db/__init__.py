# -*- encoding: utf-8 -*-
"""
keri.db Package
"""

import importlib


_MODULES = {
    "basing": ".basing",
    "dbing": ".dbing",
    "escrowing": ".escrowing",
    "koming": ".koming",
    "subing": ".subing",
    "webdbing": ".webdbing",
}


_EXPORTS = {
    "B64IoDupSuber": (".subing", "B64IoDupSuber"),
    "B64IoSetSuber": (".subing", "B64IoSetSuber"),
    "B64OnIoDupSuber": (".subing", "B64OnIoDupSuber"),
    "B64OnIoSetSuber": (".subing", "B64OnIoSetSuber"),
    "B64Suber": (".subing", "B64Suber"),
    "B64SuberBase": (".subing", "B64SuberBase"),
    "Baser": (".webbasing", "WebBaser"),
    "BaserDoer": (".basing", "BaserDoer"),
    "Broker": (".escrowing", "Broker"),
    "CatCesrDupSuber": (".subing", "CatCesrDupSuber"),
    "CatCesrIoSetSuber": (".subing", "CatCesrIoSetSuber"),
    "CatCesrSuber": (".subing", "CatCesrSuber"),
    "CatCesrSuberBase": (".subing", "CatCesrSuberBase"),
    "CesrDupSuber": (".subing", "CesrDupSuber"),
    "CesrIoSetSuber": (".subing", "CesrIoSetSuber"),
    "CesrOnSuber": (".subing", "CesrOnSuber"),
    "CesrSuber": (".subing", "CesrSuber"),
    "CesrSuberBase": (".subing", "CesrSuberBase"),
    "CryptSignerSuber": (".subing", "CryptSignerSuber"),
    "DupKomer": (".koming", "DupKomer"),
    "DupSuber": (".subing", "DupSuber"),
    "IoDupSuber": (".subing", "IoDupSuber"),
    "IoSetKomer": (".koming", "IoSetKomer"),
    "IoSetSuber": (".subing", "IoSetSuber"),
    "Komer": (".koming", "Komer"),
    "KomerBase": (".koming", "KomerBase"),
    "LMDBer": (".dbing", "LMDBer"),
    "MaxSuffix": (".dbing", "MaxSuffix"),
    "OnIoDupSuber": (".subing", "OnIoDupSuber"),
    "OnIoSetSuber": (".subing", "OnIoSetSuber"),
    "OnSuber": (".subing", "OnSuber"),
    "OnSuberBase": (".subing", "OnSuberBase"),
    "SchemerSuber": (".subing", "SchemerSuber"),
    "SerderIoSetSuber": (".subing", "SerderIoSetSuber"),
    "SerderSuber": (".subing", "SerderSuber"),
    "SerderSuberBase": (".subing", "SerderSuberBase"),
    "SignerSuber": (".subing", "SignerSuber"),
    "Suber": (".subing", "Suber"),
    "SuberBase": (".subing", "SuberBase"),
    "SuffixSize": (".dbing", "SuffixSize"),
    "WebDBer": (".webdbing", "WebDBer"),
    "clearDatabaserDir": (".dbing", "clearDatabaserDir"),
    "dgKey": (".basebasing", "dgKey"),
    "dtKey": (".dbing", "dtKey"),
    "fetchTsgs": (".basebasing", "fetchTsgs"),
    "fnKey": (".dbing", "fnKey"),
    "onKey": (".dbing", "onKey"),
    "openDB": (".basing", "openDB"),
    "openLMDB": (".dbing", "openLMDB"),
    "reopenDB": (".basing", "reopenDB"),
    "snKey": (".basebasing", "snKey"),
    "splitKey": (".dbing", "splitKey"),
    "splitKeyDT": (".dbing", "splitKeyDT"),
    "splitKeyFN": (".dbing", "splitKeyFN"),
    "splitOnKey": (".dbing", "splitOnKey"),
    "splitSnKey": (".dbing", "splitSnKey"),
    "statedict": (".basing", "statedict"),
    "suffix": (".dbing", "suffix"),
    "unsuffix": (".dbing", "unsuffix"),
}

__all__ = [*_MODULES, *_EXPORTS]


def __getattr__(name):
    if name in _MODULES:
        module = importlib.import_module(_MODULES[name], __name__)
        globals()[name] = module
        return module

    if name in _EXPORTS:
        module_name, export_name = _EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        value = getattr(module, export_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
