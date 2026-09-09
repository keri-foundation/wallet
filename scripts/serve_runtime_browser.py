#!/usr/bin/env python3
"""Serve preloaded, closed-allowlist assets for runtime browser tests."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import signal
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


REPO = Path(__file__).absolute().parent.parent
RUNTIME_ROOT = Path(os.environ.get("FORTWEB_RUNTIME_DIR", REPO / "dist" / "runtime")).resolve()
WHEELHOUSE_PUBLIC_ROOT = "/fortweb/_wheelhouse-test/build/"
OOBI_AID = "EGqt2oX6SPANU7CXCNo6XTaR-RDkmw07emyZ-Fkjc0tW"
OOBI_PATH = f"/oobi/{OOBI_AID}/controller"
OOBI_BODY = base64.b64decode(
    "eyJ2IjoiS0VSSUNBQUNBQUpTT05BQUV0LiIsInQiOiJpY3AiLCJkIjoiRUdxdDJvWDZTUEFOVTdD"
    "WENObzZYVGFSLVJEa213MDdlbXlaLUZramMwdFciLCJpIjoiRUdxdDJvWDZTUEFOVTdDWENObzZY"
    "VGFSLVJEa213MDdlbXlaLUZramMwdFciLCJzIjoiMCIsImt0IjoiMSIsImsiOlsiREMtUUpDU3BS"
    "TmF3alg3UXNnSGE2RWQ3U2FOajVaMEdJbHpEZkRSY0NQR1ciXSwibnQiOiIxIiwibiI6WyJFS2Qz"
    "M29jTU1CTWxqcWd4RF95cTI0OHk0Sk9JTy1uRDM3YTVyT1BpeVhhVCJdLCJidCI6IjAiLCJiIjpb"
    "XSwiYyI6W10sImEiOltdfS1DQVgtS0FXQUFDWmktLVhxRDFHOUo5bG5SRm9lSk9BUmR2dWtpMDJB"
    "aVFOMkNJdldsWFgtelE1Mko2V25oOWhpNndFTjNSWmE0aGlMby03elpMcjVmRVY5MENVRWM0Rw=="
)
SAFE_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
DELAYED_OOBI_PREFIX = "webbaser-timeout-stale-"
TIMEOUT_OOBI_DELAY_SECONDS = 7.0
PRELOAD_FAILURE_CONFIG = b'''interpreter = "./vendor/pyodide/314.0.5/pyodide.mjs"

[fort_runtime_packages]
manifest = "./runtime-closure.json"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

[files]
"./app/runtime/runtime_packages.py" = "./runtime_packages.py"
"./app/runtime/vaulting.py" = "./vaulting.py"
"./app/runtime/transporting.py" = "./transporting.py"
"./app/runtime/onboarding.py" = "./onboarding.py"
'''


@dataclass(frozen=True)
class Asset:
    body: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...] = ()


def _content_type(path: Path) -> str:
    overrides = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".png": "image/png",
        ".py": "text/x-python; charset=utf-8",
        ".svg": "image/svg+xml",
        ".toml": "application/toml; charset=utf-8",
        ".ttf": "font/ttf",
        ".wasm": "application/wasm",
        ".whl": "application/octet-stream",
        ".zip": "application/zip",
    }
    return overrides.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_directory(path: Path) -> int:
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f"Proof root is not a real directory: {path}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_owned_file(path: Path, root: Path) -> Asset:
    absolute_root = root.absolute()
    absolute_path = path.absolute()
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise RuntimeError(f"Proof input escapes its root: {path}") from exc
    if relative == Path(".") or not relative.parts:
        raise RuntimeError(f"Proof input must be a file below its root: {path}")

    directory = _open_directory(absolute_root)
    file_descriptor = -1
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeError(f"Proof input is not a single-link regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        body = b"".join(chunks)
        after = os.fstat(file_descriptor)
        path_after = os.stat(relative.parts[-1], dir_fd=directory, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(path_after):
            raise RuntimeError(f"Proof input changed while it was read: {path}")
        if len(body) != after.st_size:
            raise RuntimeError(f"Proof input size changed while it was read: {path}")
        return Asset(body=body, content_type=_content_type(path))
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        os.close(directory)


def _secure_file_paths(root: Path) -> set[str]:
    root_descriptor = _open_directory(root)
    result: set[str] = set()

    def walk(directory: int, prefix: tuple[str, ...]) -> None:
        for name in sorted(os.listdir(directory)):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            relative = (*prefix, name)
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError(f"Proof root contains a symlink: {'/'.join(relative)}")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory,
                )
                try:
                    if _identity(info) != _identity(os.fstat(child)):
                        raise RuntimeError(f"Proof directory changed during preload: {'/'.join(relative)}")
                    walk(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                result.add("/".join(relative))
            else:
                raise RuntimeError(f"Proof root contains a non-regular entry: {'/'.join(relative)}")

    try:
        walk(root_descriptor, ())
    finally:
        os.close(root_descriptor)
    return result


def _safe_inventory_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise RuntimeError(f"Unsafe canonical inventory path: {value}")
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"Unsafe canonical inventory path: {value}")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise RuntimeError(f"Unsafe canonical inventory path: {value}")
    return value


def _runtime_routes(inventory_path: Path) -> dict[str, Asset]:
    inventory_asset = _read_owned_file(inventory_path, inventory_path.absolute().parent)
    inventory = json.loads(inventory_asset.body)
    if inventory.get("schema") != 1 or not isinstance(inventory.get("files"), list):
        raise RuntimeError("Canonical inventory schema is invalid")
    rows: dict[str, dict[str, object]] = {}
    for row in inventory["files"]:
        if not isinstance(row, dict):
            raise RuntimeError("Canonical inventory contains a non-object row")
        relative = _safe_inventory_path(row.get("path"))
        if relative in rows:
            raise RuntimeError(f"Canonical inventory repeats {relative}")
        size = row.get("bytes")
        digest = row.get("sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"Canonical inventory row is invalid: {relative}")
        rows[relative] = row
    ordered_paths = sorted(rows, key=lambda value: value.encode())
    if list(rows) != ordered_paths:
        raise RuntimeError("Canonical inventory paths are not byte-sorted")
    aggregate = hashlib.sha256()
    for relative in ordered_paths:
        row = rows[relative]
        size = row["bytes"]
        digest = row["sha256"]
        aggregate.update(f"{relative}\0{size}\0{digest}\n".encode())
    if aggregate.hexdigest() != inventory.get("aggregate_sha256"):
        raise RuntimeError("Canonical inventory aggregate is invalid")
    expected_paths = set(rows)
    if _secure_file_paths(RUNTIME_ROOT) != expected_paths:
        raise RuntimeError("Canonical runtime path set differs from its inventory")

    routes: dict[str, Asset] = {}
    for relative, row in rows.items():
        asset = _read_owned_file(RUNTIME_ROOT / relative, RUNTIME_ROOT)
        digest = hashlib.sha256(asset.body).hexdigest()
        if len(asset.body) != row["bytes"] or digest != row["sha256"]:
            raise RuntimeError(f"Canonical runtime bytes differ from inventory: {relative}")
        routes[f"/fortweb/{relative}"] = asset
    if _secure_file_paths(RUNTIME_ROOT) != expected_paths:
        raise RuntimeError("Canonical runtime changed during preload")
    if "/fortweb/app/index.html" not in routes or "/fortweb/runtime-closure.json" not in routes:
        raise RuntimeError("Canonical runtime is incomplete")
    return routes


def _application_routes(inventory_path: Path) -> dict[str, Asset]:
    routes = _runtime_routes(inventory_path)
    fixture_root = REPO / "ci" / "fixtures" / "webbaser-lifecycle"
    routes.update(
        {
            "/_runtime-test/ci/fixtures/webbaser-lifecycle/index.html": _read_owned_file(
                fixture_root / "index.html", fixture_root
            ),
            "/_runtime-test/ci/fixtures/webbaser-lifecycle/production.html": _read_owned_file(
                fixture_root / "production.html", fixture_root
            ),
            "/_runtime-test/python/run_webbaser_lifecycle.py": _read_owned_file(
                REPO / "python" / "run_webbaser_lifecycle.py", REPO / "python"
            ),
            "/fortweb/pyscript-preload-failure.toml": Asset(
                PRELOAD_FAILURE_CONFIG,
                "application/toml; charset=utf-8",
            ),
        }
    )
    return routes


def _wheelhouse_routes(wheelhouse_root: Path, manifest_sha256: str) -> dict[str, Asset]:
    manifest_asset = _read_owned_file(wheelhouse_root / "manifest.json", wheelhouse_root)
    digest = hashlib.sha256(manifest_asset.body).hexdigest()
    if digest != manifest_sha256:
        raise RuntimeError(f"Reviewed wheelhouse manifest changed: {digest}")
    manifest = json.loads(manifest_asset.body)
    core_files = manifest["runtime"]["core_files"]
    wheel_files = [row["filename"] for row in manifest["wheels"]]
    file_rows = {row["path"]: row for row in manifest["files"]}

    def accepted_asset(relative: str) -> Asset:
        row = file_rows.get(relative)
        if not isinstance(row, dict):
            raise RuntimeError(f"Reviewed wheelhouse manifest does not contain {relative}")
        asset = _read_owned_file(wheelhouse_root / relative, wheelhouse_root)
        digest = hashlib.sha256(asset.body).hexdigest()
        if row.get("bytes") != len(asset.body) or row.get("sha256") != digest:
            raise RuntimeError(f"Reviewed wheelhouse artifact changed: {relative}")
        return asset

    routes = {
        "/fortweb/_wheelhouse-test/fixture.html": _read_owned_file(
            REPO / "ci" / "fixtures" / "pyodide-314-wheelhouse.html",
            REPO / "ci" / "fixtures",
        ),
        "/fortweb/_wheelhouse-test/worker.mjs": _read_owned_file(
            REPO / "ci" / "fixtures" / "pyodide-314-wheelhouse-worker.mjs",
            REPO / "ci" / "fixtures",
        ),
        f"{WHEELHOUSE_PUBLIC_ROOT}manifest.json": manifest_asset,
    }
    for filename in core_files:
        relative = f"runtime/{filename}"
        routes[f"{WHEELHOUSE_PUBLIC_ROOT}{relative}"] = accepted_asset(relative)
    for filename in wheel_files:
        relative = f"wheelhouse/{filename}"
        routes[f"{WHEELHOUSE_PUBLIC_ROOT}{relative}"] = accepted_asset(relative)
    if len(routes) != 3 + 5 + 34:
        raise RuntimeError(f"Unexpected wheelhouse route count: {len(routes)}")
    return routes


def _load_routes(
    mode: str,
    inventory_path: Path | None,
    wheelhouse_root: Path | None,
    manifest_sha256: str = "",
) -> dict[str, Asset]:
    if mode == "isolated":
        if inventory_path is None:
            raise RuntimeError("Isolated mode requires the canonical inventory")
        return _runtime_routes(inventory_path)
    if mode == "application":
        if inventory_path is None:
            raise RuntimeError("Application mode requires the canonical inventory")
        return _application_routes(inventory_path)
    if mode == "wheelhouse":
        if inventory_path is not None:
            raise RuntimeError("Wheelhouse mode does not accept a runtime inventory")
        if wheelhouse_root is None:
            raise RuntimeError("Wheelhouse mode requires --wheelhouse-root")
        return _wheelhouse_routes(wheelhouse_root, manifest_sha256)
    raise ValueError(mode)


def _parse_target(raw_target: str, mode: str) -> tuple[str, Asset | None] | None:
    if (
        not raw_target.startswith("/")
        or raw_target.startswith("//")
        or "://" in raw_target
        or "%" in raw_target
        or "\\" in raw_target
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_target)
    ):
        return None
    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    if "//" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/")):
        return None
    if not parsed.query:
        return parsed.path, None
    if mode != "application":
        return None
    if parsed.path.endswith("/production.html") and parsed.query in {
        "invalidRuntimeContract=1",
        "preloadFailure=1",
    }:
        return parsed.path, None
    if parsed.path in {"/oobi", OOBI_PATH}:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if set(query) == {"name"} and len(query["name"]) == 1 and SAFE_ALIAS.fullmatch(query["name"][0]):
            return parsed.path, Asset(
                OOBI_BODY,
                "application/cesr",
                (("KERI-AID", OOBI_AID),),
            )
    return None


def _oobi_delay_seconds(raw_target: str, mode: str) -> float:
    if mode != "application":
        return 0.0
    parsed = urlsplit(raw_target)
    if parsed.path != "/oobi":
        return 0.0
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return 0.0
    aliases = query.get("name", [])
    if set(query) != {"name"} or len(aliases) != 1:
        return 0.0
    alias = aliases[0]
    if SAFE_ALIAS.fullmatch(alias) is None or not alias.startswith(DELAYED_OOBI_PREFIX):
        return 0.0
    return TIMEOUT_OOBI_DELAY_SECONDS


class RuntimeBrowserServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        address,
        handler,
        *,
        mode: str,
        inventory_path: Path | None = None,
        wheelhouse_root: Path | None = None,
        manifest_sha256: str = "",
    ):
        self.mode = mode
        self.routes = _load_routes(mode, inventory_path, wheelhouse_root, manifest_sha256)
        self.request_sequence = 0
        self.record_lock = threading.Lock()
        super().__init__(address, handler)

    def record(self, payload: dict[str, object]) -> int:
        with self.record_lock:
            self.request_sequence += 1
            payload["sequence"] = self.request_sequence
            sys.stderr.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            sys.stderr.flush()
            return self.request_sequence


class RuntimeBrowserHandler(BaseHTTPRequestHandler):
    server: RuntimeBrowserServer
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _respond(
        self,
        status_code: int,
        asset: Asset,
        *,
        delayed_receipt_sequence: int | None = None,
        delay_started_at: float | None = None,
    ) -> None:
        complete = False
        error_text = ""
        unexpected_error: OSError | None = None
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", asset.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(asset.body)))
            for name, value in asset.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(asset.body)
            self.wfile.flush()
            complete = True
        except (BrokenPipeError, ConnectionResetError) as error:
            error_text = f"{type(error).__name__}: {error}"
        except OSError as error:
            error_text = f"{type(error).__name__}: {error}"
            unexpected_error = error
        origin = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
        payload: dict[str, object] = {
            "bytes": len(asset.body),
            "complete": complete,
            "error_text": error_text,
            "event": "response",
            "method": self.command,
            "mode": self.server.mode,
            "sha256": hashlib.sha256(asset.body).hexdigest(),
            "status": status_code,
            "url": origin + self.path,
        }
        if delayed_receipt_sequence is not None and delay_started_at is not None:
            payload["delayed_receipt_sequence"] = delayed_receipt_sequence
            payload["delay_elapsed_ms"] = round((time.monotonic() - delay_started_at) * 1000)
        self.server.record(payload)
        if unexpected_error is not None:
            raise unexpected_error

    def do_GET(self) -> None:  # noqa: N802
        parsed = _parse_target(self.path, self.server.mode)
        if parsed is None:
            self._respond(404, Asset(b"not found\n", "text/plain; charset=utf-8"))
            return
        path, dynamic_asset = parsed
        asset = dynamic_asset or self.server.routes.get(path)
        if asset is None:
            self._respond(404, Asset(b"not found\n", "text/plain; charset=utf-8"))
            return
        delay_seconds = _oobi_delay_seconds(self.path, self.server.mode)
        delayed_receipt_sequence = None
        delay_started_at = None
        if delay_seconds:
            origin = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
            delay_started_at = time.monotonic()
            delayed_receipt_sequence = self.server.record(
                {
                    "delay_seconds": delay_seconds,
                    "event": "delayed_oobi_received",
                    "method": self.command,
                    "mode": self.server.mode,
                    "url": origin + self.path,
                }
            )
            time.sleep(delay_seconds)
        self._respond(
            200,
            asset,
            delayed_receipt_sequence=delayed_receipt_sequence,
            delay_started_at=delay_started_at,
        )

    def _method_not_allowed(self) -> None:
        asset = Asset(b"method not allowed\n", "text/plain; charset=utf-8", (("Allow", "GET"),))
        self._respond(405, asset)

    do_HEAD = _method_not_allowed
    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def log_message(self, _format: str, *_args) -> None:
        return


def _write_ready_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        data = (json.dumps(payload, sort_keys=True) + "\n").encode()
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise OSError("ready-file write made no progress")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("isolated", "application", "wheelhouse"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--wheelhouse-root", type=Path)
    parser.add_argument("--source-manifest-sha256", default=os.environ.get("FORTWEB_RUNTIME_SOURCE_MANIFEST_SHA256", ""))
    args = parser.parse_args()

    with RuntimeBrowserServer(
        ("127.0.0.1", args.port),
        RuntimeBrowserHandler,
        mode=args.mode,
        inventory_path=args.inventory,
        wheelhouse_root=args.wheelhouse_root,
        manifest_sha256=args.source_manifest_sha256,
    ) as server:
        host, port = server.server_address[:2]
        payload = {"host": host, "mode": args.mode, "port": port, "url": f"http://{host}:{port}"}
        _write_ready_file(args.ready_file, payload)
        print(json.dumps(payload, sort_keys=True), flush=True)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def stop_server(_signum, _frame) -> None:
            threading.Thread(
                target=server.shutdown,
                name="runtime-sigterm-shutdown",
                daemon=True,
            ).start()

        signal.signal(signal.SIGTERM, stop_server)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
