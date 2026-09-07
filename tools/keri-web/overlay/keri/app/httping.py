# -*- encoding: utf-8 -*-
"""
keri.peer.httping module

"""
import asyncio
import datetime
import json
from dataclasses import dataclass
from collections import deque
from urllib import parse

from hio.base import doing
from hio.help import Hict, ogler

from ..kering import (ShortageError, ExtractionError,
                      ColdStartError, sniff, Colds)
from ..core import Sadder, SerderKERI
from ..end import designature
from ..help import nowUTC

try:  # pragma: no cover
    import js  # type: ignore
except ImportError:  # pragma: no cover
    js = None

try:  # pragma: no cover
    import falcon
except ImportError:  # pragma: no cover
    falcon = None


logger = ogler.getLogger()

CESR_CONTENT_TYPE = "application/cesr"
CESR_ATTACHMENT_HEADER = "CESR-ATTACHMENT"
CESR_DESTINATION_HEADER = "CESR-DESTINATION"


def _require_falcon():
    if falcon is None:  # pragma: no cover
        raise RuntimeError("falcon is required for HTTP endpoint handling")
    return falcon


class SignatureValidationComponent(object):
    """ Validate SKWA signatures """

    def __init__(self, hby, pre):
        self.hby = hby
        self.pre = pre

    def process_request(self, req, resp):
        """ Process request to ensure has a valid signature from controller

        Parameters:
            req: Http request object
            resp: Http response object

        """
        falconing = _require_falcon()
        sig = req.headers.get("SIGNATURE")
        ked = req.media
        ser = json.dumps(ked).encode("utf-8")
        if not self.validate(sig=sig, ser=ser):
            resp.complete = True
            resp.status = falconing.HTTP_401
            return

    def validate(self, sig, ser):
        signages = designature(sig)
        markers = signages[0].markers

        if self.pre not in self.hby.kevers:
            return False

        verfers = self.hby.kevers[self.pre].verfers
        for idx, verfer in enumerate(verfers):
            key = str(idx)
            if key not in markers:
                return False
            siger = markers[key]
            siger.verfer = verfer

            if not verfer.verify(siger.raw, ser):
                return False

        return True


@dataclass
class CesrRequest:
    payload: dict
    attachments: str


def parseCesrHttpRequest(req):
    """
    Parse Falcon HTTP request and create a CESR message from the body of the request and the two
    CESR HTTP headers (Date, Attachment).

    Parameters
        req (falcon.Request) http request object in CESR format:

    """
    falconing = _require_falcon()
    if req.content_type != CESR_CONTENT_TYPE:
        raise falconing.HTTPError(falconing.HTTP_NOT_ACCEPTABLE,
                                  title="Content type error",
                                  description="Unacceptable content type.")

    try:
        data = json.load(req.bounded_stream)
    except ValueError:
        raise falconing.HTTPError(falconing.HTTP_400,
                                  title="Malformed JSON",
                                  description="Could not decode the request body. The "
                                              "JSON was incorrect.")

    if CESR_ATTACHMENT_HEADER not in req.headers:
        raise falconing.HTTPError(falconing.HTTP_PRECONDITION_FAILED,
                                  title="Attachment error",
                                  description="Missing required attachment header.")
    attachment = req.headers[CESR_ATTACHMENT_HEADER]

    cr = CesrRequest(
        payload=data,
        attachments=attachment)

    return cr


def createCESRRequest(msg, client, dest, path=None):
    """
    Turns a KERI message into a CESR http request against the provided hio http Client

    Parameters
       msg:  KERI message parsable as Serder.raw
       dest (str): qb64 identifier prefix of destination controller
       client: hio http Client that will send the message as a CESR request
       path (str): path to post to

    """
    path = path if path is not None else "/"

    try:
        serder = SerderKERI(raw=msg)
    except ShortageError as ex:  # need more bytes
        raise ExtractionError("unable to extract a valid message to send as HTTP")
    else:  # extracted successfully
        del msg[:serder.size]  # strip off event from front of ims

    attachments = bytearray(msg)
    body = serder.raw

    headers = Hict([
        ("Content-Type", CESR_CONTENT_TYPE),
        ("Content-Length", len(body)),
        ("connection", "close"),
        (CESR_ATTACHMENT_HEADER, attachments),
        (CESR_DESTINATION_HEADER, dest)
    ])

    client.request(
        method="POST",
        path=path,
        headers=headers,
        body=body
    )


def streamCESRRequests(client, ims, dest, path=None, headers=None):
    """
    Turns a stream of KERI messages into CESR http requests against the provided hio http Client

    Parameters
       client (Client): hio http Client that will send the message as a CESR request
       ims (bytearray):  stream of KERI messages parsable as Serder.raw
       dest (str): qb64 identifier prefix of destination controller
       path (str): path to post to

    Returns
       int: Number of individual requests posted

    """
    path = path if path is not None else "/"
    path = parse.urljoin(client.requester.path, path)

    cold = sniff(ims)  # check for spurious counters at front of stream
    if cold in (Colds.txt, Colds.bny):  # not message error out to flush stream
        # replace with pipelining here once CESR message format supported.
        raise ColdStartError("Expecting message counter tritet={}"
                                    "".format(cold))

    # Otherwise its a message cold start
    cnt = 0
    while ims:  # extract and deserialize message from ims
        try:
            serder = Sadder(raw=ims)
        except ShortageError as ex:  # need more bytes
            raise ExtractionError("unable to extract a valid message to send as HTTP")
        else:  # extracted successfully
            del ims[:serder.size]  # strip off event from front of ims

        attachment = bytearray()
        while ims and ims[0] != 0x7b:  # not new message so process attachments, must support CBOR and MsgPack
            attachment.append(ims[0])
            del ims[:1]

        body = serder.raw

        headers = headers if headers is not None else Hict()
        heads = (Hict([
            ("Content-Type", CESR_CONTENT_TYPE),
            ("Content-Length", len(body)),
            (CESR_ATTACHMENT_HEADER, attachment),
            (CESR_DESTINATION_HEADER, dest)
        ]))
        heads.update(headers)

        client.request(
            method="POST",
            path=path,
            headers=heads,
            body=body
        )
        cnt += 1

    return cnt


class Clienter(doing.DoDoer):
    """
    Clienter is a DoDoer that manages hio HTTP clients using a ClientDoer for each HTTP request.
    It executes HTTP requests using a HIO HTTP Client run by a ClientDoer. Once a request has
    received a response then the corresponding Doer is removed from this Clienter.

    Doers:
        - clientDo: Periodically checks for stale clients and removes them if they have not received a response
          within the specified timeout period.
    """

    TimeoutClient = 300  # seconds to wait for response before removing client, default is 5 minutes

    def __init__(self):
        """Initialize clienter with an empty list of client tuples.

        Attributes:
            clients (list[tuple]): Active client tuples, each containing a
                ``ClientDoer`` instance, an hio HTTP ``Client`` instance,
                and a ``datetime`` timestamp.
            doers (list): Doers managed by this Clienter, initialized with clientDo.
        """
        self.clients = []
        doers = [doing.doify(self.clientDo)]
        super(Clienter, self).__init__(doers=doers)

    @staticmethod
    def _useBrowserFetch(url):
        purl = parse.urlparse(url)
        return js is not None and getattr(js, "fetch", None) is not None and purl.scheme in ("http", "https")

    @staticmethod
    def _normalizeHeaderValue(val):
        if isinstance(val, memoryview):
            val = bytes(val)
        if isinstance(val, (bytes, bytearray)):
            return bytes(val).decode("utf-8")
        return str(val)

    @staticmethod
    def _normalizeBody(body):
        if body is None:
            return None
        if isinstance(body, memoryview):
            body = bytes(body)
        if isinstance(body, (bytes, bytearray)):
            return bytes(body).decode("utf-8")
        return body

    async def _browserFetch(self, client, method, url, body=None, headers=None):
        try:
            options = js.Object.new()
            options.method = method

            if headers is not None:
                jheaders = js.Headers.new()
                for key, val in dict(headers).items():
                    jheaders.append(str(key), self._normalizeHeaderValue(val))
                options.headers = jheaders

            if body is not None:
                options.body = self._normalizeBody(body)

            response = await js.fetch(url, options)
            content_type = response.headers.get("Content-Type") or response.headers.get("content-type") or ""
            keri_aid = response.headers.get("KERI-AID") or response.headers.get("keri-aid") or ""
            text = await response.text()
            headerage = {"Content-Type": str(content_type)}
            if keri_aid:
                headerage["KERI-AID"] = str(keri_aid)

            client.responses.append(
                dict(
                    status=int(response.status),
                    headers=headerage,
                    body=text.encode("utf-8"),
                )
            )
        except Exception as ex:  # pragma: no cover
            client.responses.append(
                dict(
                    status=599,
                    headers={"Content-Type": "text/plain"},
                    body=str(ex).encode("utf-8"),
                )
            )

    def _browserRequest(self, method, url, body=None, headers=None):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as ex:
            raise RuntimeError("Browser fetch client requires an active asyncio loop") from ex

        client = type("BrowserClient", (), {})()
        client.responses = deque()
        client.url = url
        client._task = loop.create_task(self._browserFetch(client, method, url, body=body, headers=headers))
        self.clients.append((client, None, nowUTC()))
        return client

    def request(self, method, url, body=None, headers=None):
        """
        Perform an HTTP request using a hio http Client and ClientDoer and returns the Client.

        Parameters:
            method (str): HTTP method to use (e.g., "GET", "POST")
            url (str): URL to send the request to
            body (str or bytes, optional): Body of the request, defaults to None
            headers (dict, optional): Headers to include in the request, defaults to None

        Returns:
            http.clienting.Client: The hio HTTP Client used for the request, or None if an error occurs.
        """
        if self._useBrowserFetch(url):
            return self._browserRequest(method, url, body=body, headers=headers)

        from hio.core import http
        purl = parse.urlparse(url)

        try:
            client = http.clienting.Client(scheme=purl.scheme,
                                           hostname=purl.hostname,
                                           port=purl.port,
                                           portOptional=True)
        except Exception as e:
            print(f"error establishing client connection={e}")
            return None

        if hasattr(body, "encode"):
            body = body.encode("utf-8")

        client.request(
            method=method,
            path=f"{purl.path}?{purl.query}",
            qargs=None,
            headers=headers,
            body=body
        )

        clientDoer = http.clienting.ClientDoer(client=client)
        self.extend([clientDoer])
        self.clients.append((client, clientDoer, nowUTC()))

        return client

    def remove(self, client):
        """
        Find a client tuple by hio HTTP Client and remove it and its Doer from the Clienter.

        Parameters:
            client (http.clienting.Client): The hio HTTP Client to remove from the Clienter.
        """
        doers = [(c, d, dt) for (c, d, dt) in self.clients if c == client]
        if len(doers) == 0:
            return

        tup = doers[0]
        self.clients.remove(doers[0])
        (_, doer, _) = tup
        if doer is not None:
            super(Clienter, self).remove([doer])

        task = getattr(client, "_task", None)
        if task is not None and not task.done():
            task.cancel()

    def clientDo(self, tymth, tock=0.0, **kwa):
        """ Periodically prune stale clients

        Process existing clients and prune any that have receieved a response longer than timeout

        Parameters:
            tymth (function): injected function wrapper closure returned by .tymen() of
                Tymist instance. Calling tymth() returns associated Tymist .tyme.
            tock (float): injected initial tock value

        """
        self.wind(tymth)
        self.tock = tock
        yield self.tock

        while True:
            toRemove = []
            for (client, doer, dt) in self.clients:
                if client.responses:
                    now = nowUTC()
                    if (now - dt) > datetime.timedelta(seconds=self.TimeoutClient):
                        toRemove.append(client)
                elif doer is None and getattr(client, "_task", None) is not None:
                    now = nowUTC()
                    if (now - dt) > datetime.timedelta(seconds=self.TimeoutClient):
                        toRemove.append(client)

                yield self.tock

            for client in toRemove:
                self.remove(client)

            yield self.tock
