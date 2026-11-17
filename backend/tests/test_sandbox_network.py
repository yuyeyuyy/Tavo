import base64
import socket

import httpx
import pytest

from app.core.sandbox_config import NetworkTarget, default_policy
from sandbox.runtime.tavo_sandbox.network import (
    NetworkFailure,
    Outbound,
    normalize_request,
    public_addresses,
)


def config():
    value = default_policy().network.model_dump()
    value["mode"] = "proxy"
    value.update(allow_public_https=False, require_write_approval=True)
    value["targets"] = [
        NetworkTarget(
            id="test-api",
            hostname="api.example.test",
            paths=["/v1"],
            methods=["GET", "POST"],
            capability="api_test",
        ).model_dump()
    ]
    return value


def resolver(_host, _port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_default_doh_avoids_system_fake_ip_but_explicit_system_still_denies_it(
    monkeypatch, tmp_path
):
    from app.core.sandbox_config import load_policy
    from sandbox.runtime.tavo_sandbox import network

    lookups = []

    def doh(host, port, **kwargs):
        lookups.append(host)
        return resolver(host, port, **kwargs)

    def fake_ip(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.12", 443))]

    monkeypatch.setattr(network, "doh_resolve", doh)
    monkeypatch.setattr(network.socket, "getaddrinfo", fake_ip)
    policy = load_policy().network.model_dump()
    outbound = Outbound(policy, transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    try:
        assert outbound.fetch({"target": "https://api-docs.deepseek.com"})["status"] == 200
        assert lookups == ["api-docs.deepseek.com"]
    finally:
        outbound.close()
    path = tmp_path / "sandbox.toml"
    path.write_text('[network]\ndns_mode="system"\n')
    outbound = Outbound(load_policy(path).network.model_dump())
    try:
        with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
            outbound.fetch({"target": "https://api-docs.deepseek.com"})
        assert lookups == ["api-docs.deepseek.com"]
    finally:
        outbound.close()


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_public_https_all_methods_execute_without_request_approval_or_saved_credentials(method):
    policy = config()
    policy.update(allow_public_https=True, require_write_approval=False)
    policy["targets"][0]["credential"] = True
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(200, json={"method": request.method})

    with_outbound = Outbound(
        policy,
        {"test-api": "synthetic-bound-secret"},
        resolver=resolver,
        transport=httpx.MockTransport(answer),
    )
    try:
        for url in ("https://other.example.test", "https://api.example.test"):
            response = with_outbound.fetch({"target": url, "method": method, "path": "/anything"})
            assert response["status"] == 200
        assert all(r.method == method and "authorization" not in r.headers for r in seen)
        assert all(b"synthetic-bound-secret" not in r.content for r in seen)
    finally:
        with_outbound.close()


@pytest.mark.parametrize(
    "target",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://localhost",
        "https://a.local",
        "https://[::1]",
        "https://a.com:444",
        "https://name:password@a.com",
        "https://a.com/path",
        "https://a.com?x=1",
        "https://a.com#fragment",
        "https://a.com\\@b.com",
        "https://a.com\n",
    ],
)
def test_public_url_validation_keeps_origin_and_private_address_boundaries(target):
    policy = config()
    policy.update(allow_public_https=True, require_write_approval=False)
    outbound = Outbound(
        policy, resolver=resolver, transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    try:
        with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
            outbound.fetch({"target": target})
    finally:
        outbound.close()


def test_public_redirects_resolve_every_hop_and_cannot_reach_internal_network():
    policy = config()
    policy.update(allow_public_https=True, require_write_approval=False)
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://internal.example.test/secret"})

    def resolve(host, *args, **kwargs):
        if host == "internal.example.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
        return resolver(host, *args, **kwargs)

    outbound = Outbound(policy, resolver=resolve, transport=httpx.MockTransport(answer))
    try:
        with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
            outbound.fetch({"target": "https://public.example.test"})
        assert len(seen) == 1
    finally:
        outbound.close()


def test_https_helper_keeps_path_query_and_does_not_accept_ambiguous_path(monkeypatch):
    from sandbox.runtime.tavo_sandbox import http

    seen = []

    def capture(endpoint, payload):
        seen.append(payload)
        return {"status": 200, "headers": {}, "body": ""}

    monkeypatch.setattr(http, "call", capture)
    http.request("https://public.example.test/v1/data?a=1", method="post", json={"ok": True})
    assert seen[0]["target"] == "https://public.example.test"
    assert seen[0]["path"] == "/v1/data?a=1" and seen[0]["method"] == "POST"
    with pytest.raises(http.EgressError, match="NETWORK_DENIED"):
        http.request("https://public.example.test/v1/data", path="/other")


@pytest.mark.parametrize("leak_in_location", [False, True])
def test_credential_redaction_survives_public_cross_origin_redirect(leak_in_location):
    from urllib.parse import quote

    policy = config()
    policy.update(allow_public_https=True, require_write_approval=False)
    policy["targets"][0]["credential"] = True
    secret = "synthetic-bound-secret"
    seen = []

    def answer(request):
        seen.append(request)
        if len(seen) == 1:
            assert request.headers["authorization"] == "Bearer " + secret
            suffix = "?token=" + quote(secret, safe="") if leak_in_location else ""
            return httpx.Response(
                302, headers={"location": "https://other.example.test/data" + suffix}
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, text=secret)

    outbound = Outbound(
        policy, {"test-api": secret}, resolver=resolver, transport=httpx.MockTransport(answer)
    )
    try:
        with pytest.raises(NetworkFailure, match="POLICY_DENIED"):
            outbound.fetch({"target": "test-api", "path": "/v1/start"})
        assert len(seen) == (1 if leak_in_location else 2)
    finally:
        outbound.close()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "192.168.1.1",
        "::1",
        "::ffff:93.184.216.34",
        "fd00::1",
        "168.63.129.16",
    ],
)
def test_private_reserved_metadata_and_mapped_addresses_denied(address):
    def fake(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
        public_addresses("api.example.test", default_policy().network.protected_cidrs, fake)


@pytest.mark.parametrize(
    "path",
    [
        "//other.example/v1",
        "http://127.0.0.1/v1",
        "/v1/../../admin",
        "/v1/%252e%252e/admin",
        "/v1/ok%0d%0aHost:other",
        "/v1/ok#fragment",
        "/not-allowed",
    ],
)
def test_path_and_origin_confusion_denied(path):
    with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
        normalize_request({"target": "test-api", "path": path}, config())


def test_authorized_public_request_pins_ip_keeps_tls_sni_and_does_not_use_env_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    captured = []

    def answer(request):
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    outbound = Outbound(config(), resolver=resolver, transport=httpx.MockTransport(answer))
    try:
        result = outbound.fetch({"target": "test-api", "path": "/v1/test"})
        assert result["status"] == 200 and base64.b64decode(result["body"]) == b'{"ok":true}'
        assert captured[0].url.host == "93.184.216.34"
        assert captured[0].headers["host"] == "api.example.test"
        assert captured[0].extensions["sni_hostname"] == "api.example.test"
        with pytest.raises(NetworkFailure, match="APPROVAL_REQUIRED"):
            outbound.fetch({"target": "test-api", "method": "POST", "path": "/v1/test"})
    finally:
        outbound.close()


def test_dns_rebinding_and_redirect_to_internal_target_are_blocked():
    calls = 0

    def changing(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return (
            resolver("", 443)
            if calls == 1
            else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        )

    outbound = Outbound(
        config(),
        resolver=changing,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "/v1/next"})
        ),
    )
    try:
        with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
            outbound.fetch({"target": "test-api", "path": "/v1/test"})
        assert calls == 2
    finally:
        outbound.close()


def test_secret_injection_never_returns_an_echoed_secret_and_requires_exact_approval():
    policy = config()
    policy["targets"][0]["credential"] = True
    synthetic = "synthetic-api-credential"
    outbound = Outbound(
        policy,
        {"test-api": synthetic},
        resolver=resolver,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=request.headers["authorization"])
        ),
    )
    try:
        with pytest.raises(NetworkFailure, match="POLICY_DENIED"):
            outbound.fetch({"target": "test-api", "path": "/v1/test"})
    finally:
        outbound.close()


def test_same_origin_redirect_cannot_widen_credential_path_to_another_target():
    policy = config()
    policy["targets"][0].update(paths=["/v1/private"], credential=True)
    policy["targets"].append(
        {**policy["targets"][0], "id": "other-path", "paths": ["/v1/public"], "credential": False}
    )
    observed = []

    def reply(request):
        observed.append(request)
        return httpx.Response(302, headers={"location": "/v1/public/echo"})

    outbound = Outbound(
        policy,
        {"test-api": "synthetic-bound-token"},
        resolver=resolver,
        transport=httpx.MockTransport(reply),
    )
    try:
        with pytest.raises(NetworkFailure, match="NETWORK_DENIED"):
            outbound.fetch({"target": "test-api", "path": "/v1/private/test"})
        assert len(observed) == 1
    finally:
        outbound.close()
