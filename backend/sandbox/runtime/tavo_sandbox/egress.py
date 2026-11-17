"""Dedicated UDS application proxy. No CONNECT, filesystem API, Docker API, or user code."""

import fcntl
import json
import os
import socketserver
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from tavo_sandbox.network import NetworkFailure, Outbound, digest, normalize_request

PRIVATE = Path("/run/private")
SOCKET = "/run/tavo-egress/proxy.sock"


def atomic_json(path, value):
    temp = path.with_suffix(".new")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, separators=(",", ":"))
    temp.replace(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # no URLs, credentials, query strings, request/response bodies

    def reply(self, status, value):
        data = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            self.connection.settimeout(self.server.config["policy"]["request_seconds"])
            length = int(self.headers.get("Content-Length", "0"))
            if (
                length < 2
                or length > self.server.config["policy"]["request_bytes"] * 2
                or self.headers.get("Transfer-Encoding")
            ):
                raise NetworkFailure("NETWORK_DENIED")
            raw = self.rfile.read(length)
            value = json.loads(raw)
            with (PRIVATE / "request.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                if (PRIVATE / "sealed").exists():
                    raise NetworkFailure("NETWORK_DENIED")
                self.dispatch(value)
        except NetworkFailure as error:
            with self.server.pending_lock:
                path = PRIVATE / "errors.json"
                errors = json.loads(path.read_bytes()) if path.is_file() else []
                atomic_json(path, (errors + [error.code])[-16:])
            self.reply(403 if error.code != "APPROVAL_REQUIRED" else 409, {"errorCode": error.code})
        except (ValueError, KeyError, TypeError, OSError):
            self.reply(400, {"errorCode": "SANDBOX_ERROR"})

    def dispatch(self, value):
        outbound, config = self.server.outbound, self.server.config
        if time.time() > config["expires"]:
            raise NetworkFailure("NETWORK_DENIED")
        if self.path == "/packages":
            self.reply(
                200,
                [
                    {
                        **{k: p[k] for k in ("name", "version", "sha256")},
                        "filename": urlsplit(p["url"]).path.rsplit("/", 1)[-1],
                    }
                    for p in config["policy"]["packages"]
                ],
            )
        elif self.path == "/result":
            key = value.get("requestId", "")
            if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
                raise NetworkFailure("NETWORK_DENIED")
            result = PRIVATE / "results" / (key + ".json")
            self.reply(
                200,
                json.loads(result.read_bytes())
                if result.is_file()
                else {"errorCode": "APPROVAL_REQUIRED", "requestId": key},
            )
        elif self.path == "/wheel":
            package = next(
                (
                    p
                    for p in config["policy"]["packages"]
                    if p["name"] == value.get("name") and p["version"] == value.get("version")
                ),
                None,
            )
            if not package or not package["url"].endswith(".whl"):
                raise NetworkFailure("NETWORK_DENIED")
            parsed = urlsplit(package["url"])
            target = next(
                (
                    t
                    for t in config["policy"]["targets"]
                    if t["hostname"] == parsed.hostname and t["capability"] == "package_download"
                ),
                None,
            )
            if not target:
                raise NetworkFailure("NETWORK_DENIED")
            with tempfile.TemporaryFile() as staging:
                result = outbound.fetch(
                    {"target": target["id"], "method": "GET", "path": parsed.path},
                    destination=staging,
                    limit=config["policy"]["download_bytes"],
                )
                if result["status"] != 200 or result["sha256"] != package["sha256"]:
                    raise NetworkFailure("POLICY_DENIED")
                staging.seek(0)
                self.send_response(200)
                self.send_header("Content-Length", str(result["size"]))
                self.end_headers()
                while chunk := staging.read(16384):
                    self.wfile.write(chunk)
        elif self.path == "/request":
            request = normalize_request(value, config["policy"])
            try:
                self.reply(200, outbound.fetch(request))
            except NetworkFailure as error:
                if error.code != "APPROVAL_REQUIRED":
                    raise
                with self.server.pending_lock:
                    pending = PRIVATE / "pending"
                    pending.mkdir(exist_ok=True, mode=0o700)
                    if len(list(pending.glob("*.json"))) >= 8:
                        raise NetworkFailure("RESOURCE_LIMIT") from None
                    key = uuid4().hex
                    atomic_json(
                        pending / (key + ".json"),
                        {"id": key, "request": request, "digest": digest(request)},
                    )
                self.reply(409, {"errorCode": "APPROVAL_REQUIRED", "requestId": key})
        else:
            raise NetworkFailure("NETWORK_DENIED")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, config):
        self.config = config
        self.pending_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)
        self.outbound = Outbound(config["policy"], config.get("credentials"))
        stats = PRIVATE / "stats.json"
        self.outbound.usage_read = lambda: (
            json.loads(stats.read_bytes())
            if stats.is_file()
            else config.get("usage", {"count": 0, "timestamps": []})
        )
        self.outbound.usage_write = lambda value: atomic_json(stats, value)
        super().__init__(SOCKET, Handler)
        os.chmod(SOCKET, 0o600)

    def verify_request(self, *_args):
        return self.slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    config = PRIVATE / "config.json"
    deadline = time.monotonic() + 30
    while not config.is_file():
        if time.monotonic() > deadline:
            raise SystemExit(1)
        time.sleep(0.05)
    with Server(json.loads(config.read_bytes())) as server:
        server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
