"""Outbound policy shared by gateway and deterministic host-side tests."""

import base64
import concurrent.futures
import hashlib
import ipaddress
import json
import socket
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit

import httpx


class NetworkFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def target_for(identity, policy):
    configured = next((target for target in policy["targets"] if target["id"] == identity), None)
    if configured:
        return configured
    if (
        not policy.get("allow_public_https", False)
        or not isinstance(identity, str)
        or len(identity) > 280
    ):
        raise NetworkFailure("NETWORK_DENIED")
    try:
        parsed = urlsplit(identity)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "\\" in identity
            or any(ord(c) <= 32 or ord(c) == 127 for c in identity)
        ):
            raise ValueError
        hostname = hostname.encode("idna").decode("ascii").lower()
        if (
            "." not in hostname
            or len(hostname) > 253
            or hostname.endswith(".")
            or any(
                not p
                or len(p) > 63
                or p.startswith("-")
                or p.endswith("-")
                or not all(c.isascii() and (c.isalnum() or c == "-") for c in p)
                for p in hostname.split(".")
            )
        ):
            raise ValueError
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError
    except (ValueError, UnicodeError):
        raise NetworkFailure("NETWORK_DENIED") from None
    return {
        "id": "https://" + hostname,
        "hostname": hostname,
        "paths": ["/"],
        "methods": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        "capability": "public_https",
        "credential": False,
    }


def normalize_request(value, policy):
    if not isinstance(value, dict) or set(value) - {"target", "method", "path", "headers", "body"}:
        raise NetworkFailure("NETWORK_DENIED")
    target = target_for(value.get("target"), policy)
    method, path = value.get("method", "GET"), value.get("path", "/")
    if (
        not target
        or method not in target["methods"]
        or not isinstance(path, str)
        or len(path) > 4096
    ):
        raise NetworkFailure("NETWORK_DENIED")
    decoded = path
    for _ in range(3):
        decoded = unquote(decoded)
    parsed = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.netloc
        or parsed.scheme
        or parsed.fragment
        or "\\" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or ".." in decoded.split("/")
        or not any(
            parsed.path == p.rstrip("/") or parsed.path.startswith(p.rstrip("/") + "/") or p == "/"
            for p in target["paths"]
        )
    ):
        raise NetworkFailure("NETWORK_DENIED")
    headers = value.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 8:
        raise NetworkFailure("NETWORK_DENIED")
    for name, content in headers.items():
        if (
            not isinstance(name, str)
            or name.lower() not in {"accept", "content-type", "user-agent"}
            or not isinstance(content, str)
            or len(content) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in content)
        ):
            raise NetworkFailure("NETWORK_DENIED")
    try:
        body = base64.b64decode(value.get("body", ""), validate=True)
    except (ValueError, TypeError):
        raise NetworkFailure("NETWORK_DENIED") from None
    if len(body) > policy["request_bytes"] or (method in {"GET", "HEAD"} and body):
        raise NetworkFailure("NETWORK_DENIED")
    return {
        "target": target["id"],
        "method": method,
        "path": path,
        "headers": headers,
        "body": base64.b64encode(body).decode(),
    }


def public_addresses(hostname, protected=(), resolver=socket.getaddrinfo):
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise NetworkFailure("NETWORK_DENIED")
    if (
        hostname.endswith((".localhost", ".local", ".internal"))
        or hostname == "localhost"
        or any(
            not part or not all(c.isalnum() or c == "-" for c in part)
            for part in hostname.split(".")
        )
    ):
        raise NetworkFailure("NETWORK_DENIED")
    try:
        values = {item[4][0] for item in resolver(hostname, 443, type=socket.SOCK_STREAM)}
        denied = [ipaddress.ip_network(cidr) for cidr in protected]
        if not values:
            raise ValueError
        for value in values:
            address = ipaddress.ip_address(value)
            if (
                not address.is_global
                or getattr(address, "ipv4_mapped", None)
                or any(address in block for block in denied)
            ):
                raise ValueError
        return sorted(values)
    except (ValueError, OSError):
        raise NetworkFailure("NETWORK_DENIED") from None


def doh_resolve(hostname, port, **_kwargs):
    """Administrator-selected resolver only; scripts cannot change its bootstrap or endpoint."""
    result = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        for kind, family, number in (("A", socket.AF_INET, 1), ("AAAA", socket.AF_INET6, 28)):
            with client.stream(
                "GET",
                "https://1.1.1.1/dns-query",
                params={"name": hostname, "type": kind},
                headers={"Host": "cloudflare-dns.com", "Accept": "application/dns-json"},
                extensions={"sni_hostname": "cloudflare-dns.com"},
            ) as response:
                if response.status_code != 200:
                    raise NetworkFailure("NETWORK_DENIED")
                data = bytearray()
                for chunk in response.iter_bytes(4096):
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise NetworkFailure("NETWORK_DENIED")
                body = json.loads(data)
                if body.get("Status") != 0:
                    raise NetworkFailure("NETWORK_DENIED")
                for answer in body.get("Answer", []):
                    if answer.get("type") == number:
                        address = str(ipaddress.ip_address(answer["data"]))
                        result.append((family, socket.SOCK_STREAM, 6, "", (address, port)))
    return result


class Outbound:
    def __init__(self, policy, credentials=None, *, resolver=None, transport=None):
        self.policy, self.credentials = policy, credentials or {}
        self.resolver = resolver or (
            doh_resolve if policy.get("dns_mode") == "cloudflare-doh" else socket.getaddrinfo
        )
        self.transport = transport
        self.started = time.monotonic()
        self.timestamps = []
        self.count = 0
        self.lock = threading.Lock()
        self.usage_read = None
        self.usage_write = None
        self.dns = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def close(self):
        self.dns.shutdown(wait=False, cancel_futures=True)

    def rate(self):
        with self.lock:
            if self.usage_read:
                previous = self.usage_read()
                self.count, self.timestamps = previous["count"], previous["timestamps"]
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            if (
                self.count >= self.policy["requests_per_run"]
                or len(self.timestamps) >= self.policy["requests_per_minute"]
            ):
                raise NetworkFailure("RESOURCE_LIMIT")
            self.timestamps.append(now)
            self.count += 1
            if self.usage_write:
                self.usage_write({"count": self.count, "timestamps": self.timestamps})

    def fetch(self, request, *, approved=False, limit=None, destination=None):
        if self.policy["mode"] != "proxy":
            raise NetworkFailure("NETWORK_DENIED")
        request = normalize_request(request, self.policy)
        target = target_for(request["target"], self.policy)
        sensitive = request["method"] not in {"GET", "HEAD"}
        if sensitive and self.policy.get("require_write_approval", True) and not approved:
            raise NetworkFailure("APPROVAL_REQUIRED")
        deadline = time.monotonic() + self.policy["request_seconds"]
        limit = limit or self.policy["response_bytes"]
        url = "https://" + target["hostname"] + request["path"]
        headers = dict(request["headers"])
        secret = self.credentials.get(target["id"]) if target.get("credential") else None
        if target.get("credential") and not secret:
            raise NetworkFailure("NETWORK_DENIED")
        bound_secret = secret  # Keep response redaction even after cross-origin auth is dropped.
        origin = target["hostname"]
        for hop in range(self.policy["redirects"] + 1):
            self.rate()
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError:
                raise NetworkFailure("NETWORK_DENIED") from None
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or port not in (None, 443)
                or parsed.fragment
            ):
                raise NetworkFailure("NETWORK_DENIED")
            current = (
                target
                if parsed.hostname == origin
                else next(
                    (
                        t
                        for t in self.policy["targets"]
                        if t["hostname"] == parsed.hostname
                        and request["method"] in t["methods"]
                        and t["capability"] == target["capability"]
                    ),
                    None,
                )
            )
            if not current:
                current = target_for("https://" + str(parsed.hostname), self.policy)
            normalize_request(
                {
                    **request,
                    "target": current["id"],
                    "path": parsed.path + ("?" + parsed.query if parsed.query else ""),
                },
                self.policy,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkFailure("TIMEOUT")
            try:
                addresses = self.dns.submit(
                    public_addresses, parsed.hostname, self.policy["protected_cidrs"], self.resolver
                ).result(timeout=remaining)
                ip = addresses[0]
                pinned = httpx.URL(url).copy_with(host=ip)
                outbound_headers = {**headers, "Host": parsed.hostname}
                if secret and parsed.hostname == origin:
                    outbound_headers[target["auth_header"]] = target["auth_prefix"] + secret
                with httpx.Client(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=max(0.01, deadline - time.monotonic()),
                    transport=self.transport,
                ) as client:
                    with client.stream(
                        request["method"],
                        pinned,
                        headers=outbound_headers,
                        content=base64.b64decode(request["body"]),
                        extensions={"sni_hostname": parsed.hostname},
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if sensitive or hop == self.policy["redirects"]:
                                raise NetworkFailure("NETWORK_DENIED")
                            location = response.headers.get("location", "")
                            decoded_location = location
                            for _ in range(3):
                                decoded_location = unquote(decoded_location)
                            if bound_secret and bound_secret in decoded_location:
                                raise NetworkFailure("POLICY_DENIED")
                            url = urljoin(url, location)
                            if urlsplit(url).hostname != parsed.hostname:
                                secret = None
                                headers = {}  # never forward auth or caller headers across origins
                            continue
                        size = 0
                        chunks = []
                        fingerprint = hashlib.sha256()
                        for chunk in response.iter_bytes(16384):
                            size += len(chunk)
                            if time.monotonic() > deadline:
                                raise NetworkFailure("TIMEOUT")
                            if size > limit:
                                raise NetworkFailure("RESOURCE_LIMIT")
                            if bound_secret and bound_secret.encode() in chunk:
                                raise NetworkFailure("POLICY_DENIED")
                            fingerprint.update(chunk)
                            if destination is not None:
                                destination.write(chunk)
                            else:
                                chunks.append(chunk)
                        data = b"".join(chunks)
                        if bound_secret and bound_secret.encode() in data:
                            raise NetworkFailure("POLICY_DENIED")
                        return {
                            "status": response.status_code,
                            "headers": {
                                k: v
                                for k, v in response.headers.items()
                                if k.lower() in {"content-type", "content-length"}
                                and (not bound_secret or bound_secret not in v)
                            },
                            "body": base64.b64encode(data).decode(),
                            "size": size,
                            "sha256": fingerprint.hexdigest(),
                        }
            except (httpx.HTTPError, OSError, concurrent.futures.TimeoutError):
                raise NetworkFailure(
                    "TIMEOUT" if time.monotonic() >= deadline else "NETWORK_DENIED"
                ) from None
        raise NetworkFailure("NETWORK_DENIED")
