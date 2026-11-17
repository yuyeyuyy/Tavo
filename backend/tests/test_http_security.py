import json

from app.core.security import private_read, private_write


def test_auth_origin_host_validation_and_body_limit(f):
    assert f.client.get("/api/v1/health").json()["status"] == "ok"
    token = f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    assert f.client.get("/api/v1/health").status_code == 401
    assert (
        f.client.get(
            "/api/v1/sessions",
            headers={
                "Authorization": "Bearer " + private_read(f.settings.client_token_file).decode()
            },
        ).status_code
        == 401
    )
    f.client.headers["Authorization"] = token
    assert (
        f.client.get("/api/v1/sessions", headers={"Origin": "https://attacker.invalid"}).status_code
        == 403
    )
    assert f.client.get("/api/v1/sessions", headers={"Host": "attacker.invalid"}).status_code == 403
    bad = f.client.post(
        "/api/v1/sessions", json={"profileId": "mock", "secret": "synthetic-secret-marker"}
    )
    assert bad.status_code == 422 and "synthetic-secret-marker" not in bad.text
    assert f.client.get("/api/v1/sessions?limit=0").status_code == 422
    assert (
        f.client.post(
            "/api/v1/sessions", content=b"x" * 32769, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )


def test_session_expiration_and_owner_rotation(f):
    f.clock.advance(1000)
    assert f.client.get("/api/v1/health").status_code == 401
    owner = private_read(f.settings.client_token_file).decode()
    response = f.client.post("/api/v1/auth/session", headers={"Authorization": "Bearer " + owner})
    f.client.headers["Authorization"] = "Bearer " + response.json()["accessToken"]
    assert f.client.get("/api/v1/health").status_code == 200
    private_write(f.settings.client_token_file, b"a-new-random-owner-token-for-test-only-123456789")
    assert f.client.get("/api/v1/health").status_code == 401


def test_authenticated_offline_docs_advertise_only_active_api(f):
    spec = f.client.get("/openapi.json").json()
    assert "/api/v1/sessions" in spec["paths"]
    assert not any("accounts" in path or "snapshot" in path for path in spec["paths"])
    assert f.client.get("/_docs/swagger-ui-bundle.js").status_code == 200
    assert "swagger-ui-bundle.js" in f.client.get("/docs").text
    assert f.client.get("/_docs/not-a-file").status_code == 404
    assert private_read(f.settings.client_token_file).decode() not in json.dumps(spec)
