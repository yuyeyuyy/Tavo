import json

from sqlalchemy import select

from app.core.security import private_read, private_write
from app.models.entities import Secret


def test_health_auth_validation_and_origins(f):
    assert f.client.get("/api/v1/health").json()["status"] == "ok"
    assert f.client.get("/api/v1/system/status").json()["mode"] == "single-owner"
    token = f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    assert f.client.get("/api/v1/health").status_code == 401
    assert (
        f.client.get(
            "/api/v1/accounts",
            headers={
                "Authorization": "Bearer " + private_read(f.settings.client_token_file).decode()
            },
        ).status_code
        == 401
    )
    f.client.headers["Authorization"] = token
    assert (
        f.client.get("/api/v1/accounts", headers={"Origin": "https://attacker.invalid"}).status_code
        == 403
    )
    assert f.client.get("/api/v1/accounts", headers={"Host": "attacker.invalid"}).status_code == 403
    response = f.client.post(
        "/api/v1/accounts",
        json={
            "connectorId": "mock",
            "externalAccountId": "x",
            "displayName": "X",
            "secret": "synthetic-secret-marker",
        },
    )
    assert response.status_code == 422
    assert "synthetic-secret-marker" not in response.text
    assert f.client.get("/api/v1/accounts?limit=0").status_code == 422
    assert (
        f.client.post(
            "/api/v1/accounts", content=b"x" * 32769, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )


def test_account_crud_and_credential_isolation(f):
    a, b = f.add(), f.add("demo-b")
    assert a["id"] != b["id"]
    duplicate = f.client.post(
        "/api/v1/accounts",
        json={"connectorId": "mock", "externalAccountId": "demo-a", "displayName": "Duplicate"},
    )
    assert duplicate.status_code == 409
    assert f.client.get("/api/v1/accounts?limit=1").json()["total"] == 2
    assert len(f.client.get("/api/v1/accounts?limit=1&offset=1").json()["items"]) == 1
    raw = f.client.get("/api/v1/snapshot").text
    assert "mock-only:" not in raw and "credentialRef" not in raw and "ciphertext" not in raw
    with f.services.db.session() as session:
        secrets = list(session.scalars(select(Secret)))
        assert len(secrets) == 2
        assert all(b"mock-only:" not in row.ciphertext for row in secrets)
    assert (
        f.client.post(f"/api/v1/accounts/{a['id']}/disconnect").json()["status"] == "disconnected"
    )
    assert f.client.post(f"/api/v1/accounts/{a['id']}/sync").status_code == 409
    assert f.client.post(f"/api/v1/accounts/{a['id']}/reconnect").json()["credentialConfigured"]
    assert f.client.delete(f"/api/v1/accounts/{a['id']}").status_code == 204
    assert f.client.get(f"/api/v1/accounts/{a['id']}").status_code == 404
    assert f.client.get("/api/v1/accounts").json()["total"] == 1
    assert f.add()["id"] != a["id"]


def test_secrets_fail_closed_rotate_and_logs(f, caplog):
    a = f.add()
    secret = f.services.secrets.for_connector(a["id"], "mock")
    with f.services.db.session() as session:
        old = session.scalar(select(Secret)).ciphertext
    f.services.secrets.rotate()
    assert (
        f.services.secrets.for_connector(a["id"], "mock").get_secret_value()
        == secret.get_secret_value()
    )
    with f.services.db.session() as session:
        assert session.scalar(select(Secret)).ciphertext != old
    assert secret.get_secret_value() not in caplog.text
    assert secret.get_secret_value() not in repr(secret)
    f.settings.keyring_file.chmod(0o644)
    response = f.client.post(
        "/api/v1/accounts",
        json={"connectorId": "mock", "externalAccountId": "bad", "displayName": "Bad"},
    )
    assert response.status_code == 503
    assert f.client.get("/api/v1/accounts").json()["total"] == 1
    assert secret.get_secret_value() not in response.text
    f.settings.keyring_file.chmod(0o600)


def test_session_expiration_and_owner_rotation(f):
    f.clock.advance(1000)
    assert f.client.get("/api/v1/health").status_code == 401
    owner = private_read(f.settings.client_token_file).decode()
    session = f.client.post("/api/v1/auth/session", headers={"Authorization": "Bearer " + owner})
    f.client.headers["Authorization"] = "Bearer " + session.json()["accessToken"]
    assert f.client.get("/api/v1/health").status_code == 200
    private_write(f.settings.client_token_file, b"a-new-random-owner-token-for-test-only-123456789")
    assert f.client.get("/api/v1/health").status_code == 401


def test_registry_capabilities_and_offline_docs(f):
    assert f.client.get("/api/v1/connectors").json()[0]["id"] == "mock"
    assert "balance" in f.client.get("/api/v1/connectors/mock/capabilities").json()["capabilities"]
    assert f.client.get("/api/v1/connectors/missing/capabilities").status_code == 404
    assert f.client.get("/openapi.json").status_code == 200
    spec = f.client.get("/openapi.json").json()
    assert "/api/v1/accounts/{account_id}/sync" in spec["paths"]
    assert f.client.get("/_docs/swagger-ui-bundle.js").status_code == 200
    assert "swagger-ui-bundle.js" in f.client.get("/docs").text
    assert f.client.get("/_docs/not-a-file").status_code == 404
    # Public examples contain no configured secret material.
    assert private_read(f.settings.client_token_file).decode() not in json.dumps(spec)
