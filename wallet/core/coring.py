import json
from collections import namedtuple

from keri.core.coring import MtrDex, Number, Sadder, Tholder, Verfer
from keri.kering import Ilks

Identage = namedtuple('Identage', 'keri acdc')

Idents = Identage(keri='KERI', acdc='ACDC')


class Serder(Sadder):
    """
    Serder is versioned protocol key event message serializer-deserializer class

    Only supports current version VERSION

    Has the following public properties:

    Properties:
        .raw is bytes of serialized event only
        .ked is key event dict
        .kind is serialization kind string value (see namedtuple coring.Kinds)
        .version is Versionage instance of event version
        .size is int of number of bytes in serialed event only
        .diger is Diger instance of digest of .raw
        .dig  is qb64 digest from .diger
        .digb is qb64b digest from .diger
        .verfers is list of Verfers converted from .ked["k"]
        .werfers is list of Verfers converted from .ked["b"]
        .tholder is Tholder instance from .ked["kt'] else None
        .ntholder is Tholder instance from .ked["nt'] else None
        sner (Number): instance converted from sequence number .ked["s"] hex str
        sn (int): sequence number converted from .ked["s"]
        .pre is qb64 str of identifier prefix from .ked["i"]
        .preb is qb64b bytes of identifier prefix from .ked["i"]
        .said is qb64 of .ked['d'] if present
        .saidb is qb64b of .ked['d'] of present

    Hidden Attributes:
          ._raw is bytes of serialized event only
          ._ked is key event dict
          ._kind is serialization kind string value (see namedtuple coring.Kinds)
            supported kinds are 'json', 'cbor', 'msgpack', 'binary'
          ._version is Versionage instance of event version
          ._size is int of number of bytes in serialed event only
          ._code is default code for .diger
          ._diger is Diger instance of digest of .raw

    Note:
        loads and jumps of json use str whereas cbor and msgpack use bytes

    """

    def __init__(self, raw=b'', ked=None, kind=None, sad=None, code=MtrDex.Blake3_256):
        """
        Deserialize if raw provided
        Serialize if ked provided but not raw
        When serilaizing if kind provided then use kind instead of field in ked

        Parameters:
          raw is bytes of serialized event plus any attached signatures
          ked is key event dict or None
            if None its deserialized from raw
          sad (Sadder) is clonable base class
          kind is serialization kind string value or None (see namedtuple coring.Kinds)
            supported kinds are 'json', 'cbor', 'msgpack', 'binary'
            if kind is None then its extracted from ked or raw
          code is .diger default digest code

        """
        super(Serder, self).__init__(raw=raw, ked=ked, kind=kind, sad=sad, code=code)

        # if self._ident != Idents.keri:
        #     raise ValueError("Invalid ident {}, must be KERI".format(self._ident))

    @property
    def verfers(self):
        """
        Returns list of Verfer instances as converted from .ked['k'].
        One for each key.
        verfers property getter
        """
        if 'k' in self.ked:  # establishment event
            keys = self.ked['k']
        else:  # non-establishment event
            keys = []

        return [Verfer(qb64=key) for key in keys]

    @property
    def werfers(self):
        """
        Returns list of Verfer instances as converted from .ked['b'].
        One for each backer (witness).
        werfers property getter
        """
        if 'b' in self.ked:  # inception establishment event
            wits = self.ked['b']
        else:  # non-establishment event
            wits = []

        return [Verfer(qb64=wit) for wit in wits]

    @property
    def tholder(self):
        """
        Returns Tholder instance as converted from .ked['kt'] or None if missing.

        """
        return Tholder(sith=self.ked['kt']) if 'kt' in self.ked else None

    @property
    def ntholder(self):
        """
        Returns Tholder instance as converted from .ked['nt'] or None if missing.

        """
        return Tholder(sith=self.ked['nt']) if 'nt' in self.ked else None

    @property
    def sner(self):
        """
        sner (Number of sequence number) property getter
        Returns:
            (Number): of .ked["s"] hex number str converted
        """
        return Number(num=self.ked['s'])  # auto converts hex num str to int

    @property
    def sn(self):
        """
        sn (sequence number) property getter
        Returns:
            sn (int): of .sner.num from .ked["s"]
        """
        return self.sner.num

    @property
    def pre(self):
        """
        Returns str qb64  of .ked["i"] (identifier prefix)
        pre (identifier prefix) property getter
        """
        return self.ked['i']

    @property
    def preb(self):
        """
        Returns bytes qb64b  of .ked["i"] (identifier prefix)
        preb (identifier prefix) property getter
        """
        return self.pre.encode('utf-8')

    @property
    def est(self):  # establishative
        """Returns True if Serder represents an establishment event"""
        return self.ked['t'] in (Ilks.icp, Ilks.rot, Ilks.dip, Ilks.drt)

    def pretty(self, *, size=1024):
        """
        Returns str JSON of .ked with pretty formatting

        ToDo: add default size limit on pretty when used for syslog UDP MCU
        like 1024 for ogler.logger
        """
        return json.dumps(self.ked, indent=1)[: size if size is not None else None]
