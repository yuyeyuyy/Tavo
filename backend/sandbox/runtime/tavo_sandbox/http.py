"""Task scripts call public HTTPS URLs or bound aliases without receiving saved tokens."""

import base64
import json as json_module
import os
from urllib.parse import urlsplit

import httpx


class EgressError(RuntimeError):
    def __init__(self, code, request_id=None):
        self.code, self.request_id = code, request_id
        super().__init__(
            code
            + (
                f" requestId={request_id}; request approval with http_request, then read tavo_http.result(requestId). Do not rerun the whole script."
                if request_id
                else ""
            )
        )


def call(endpoint, payload):
    path = os.environ.get("TAVO_EGRESS_SOCKET")
    if not path:
        raise EgressError("NETWORK_DENIED")
    try:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=path), trust_env=False, timeout=120
        ) as client:
            response = client.post("http://egress" + endpoint, json=payload)
            value = response.json()
    except (httpx.HTTPError, ValueError):
        raise EgressError("NETWORK_DENIED") from None
    if isinstance(value, dict) and value.get("errorCode"):
        raise EgressError(value["errorCode"], value.get("requestId"))
    return value


class Response:
    def __init__(self, value):
        self.status_code, self.headers = value["status"], value["headers"]
        self.content = base64.b64decode(value["body"])
        self.text = self.content.decode("utf-8", "replace")

    def json(self):
        return json_module.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def request(target, method="GET", path="/", *, headers=None, content=b"", json=None):
    if isinstance(target, str) and target.startswith("https://"):
        if "\\" in target or any(ord(c) <= 32 or ord(c) == 127 for c in target):
            raise EgressError("NETWORK_DENIED")
        try:
            parsed = urlsplit(target)
        except ValueError:
            raise EgressError("NETWORK_DENIED") from None
        if (
            parsed.fragment
            or parsed.username
            or parsed.password
            or (path != "/" and (parsed.path not in {"", "/"} or parsed.query))
        ):
            raise EgressError("NETWORK_DENIED")
        if path == "/":
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        target = "https://" + parsed.netloc
    headers = dict(headers or {})
    if json is not None:
        content = json_module.dumps(json).encode()
        headers["Content-Type"] = "application/json"
    if isinstance(content, str):
        content = content.encode()
    return Response(
        call(
            "/request",
            {
                "target": target,
                "method": method.upper(),
                "path": path,
                "headers": headers,
                "body": base64.b64encode(content).decode(),
            },
        )
    )


def result(request_id):
    return Response(call("/result", {"requestId": request_id}))
