# -*- encoding: utf-8 -*-
"""
keri.app.webkeeping module

Browser-safe keeper backed by WebDBer storage.
"""

from __future__ import annotations

import asyncio

from ..core import Cipher, Prefixer, Number
from ..db import (WebDBer, Suber, CryptSignerSuber, CesrSuber,
                  CatCesrIoSetSuber, Komer)

from .keeping import PrePrm, PreSit, PubSet


class WebKeeper(WebDBer):
    """Browser-safe keystore with the subset of Keeper storage used by Manager."""

    SubDbNames = [
        "gbls.",
        "pris.",
        "prxs.",
        "nxts.",
        "smids.",
        "rmids.",
        "pres.",
        "prms.",
        "sits.",
        "pubs.",
    ]

    def __init__(self, name="main", temp=False, reopen=False, **kwa):
        self.name = name
        self.temp = temp
        self.db = None
        self.env = None
        self.opened = False
        self._subdb_names = ()

    async def reopen(self, clear=False, storageOpener=None):
        """Open or re-open the browser-backed keeper stores."""
        if storageOpener is not None:
            self._storageOpener = storageOpener
        opener = getattr(self, "_storageOpener", None)

        try:
            self.db = await WebDBer.open(
                name=self.name,
                stores=self.SubDbNames,
                clear=clear,
                storageOpener=opener,
            )
        except RuntimeError as e:
            if opener is None:
                raise RuntimeError(
                    "No storage opener available. "
                    "Provide storageOpener=FakeStorageBackend.open in CPython, "
                    "or run under PyScript for IndexedDB."
                ) from e
            raise

        self.env = self.db.env
        self._bindSubDbs()
        self.opened = True
        return self.env

    def close(self, *, clear=False):
        """Close the keeper and schedule a best-effort flush."""
        if not self.opened or self.db is None:
            return

        if clear or self.temp:
            for subdb in self.db._stores.values():
                subdb.items.clear()
                subdb.dirty = True

        db = self.db
        self.db = None
        self.env = None
        self.opened = False

        for name in self._subdb_names:
            try:
                delattr(self, name)
            except AttributeError:
                pass

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(db.flush())
        except RuntimeError:
            pass

    async def aclose(self, *, clear=False):
        """Close the keeper and wait for pending writes to flush."""
        if not self.opened or self.db is None:
            return

        if clear or self.temp:
            for subdb in self.db._stores.values():
                subdb.items.clear()
                subdb.dirty = True

        await self.db.flush()
        self.db = None
        self.env = None
        self.opened = False

        for name in self._subdb_names:
            try:
                delattr(self, name)
            except AttributeError:
                pass

    def _bindSubDbs(self):
        """Bind keeper subdb helpers onto the reopened browser-backed env."""
        self.gbls = Suber(db=self, subkey="gbls.")
        self.pris = CryptSignerSuber(db=self, subkey="pris.")
        self.prxs = CesrSuber(db=self, subkey="prxs.", klas=Cipher)
        self.nxts = CesrSuber(db=self, subkey="nxts.", klas=Cipher)
        self.smids = CatCesrIoSetSuber(db=self, subkey="smids.", klas=(Prefixer, Number))
        self.rmids = CatCesrIoSetSuber(db=self, subkey="rmids.", klas=(Prefixer, Number))
        self.pres = CesrSuber(db=self, subkey="pres.", klas=Prefixer)
        self.prms = Komer(db=self, subkey="prms.", klas=PrePrm)
        self.sits = Komer(db=self, subkey="sits.", klas=PreSit)
        self.pubs = Komer(db=self, subkey="pubs.", klas=PubSet)
        self._subdb_names = (
            "gbls",
            "pris",
            "prxs",
            "nxts",
            "smids",
            "rmids",
            "pres",
            "prms",
            "sits",
            "pubs",
        )
