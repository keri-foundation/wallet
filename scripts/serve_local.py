#!/usr/bin/env python3
"""Serve FortWeb for local browser testing.

Why this exists
---------------
FortWeb's HTML lives under ``app/index.html``. ``pyscript-ci.toml`` selects the
Pyodide interpreter and the ``fort_runtime_packages`` manifest. Those files
resolve runtime artifacts from the FortWeb application base.

If you run ``python -m http.server`` *inside* ``libs/fortweb/app``, those paths
404 (the server returns HTML error pages). Browsers then report::

    Loading module ... core.js was blocked because of a disallowed MIME type ("text/html")

because the "script" is actually an HTML 404 page.

If you run a bare server at ``libs/fortweb``, paths still do not match ``/fortweb/...``.

**Fix:** serve the parent of ``fortweb`` (usually ``libs/`` in the workspace)
and open **exactly**::

    http://127.0.0.1:<port>/fortweb/app/

Usage::

    ./scripts/serve_local.py
    ./scripts/serve_local.py --port 8765

Then open the printed URL (or rely on ``/`` redirect).
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import os
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


OOBI_AID = "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW"
OOBI_BODY = base64.b64decode(
    "eyJ2IjoiS0VSSUNBQUNBQUpTT05BQUV0LiIsInQiOiJpY3AiLCJkIjoiRUdxdDJvWDZTUEFOVTdD"
    "WENObzZYVGFSLVJEa213MDdlbXlaLUZramMwdFciLCJpIjoiRUdxdDJvWDZTUEFOVTdDWENObzZY"
    "VGFSLVJEa213MDdlbXlaLUZramMwdFciLCJzIjoiMCIsImt0IjoiMSIsImsiOlsiREMtUUpDU3BS"
    "TmF3alg3UXNnSGE2RWQ3U2FOajVaMEdJbHpEZkRSY0NQR1ciXSwibnQiOiIxIiwibiI6WyJFS2Qz"
    "M29jTU1CTWxqcWd4RF95cTI0OHk0Sk9JTy1uRDM3YTVyT1BpeVhhVCJdLCJidCI6IjAiLCJiIjpb"
    "XSwiYyI6W10sImEiOltdfS1DQVgtS0FXQUFDWmktLVhxRDFHOUo5bG5SRm9lSk9BUmR2dWtpMDJB"
    "aVFOMkNJdldsWFgtelE1Mko2V25oOWhpNndFTjNSWmE0aGlMby03elpMcjVmRVY5MENVRWM0Rw=="
)
PROXY_PREFIX = "/_fortweb_proxy/"
ALLOWED_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
def _contained_path(root: Path, relative: str) -> Path | None:
    root = root.resolve()
    candidate = (root / relative.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class FortWebRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static files with sane MIME types for JS modules and Pyodide wheels."""

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".cjs": "application/javascript",
        ".whl": "application/octet-stream",
        ".wasm": "application/wasm",
        ".json": "application/json",
    }

    def __init__(self, *args, fortweb_root: Path, runtime_dir: Path | None = None, **kwargs):
        self.fortweb_root = fortweb_root.resolve()
        self.runtime_dir = runtime_dir.resolve() if runtime_dir is not None else None
        super().__init__(*args, directory=str(self.fortweb_root.parent), **kwargs)

    def _translate_under(self, root: Path, relative: str) -> Path | None:
        return _contained_path(root, relative)

    def _invalid_path(self) -> str:
        return str(self.fortweb_root / ".fortweb-invalid-path")

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)

        # Artifact mode has one static root and never falls back to source files.
        runtime = getattr(self, "runtime_dir", None)
        if runtime is not None:
            if not request_path.startswith("/fortweb/"):
                return self._invalid_path()
            relative = request_path[len("/fortweb/"):]
            if relative == "app/":
                relative = "app/index.html"
            parts = relative.split("/")
            if any(not part or part in {".", ".."} or part.startswith(".") for part in parts):
                return self._invalid_path()
            if parts[0] not in {"app", "vendor", "wheels", "contracts", "pyscript-ci.toml", "runtime-closure.json"}:
                return self._invalid_path()
            candidate = runtime
            for part in parts:
                candidate /= part
                if candidate.is_symlink():
                    return self._invalid_path()
            candidate = self._translate_under(runtime, relative)
            if candidate is None or not candidate.is_file():
                return self._invalid_path()
            return str(candidate)

        # Handle vendor requests
        if request_path.startswith('/fortweb/vendor/'):
            vendor_relative = request_path[len('/fortweb/vendor/'):]
            dist_vendor_path = self._translate_under(
                self.fortweb_root / 'dist' / 'runtime' / 'vendor',
                vendor_relative,
            )
            source_vendor_path = self._translate_under(
                self.fortweb_root / 'vendor',
                vendor_relative,
            )
            if dist_vendor_path is None or source_vendor_path is None:
                return self._invalid_path()
            if dist_vendor_path.exists():
                return str(dist_vendor_path)
            return str(source_vendor_path)

        # Handle app requests
        if request_path.startswith('/fortweb/app/'):
            relative_path = request_path[len('/fortweb/app/'):]

            # Serve index.html from source
            if not relative_path or relative_path == 'index.html':
                return str(self.fortweb_root / 'app' / (relative_path or 'index.html'))

            # Check if the requested file exists in the compiled runtime output
            roots = (
                self.fortweb_root / 'dist' / 'runtime' / 'app',
                self.fortweb_root / 'dist' / 'runtime',
                self.fortweb_root / 'app',
                self.fortweb_root,
            )
            candidates = [self._translate_under(root, relative_path) for root in roots]
            if any(candidate is None for candidate in candidates):
                return self._invalid_path()

            for candidate in candidates[:-1]:
                if candidate.exists():
                    return str(candidate)

            return str(candidates[-1])

        if request_path == '/fortweb/runtime-closure.json':
            return str(self.fortweb_root / 'dist' / 'runtime' / 'runtime-closure.json')

        if request_path.startswith('/fortweb/wheels/'):
            candidate = self._translate_under(
                self.fortweb_root / 'dist' / 'runtime' / 'wheels',
                request_path[len('/fortweb/wheels/'):],
            )
            return str(candidate) if candidate is not None else self._invalid_path()

        if request_path.startswith('/fortweb/'):
            candidate = self._translate_under(
                self.fortweb_root,
                request_path[len('/fortweb/'):],
            )
            return str(candidate) if candidate is not None else self._invalid_path()

        return super().translate_path(request_path)

    def _redirect_root_to_app(self) -> bool:
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/fortweb/app/")
            self.end_headers()
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self._redirect_root_to_app():
            return
        if urlsplit(self.path).path.startswith(PROXY_PREFIX):
            self._proxy_request()
            return
        if getattr(self, "runtime_dir", None) is None and self.path.split("?", 1)[0] in {"/oobi", f"/oobi/{OOBI_AID}/controller"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/cesr")
            self.send_header("KERI-AID", OOBI_AID)
            self.send_header("Content-Length", str(len(OOBI_BODY)))
            self.end_headers()
            self.wfile.write(OOBI_BODY)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_or_405()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_or_405()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_or_405()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_or_405()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._redirect_root_to_app():
            return
        super().do_HEAD()

    def end_headers(self) -> None:
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, max-age=0")
        if urlsplit(self.path).path in {"/fortweb/app/", "/fortweb/app/index.html"}:
            self.send_header("Clear-Site-Data", '"cache"')
        super().end_headers()

    def _proxy_or_405(self) -> None:
        if urlsplit(self.path).path.startswith(PROXY_PREFIX):
            self._proxy_request()
            return
        self.send_error(405, f"{self.command} is only supported for the local proxy path.")

    def _proxy_request(self) -> None:
        try:
            target_url = self._proxy_target_url()
        except ValueError as exc:
            self._send_proxy_error(400, f"{exc}\n")
            return

        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length > 0 else b""
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "origin", "referer", "connection", "content-length"}
        }
        request = Request(
            target_url,
            data=body if self.command in {"POST", "PUT", "PATCH", "DELETE"} else None,
            headers=headers,
            method=self.command,
        )

        try:
            with urlopen(request, timeout=30) as response:
                self._send_proxy_response(response.status, response.headers, response.read())
        except HTTPError as exc:
            self._send_proxy_response(exc.code, exc.headers, exc.read())
        except URLError as exc:
            self._send_proxy_error(502, f"Local proxy request failed for {target_url}: {exc.reason}\n")

    def _send_proxy_response(self, status: int, headers, payload: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS | {"content-length"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _send_proxy_error(self, status: int, message: str) -> None:
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _proxy_target_url(self) -> str:
        parts = urlsplit(self.path)
        if not parts.path.startswith(PROXY_PREFIX):
            raise ValueError("Proxy path was malformed.")

        remainder = parts.path[len(PROXY_PREFIX):]
        try:
            scheme, rest = remainder.split("/", 1)
            netloc, tail = rest.split("/", 1)
        except ValueError as exc:
            raise ValueError(
                "Proxy path must be /_fortweb_proxy/<scheme>/<host:port>/<path>."
            ) from exc

        if scheme not in {"http", "https"}:
            raise ValueError("Proxy scheme must be http or https.")

        parsed_target = urlsplit(f"{scheme}://{netloc}")
        try:
            port = parsed_target.port
        except ValueError as exc:
            raise ValueError("Proxy target port was invalid.") from exc
        if (
            parsed_target.username
            or parsed_target.password
            or parsed_target.hostname not in ALLOWED_PROXY_HOSTS
            or port is None
        ):
            raise ValueError("Proxy target must be an explicit local host and port.")

        target = f"{scheme}://{netloc}/{tail}"
        if parts.query:
            target = f"{target}?{parts.query}"
        return target

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args)
        )


def _libs_root(fortweb_root: Path) -> Path:
    resolved = fortweb_root.resolve()
    if not (resolved / "app" / "index.html").is_file():
        raise SystemExit(
            f"Expected FortWeb root directory (got {resolved}). "
            "Run this script from the fortweb repo, e.g. ./scripts/serve_local.py"
        )
    return resolved.parent


class FortWebServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8765")),
        help="TCP port (default: 8765)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--fortweb",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Path to fortweb repo root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Serve only this built or extracted runtime; keep the loopback API proxy.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser tab",
    )
    args = parser.parse_args()

    if args.runtime_dir is not None:
        if args.runtime_dir.is_symlink():
            parser.error("--runtime-dir must be a real directory")
        doc_root = args.runtime_dir.resolve()
        args.runtime_dir = doc_root
        for relative in ("app/index.html", "pyscript-ci.toml", "runtime-closure.json"):
            if not (doc_root / relative).is_file():
                parser.error(f"runtime is missing {relative}")
    else:
        doc_root = _libs_root(args.fortweb)
    os.chdir(doc_root)

    url = f"http://{args.host}:{args.port}/fortweb/app/"
    print(f"Serving HTTP from: {doc_root}")
    print(f"Open FortWeb at:   {url}")
    print("(/ redirects to /fortweb/app/)")
    print("Press Ctrl+C to stop.\n")

    if not args.no_open:
        try:
            webbrowser.open(url)
        except OSError:
            pass

    handler = functools.partial(FortWebRequestHandler, fortweb_root=args.fortweb, runtime_dir=args.runtime_dir)
    with FortWebServer((args.host, args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
