#!/usr/bin/env python3
"""Serve FortWeb for local browser testing.

Why this exists
---------------
FortWeb's HTML lives under ``app/index.html``, but ``pyscript-ci.toml`` and the
wallet worker load Pyodide and wheels using absolute URL paths such as
``/fortweb/vendor/pyodide/...`` and ``/fortweb/wheels/...``.

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
import http.server
import os
import socketserver
import sys
import webbrowser
from pathlib import Path


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

    # Deterministic local OOBI endpoints (test-owned). Serves the same V2 CESR
    # controller streams the DigitalOcean witness/watcher serve, from local
    # fixture files, so the real BrowserClienter OOBI path can be exercised
    # with zero external network during the deterministic regressions.
    OOBI_FIXTURES = {
        "BIWLbdRiC1X2ylzDl-blkqkXKz7LI-1ErzbjokYVlk9Z": "synth-witness-oobi.cesr",  # witness :5633
        "BBYFnq8-_i2hjGemEsUMdE6M4RAB9Bi3iY7dvfEGKV2O": "synth-watcher-oobi.cesr",  # watcher :7633
    }

    def _serve_oobi_fixture(self) -> bool:
        import re

        match = re.fullmatch(r"/oobi/([^/]+)/([^/]+)", self.path.split("?", 1)[0])
        if not match:
            return False
        cid, role = match.group(1), match.group(2)
        fixture_name = self.OOBI_FIXTURES.get(cid)
        if fixture_name is None:
            self.send_error(404, f"unknown deterministic OOBI fixture for {cid}")
            return True
        fixture_path = os.path.join(self.directory, "fortweb", "app", "fixtures", fixture_name)
        try:
            with open(fixture_path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_error(404, f"fixture file missing: {fixture_path}")
            return True
        self.send_response(200)
        self.send_header("Content-Type", "application/cesr")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def translate_path(self, path: str) -> str:
        # Handle vendor requests
        if path.startswith('/fortweb/vendor/'):
            vendor_relative = path[len('/fortweb/vendor/'):]
            dist_vendor_path = os.path.join(self.directory, 'fortweb', 'dist', 'runtime', 'vendor', vendor_relative)
            if os.path.exists(dist_vendor_path):
                return dist_vendor_path
            return os.path.join(self.directory, 'fortweb', 'vendor', vendor_relative)

        # Handle app requests
        if path.startswith('/fortweb/app/'):
            relative_path = path[len('/fortweb/app/'):]

            # Serve index.html from source
            if not relative_path or relative_path == 'index.html':
                return os.path.join(self.directory, 'fortweb', 'app', relative_path or 'index.html')

            # Check if the requested file exists in the compiled runtime output
            dist_app_path = os.path.join(self.directory, 'fortweb', 'dist', 'runtime', 'app', relative_path)
            if os.path.exists(dist_app_path):
                return dist_app_path

            dist_root_path = os.path.join(self.directory, 'fortweb', 'dist', 'runtime', relative_path)
            if os.path.exists(dist_root_path):
                return dist_root_path

            # Fallback to source app directory, then source root
            source_app_path = os.path.join(self.directory, 'fortweb', 'app', relative_path)
            if os.path.exists(source_app_path):
                return source_app_path

            return os.path.join(self.directory, 'fortweb', relative_path)

        return super().translate_path(path)

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
        if self._serve_oobi_fixture():
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._redirect_root_to_app():
            return
        if self._serve_oobi_fixture():
            return
        super().do_HEAD()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args)
        )


def _libs_root(fortweb_root: Path) -> Path:
    resolved = fortweb_root.resolve()
    name = resolved.name
    if name != "fortweb":
        raise SystemExit(
            f"Expected fortweb root directory (got {resolved}). "
            "Run this script from the fortweb repo, e.g. ./scripts/serve_local.py"
        )
    return resolved.parent


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
        "--no-open",
        action="store_true",
        help="Do not open a browser tab",
    )
    args = parser.parse_args()

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

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), FortWebRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
