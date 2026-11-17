import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_secret_submissions import sealed

from app.core.errors import AppError
from app.core.security import SecretStore, initialize_keys
from app.llm.adapter import ModelGateway
from app.llm.config import PRESETS, ModelConfig, ModelProfile
from app.llm.contracts import ModelProfileCreate
from app.models.entities import ModelCatalogState, ModelProfileRecord, Secret
from app.repositories.database import Database
from app.services.model_profiles import ModelProfileService


def create(f, **extra):
    return f.client.post(
        "/api/v1/model-profiles", json={"provider": "deepseek", "model": "fixture-text", **extra}
    )


def test_empty_catalog_can_list_providers_and_create_without_restart_or_vendor_call(f):
    calls = []
    f.services.chat.gateway = ModelGateway(
        httpx.MockTransport(lambda request: calls.append(request))
    )
    providers = f.client.get("/api/v1/model-providers")
    assert providers.status_code == 200
    assert providers.headers["cache-control"] == "no-store"
    assert {item["id"] for item in providers.json()["items"]} == set(PRESETS)
    assert f.client.get("/api/v1/model-profiles").json()["items"] == []
    response = create(f, label="日常对话")
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["id"].startswith("model-") and value["enabled"]
    assert value["credentialRevision"] == 0 and not value["ready"]
    assert value["label"] == "日常对话"
    assert set(value) == {
        "id",
        "label",
        "provider",
        "model",
        "enabled",
        "ready",
        "credentialRevision",
    }
    assert f.client.get("/api/v1/chat/models").json()["items"][0]["id"] == value["id"]
    assert not calls
    assert not f.settings.models_file.exists()


def test_new_model_immediately_supports_encrypted_key_and_chat(f, caplog):
    profile = create(f).json()
    challenge = f.client.post(
        "/api/v1/secret-submissions/challenges",
        json={"purpose": "model.api_key", "resourceId": profile["id"], "expectedRevision": 0},
    )
    key = "synthetic-client-profile-key"
    assert (
        f.client.post(
            "/api/v1/secret-submissions", json=sealed(challenge.json(), key=key)
        ).status_code
        == 200
    )
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + key
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "public answer"},
                    }
                ]
            },
        )

    f.services.chat.gateway = ModelGateway(httpx.MockTransport(respond))
    response = f.client.post(
        "/api/v1/chat/completions",
        json={"profileId": profile["id"], "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200 and response.json()["message"]["content"] == "public answer"
    assert len(calls) == 1
    public = f.client.get("/api/v1/model-profiles").text
    assert key not in public + response.text + caplog.text
    assert json.loads(public)["items"][0]["ready"]
    with f.services.db.session() as session:
        assert key not in repr(session.scalar(select(ModelProfileRecord)).__dict__)
        assert key.encode() not in session.scalar(select(Secret)).ciphertext


@pytest.mark.parametrize(
    "extra",
    [
        {"provider": "unknown"},
        {"model": "SET_MODEL_ID"},
        {"model": ""},
        {"model": "x" * 161},
        {"provider": "gemini", "model": "models/unsafe"},
        {"label": "   "},
        {"label": "x" * 81},
        {"apiKey": "synthetic-forbidden-key"},
        {"baseUrl": "https://other.invalid"},
        {"headers": {}},
        {"id": "chosen-by-client"},
        {"enabled": False},
        {"maxOutputTokens": 8192},
    ],
)
def test_profile_creation_rejects_invalid_or_privileged_input(f, extra):
    response = create(f, **extra)
    assert response.status_code == 422
    assert "synthetic-forbidden-key" not in response.text
    assert f.services.models.snapshot().profiles == []


def test_profiles_require_authentication_and_exact_origin(f):
    assert create(f, label="Valid").status_code == 201
    response = f.client.post(
        "/api/v1/model-profiles",
        headers={"Origin": "https://other.invalid"},
        json={"provider": "deepseek", "model": "fixture"},
    )
    assert response.status_code == 403
    f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    for path in ["/model-providers", "/model-profiles"]:
        assert f.client.get("/api/v1" + path).status_code == 401
    assert create(f).status_code == 401


def test_capacity_serializes_writers_at_32(f):
    request = ModelProfileCreate(provider="deepseek", model="fixture")
    for _ in range(31):
        f.services.models.create(request, lambda: None)

    def attempt():
        try:
            f.services.models.create(request, lambda: None)
            return "created"
        except AppError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["MODEL_PROFILE_LIMIT", "created"]
    assert create(f).status_code == 409
    assert len(f.services.models.snapshot().profiles) == 32


def test_authentication_is_rechecked_after_waiting_for_writer(f, monkeypatch):
    original = f.services.db.session
    advanced = False

    @contextmanager
    def delayed(write=False):
        nonlocal advanced
        with original(write=write) as session:
            if write and not advanced:
                advanced = True
                f.clock.advance(1000)
            yield session

    monkeypatch.setattr(f.services.db, "session", delayed)
    assert create(f).status_code == 401
    assert not f.services.models.snapshot().profiles


def test_import_preserves_profiles_custom_endpoint_default_and_key_then_ignores_json(f):
    # Emulate an existing 0003 installation awaiting its first 0004 import.
    with f.services.db.session(write=True) as session:
        session.delete(session.get(ModelCatalogState, 1))
    profile = ModelProfile(
        id="old-profile",
        provider="deepseek",
        model="old-text",
        label="Old",
        enabled=True,
        base_url="http://127.0.0.1:12345/v1",
        max_output_tokens=4096,
    )
    disabled = ModelProfile(
        id="disabled", provider="openai", model="SET_MODEL_ID", label="Disabled"
    )
    key = "synthetic-existing-model-key"
    f.services.chat.keys.put(profile, SecretStr(key))
    before = None
    with f.services.db.session() as session:
        before = session.get(Secret, f.services.chat.keys.reference(profile)).ciphertext
    f.settings.models_file.write_text(
        ModelConfig(profiles=[profile, disabled], default_profile_id=profile.id).model_dump_json(
            by_alias=True
        )
    )
    original_json = f.settings.models_file.read_bytes()
    f.services.models.initialize()
    assert f.services.models.snapshot().profiles == [profile, disabled]
    assert f.services.models.snapshot().default_profile_id == profile.id
    assert f.services.chat.catalog().items[0].ready
    created = create(f).json()
    f.settings.models_file.write_text("invalid, ignored after first import")
    second = ModelProfileService(f.services.db, f.services.secrets, f.settings.models_file)
    second.initialize()
    assert second.profile(created["id"]).model == "fixture-text"
    assert second.profile(profile.id) == profile
    assert f.services.chat.keys.get(profile).get_secret_value() == key
    with f.services.db.session() as session:
        assert session.get(Secret, f.services.chat.keys.reference(profile)).ciphertext == before
    assert b"synthetic-existing" not in original_json


def test_import_failure_is_atomic_and_empty_import_does_not_later_override(f):
    with f.services.db.session(write=True) as session:
        session.delete(session.get(ModelCatalogState, 1))
    f.settings.models_file.write_text('{"profiles":[{"provider":"invalid"}]}')
    with pytest.raises(AppError, match="MODEL_CONFIG_INVALID"):
        f.services.models.initialize()
    with f.services.db.session() as session:
        assert session.get(ModelCatalogState, 1) is None
        assert session.scalar(select(func.count()).select_from(ModelProfileRecord)) == 0
    f.settings.models_file.unlink()
    f.services.models.initialize()
    assert create(f).status_code == 201
    f.settings.models_file.write_text("not valid JSON")
    f.services.models.initialize()
    assert len(f.services.models.snapshot().profiles) == 1


def test_upgrade_from_0003_preserves_existing_secret_and_matches_metadata(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app.core.config import Settings

    root = Path(__file__).parents[1]
    settings = Settings(
        bash_enabled=False,
        data_dir=tmp_path,
        keyring_file=tmp_path / "keys",
        client_token_file=tmp_path / "token",
    )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database}")
    command.upgrade(config, "0003_secret_submission_state")
    initialize_keys(settings)
    db = Database(settings.database)
    try:
        store = SecretStore(db, settings)
        profile = ModelProfile(
            id="legacy", provider="deepseek", model="fixture", label="Old", enabled=True
        )
        from app.llm.keys import ModelKeys

        keys = ModelKeys(store)
        keys.put(profile, SecretStr("synthetic-upgrade-key"))
        with db.session() as session:
            before = session.get(Secret, keys.reference(profile)).ciphertext
        settings.models_file.write_text(
            ModelConfig(profiles=[profile]).model_dump_json(by_alias=True)
        )
        command.upgrade(config, "head")
        command.check(config)
        directory = ModelProfileService(db, store, settings.models_file)
        directory.initialize()
        assert directory.profile(profile.id) == profile
        assert keys.revision(profile) == 1 and keys.ready(profile)
        with db.session() as session:
            assert session.get(Secret, keys.reference(profile)).ciphertext == before
    finally:
        db.close()
