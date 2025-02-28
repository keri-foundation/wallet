import json
from dataclasses import dataclass
from typing import Iterable, Type, Union

import cbor2
import msgpack
from keri.core import coring
from keri.db import dbing
from keri.help import helping

from wallet.walleting import OldKeystoreError


class KomerBase:
    """
    KomerBase is a base class for Komer (Keyspace Object Mapper) subclasses that
    each use a dataclass as the object mapped via serialization to an dber LMDB
    database subclass.
    Each Komer .schema is a dataclass class reference that is used to define
    the fields in each database entry. The base class is not meant to be instantiated.
    Use an instance of one of the subclasses instead.

    Attributes:
        db (dbing.LMDBer): instance of LMDB database manager class
        sdb (lmdb._Database): instance of named sub db lmdb for this Komer
        schema (Type[dataclass]): class reference of dataclass subclass
        kind (str): serialization/deserialization type from coring.Kinds
        serializer (types.MethodType): serializer method
        deserializer (types.MethodType): deserializer method
        sep (str): separator for combining keys tuple of strs into key bytes
    """

    Sep = '.'  # separator for combining key iterables

    def __init__(
        self,
        db: dbing.LMDBer,
        *,
        subkey: str = 'docs.',
        schema: Type[dataclass],  # class not instance
        kind: str = coring.Kinds.json,
        dupsort: bool = False,
        sep: str = None,
        **kwa,
    ):
        """
        Parameters:
            db (dbing.LMDBer): base db
            schema (Type[dataclass]):  reference to Class definition for dataclass sub class
            subkey (str):  LMDB sub database key
            kind (str): serialization/deserialization type
            dupsort (bool): True means enable duplicates at each key
                               False (default) means do not enable duplicates at
                               each key
            sep (str): separator to convert keys iterator to key bytes for db key
                       default is self.Sep == '.'
        """
        super(KomerBase, self).__init__()
        self.db = db
        self.sdb = self.db.env.open_db(key=subkey.encode('utf-8'), dupsort=dupsort)
        self.schema = schema
        self.kind = kind
        self.serializer = self._serializer(kind)
        self.deserializer = self._deserializer(kind)
        self.sep = sep if sep is not None else self.Sep

    def _tokey(self, keys: Union[str, bytes, memoryview, Iterable]):
        """
        Converts key to key str with proper separators and returns key bytes.
        If key is already str then returns. Else If key is iterable (non-str)
        of strs then joins with separator converts to bytes and returns

        Parameters:
           keys (Union[str, bytes, Iterable]): str, bytes, or Iterable of str.

        """
        if isinstance(keys, memoryview):  # memoryview of bytes
            return bytes(keys)  # return bytes
        if hasattr(keys, 'encode'):  # str
            return keys.encode('utf-8')  # convert to bytes
        elif hasattr(keys, 'decode'):  # bytes
            return keys  # return as is
        return self.sep.join(keys).encode('utf-8')  # iterable so join

    def _tokeys(self, key: Union[str, bytes, memoryview]):
        """
        Converts key bytes to keys tuple of strs by decoding and then splitting
        at separator.

        Returns:
           keys (iterable): of str

        Parameters:
           key (Union[str, bytes]): str or bytes.

        """
        if isinstance(key, memoryview):  # memoryview of bytes
            key = bytes(key)
        return tuple(key.decode('utf-8').split(self.sep))

    def getItemIter(self, keys: Union[str, Iterable] = b''):
        """
        Returns:
            items (Iterator): of (key, val) tuples  over the all the items in
            subdb whose key startswith key made from keys. Keys may be keyspace
            prefix to return branches of key space. When keys is empty then
            returns all items in subdb

        Parameters:
            keys (Iterator): tuple of bytes or strs that may be a truncation of
                a full keys tuple in  in order to get all the items from
                multiple branches of the key space. If keys is empty then gets
                all items in database.

        """
        for key, val in self.db.getTopItemIter(db=self.sdb, key=self._tokey(keys)):
            yield (self._tokeys(key), self.deserializer(val))

    def _serializer(self, kind):
        """
        Parameters:
            kind (str): serialization
        """
        if kind == coring.Kinds.mgpk:
            return self.__serializeMGPK
        elif kind == coring.Kinds.cbor:
            return self.__serializeCBOR
        else:
            return self.__serializeJSON

    def _deserializer(self, kind):
        """
        Parameters:
            kind (str): deserialization
        """
        if kind == coring.Kinds.mgpk:
            return self.__deserializeMGPK
        elif kind == coring.Kinds.cbor:
            return self.__deserializeCBOR
        else:
            return self.__deserializeJSON

    def __deserializeJSON(self, val):
        if val is not None:
            val = helping.datify(self.schema, json.loads(bytes(val).decode('utf-8')))
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
        return val

    def __deserializeMGPK(self, val):
        if val is not None:
            val = helping.datify(self.schema, msgpack.loads(bytes(val)))
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
        return val

    def __deserializeCBOR(self, val):
        if val is not None:
            val = helping.datify(self.schema, cbor2.loads(bytes(val)))
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
        return val

    def __serializeJSON(self, val):
        if val is not None:
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
            val = json.dumps(helping.dictify(val), separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return val

    def __serializeMGPK(self, val):
        if val is not None:
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
            val = msgpack.dumps(helping.dictify(val))
        return val

    def __serializeCBOR(self, val):
        if val is not None:
            if not isinstance(val, self.schema):
                raise ValueError('Invalid schema type={} of value={}, expected {}.'.format(type(val), val, self.schema))
            val = cbor2.dumps(helping.dictify(val))
        return val


class Komer(KomerBase):
    """
    Keyspace Object Mapper factory class.
    """

    def __init__(
        self,
        db: dbing.LMDBer,
        *,
        subkey: str = 'docs.',
        schema: Type[dataclass],  # class not instance
        kind: str = coring.Kinds.json,
        **kwa,
    ):
        """
        Parameters:
            db (dbing.LMDBer): base db
            schema (Type[dataclass]):  reference to Class definition for dataclass sub class
            subkey (str):  LMDB sub database key
            kind (str): serialization/deserialization type
        """
        super(Komer, self).__init__(db=db, subkey=subkey, schema=schema, kind=kind, dupsort=False, **kwa)

    def put(self, keys: Union[str, Iterable], val: dataclass):
        """
        Puts val at key made from keys. Does not overwrite

        Parameters:
            keys (tuple): of key strs to be combined in order to form key
            val (dataclass): instance of dataclass of type self.schema as value

        Returns:
            result (bool): True If successful, False otherwise, such as key
                              already in database.
        """
        return self.db.putVal(db=self.sdb, key=self._tokey(keys), val=self.serializer(val))

    def pin(self, keys: Union[str, Iterable], val: dataclass):
        """
        Pins (sets) val at key made from keys. Overwrites.

        Parameters:
            keys (tuple): of key strs to be combined in order to form key
            val (dataclass): instance of dataclass of type self.schema as value

        Returns:
            result (bool): True If successful. False otherwise.
        """
        return self.db.setVal(db=self.sdb, key=self._tokey(keys), val=self.serializer(val))

    def get(self, keys: Union[str, Iterable]):
        """
        Gets val at keys

        Parameters:
            keys (tuple): of key strs to be combined in order to form key

        Returns:
            val (dataclass):
            None if no entry at keys

        Usage:
            Use walrus operator to catch and raise missing entry
            if (val := mydb.get(keys)) is None:
                raise ExceptionHere
            use val here
        """
        return self.deserializer(self.db.getVal(db=self.sdb, key=self._tokey(keys)))

    def get_expect_type(self, keys: Union[str, Iterable], klas: Type):
        """
        Gets val at keys

        Parameters:
            keys (tuple): of key strs to be combined in order to form key

        Returns:
            val (dataclass):
            None if no entry at keys

        Usage:
            Use walrus operator to catch and raise missing entry
            if (val := mydb.get(keys)) is None:
                raise ExceptionHere
            use val here
        """
        val = self.db.getVal(db=self.sdb, key=self._tokey(keys))
        if val is not None:
            val = helping.datify(self.schema, json.loads(bytes(val).decode('utf-8')))
            if not isinstance(val, klas):
                raise OldKeystoreError(f'Invalid data type={type(val)}, expected {klas} for value={val}.')
        return val

    def rem(self, keys: Union[str, Iterable]):
        """
        Removes entry at keys

        Parameters:
            keys (tuple): of key strs to be combined in order to form key

        Returns:
           result (bool): True if key exists so delete successful. False otherwise
        """
        return self.db.delVal(db=self.sdb, key=self._tokey(keys))

    def trim(self, keys: Union[str, Iterable] = b''):
        """
        Removes all entries whose keys startswith keys. Enables removal of whole
        branches of db key space. To ensure that proper separation of a branch
        include empty string as last key in keys. For example ("a","") deletes
        'a.1'and 'a.2' but not 'ab'

        Parameters:
            keys (tuple): of key strs to be combined in order to form key

        Returns:
           result (bool): True if key exists so delete successful. False otherwise
        """
        return self.db.delTopVal(db=self.sdb, key=self._tokey(keys))

    def cntAll(self):
        """
        Return iterator over the all the items in subdb

        Returns:
            iterator: of tuples of keys tuple and val dataclass instance for
            each entry in db. Raises StopIteration when done

        Example:
            if key in database is "a.b" and val is serialization of dataclass
               with attributes x and y then returns
               (("a","b"), dataclass(x=1,y=2))
        """
        return self.db.cnt(db=self.sdb)
