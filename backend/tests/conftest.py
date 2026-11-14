from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.connectors.registry import MockConnector
from app.core.config import Settings
from app.core.security import initialize_keys, private_read
from app.main import create_app
from app.repositories.database import upgrade_database
from app.services.application import Services


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 13, tzinfo=UTC)

    def now(self):
        return self.value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@dataclass
class Fixture:
    client: TestClient
    services: Services
    worker: object
    clock: Clock
    settings: Settings

    def add(self, identity="demo-a"):
        result = self.client.post(
            "/api/v1/accounts",
            json={"connectorId": "mock", "externalAccountId": identity, "displayName": identity},
        )
        assert result.status_code == 201
        return result.json()


@pytest.fixture
def f(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        keyring_file=tmp_path / "master.keys",
        client_token_file=tmp_path / "client.token",
        worker_enabled=False,
        timeout_seconds=0.03,
        lease_seconds=2,
    )
    upgrade_database(settings.database)
    initialize_keys(settings)
    clock = Clock()
    app = create_app(
        settings, lambda value: Services(value, now=clock.now, connector=MockConnector(0))
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/session",
            headers={
                "Authorization": "Bearer " + private_read(settings.client_token_file).decode()
            },
        )
        assert response.status_code == 200
        client.headers["Authorization"] = "Bearer " + response.json()["accessToken"]
        yield Fixture(client, app.state.services, app.state.worker, clock, settings)
