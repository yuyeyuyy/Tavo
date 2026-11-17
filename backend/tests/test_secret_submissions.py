import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import select

from app.core.errors import AppError
from app.credentials.contracts import ChallengeRequest, ProtectedHeader, SecretSubmission
from app.credentials.envelope import decode, encode
from app.credentials.service import SecretSubmissionService
from app.llm.config import ModelConfig, ModelProfile
from app.models.entities import CredentialState, Secret

KEY = "synthetic-upload-key-never-real"


def setup(f):
    profile = ModelProfile(
        id="upload-test", provider="deepseek", model="fixture", label="Upload", enabled=True
    )
    f.services.chat.config = ModelConfig(profiles=[profile])
    return profile


def challenge(f, p, revision=None):
    return f.client.post(
        "/api/v1/secret-submissions/challenges",
        json={
            "purpose": "model.api_key",
            "resourceId": p.id,
            "expectedRevision": f.services.chat.keys.revision(p) if revision is None else revision,
        },
    )


def sealed(value, key=KEY, header_changes=None, **parts):
    from cryptography.hazmat.primitives.asymmetric import rsa

    jwk = value["publicJwk"]
    public = rsa.RSAPublicNumbers(65537, int.from_bytes(decode(jwk["n"]), "big")).public_key()
    header = ProtectedHeader(
        kid=value["kid"],
        challenge_id=value["challengeId"],
        purpose=value["purpose"],
        resource_id=value["resourceId"],
    ).model_dump(by_alias=True)
    header.update(header_changes or {})
    protected = encode(json.dumps(header, separators=(",", ":")).encode())
    cek, iv = AESGCM.generate_key(bit_length=256), os.urandom(12)
    encrypted = AESGCM(cek).encrypt(iv, key.encode(), protected.encode())
    envelope = {
        "protected": protected,
        "wrapped": encode(
            public.encrypt(
                cek,
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
                ),
            )
        ),
        "iv": encode(iv),
        "ciphertext": encode(encrypted[:-16]),
        "tag": encode(encrypted[-16:]),
    }
    envelope.update(parts)
    return {"challengeId": value["challengeId"], "envelope": ".".join(envelope.values())}


def save(f, payload):
    return f.client.post("/api/v1/secret-submissions", json=payload)


def test_roundtrip_revision_no_secret_in_public_data_or_logs(f, caplog):
    p = setup(f)
    proof = challenge(f, p)
    assert proof.status_code == 200 and proof.headers["cache-control"] == "no-store"
    assert set(proof.json()["publicJwk"]) == {"kty", "n", "e", "alg", "ext", "keyOps"}
    payload = sealed(proof.json())
    assert KEY not in json.dumps(payload)
    response = save(f, payload)
    assert response.status_code == 200
    assert response.json() == {"resourceId": p.id, "credentialRevision": 1, "ready": True}
    assert f.services.chat.keys.get(p).get_secret_value() == KEY
    assert (
        KEY
        not in response.text + proof.text + caplog.text + f.client.get("/api/v1/chat/models").text
    )
    with f.services.db.session() as session:
        assert KEY.encode() not in session.get(Secret, f.services.chat.keys.reference(p)).ciphertext
        assert session.scalar(select(CredentialState)).revision == 1
    assert save(f, payload).status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"alg": "RSA1_5"},
        {"enc": "A128GCM"},
        {"kid": "z" * 32},
        {"purpose": "connector.key"},
        {"resourceId": "other"},
        {"jku": "https://evil.invalid"},
        {"zip": "DEF"},
    ],
)
def test_protected_header_scope_and_algorithm_rejected(f, changes):
    p = setup(f)
    proof = challenge(f, p).json()
    assert save(f, sealed(proof, header_changes=changes)).status_code == 422
    assert f.services.chat.keys.revision(p) == 0


@pytest.mark.parametrize("part", ["wrapped", "iv", "ciphertext", "tag", "protected"])
def test_tampering_consumes_challenge_without_storing(f, part):
    p = setup(f)
    proof = challenge(f, p).json()
    payload = sealed(proof)
    pieces = payload["envelope"].split(".")
    index = ["protected", "wrapped", "iv", "ciphertext", "tag"].index(part)
    pieces[index] = ("A" if pieces[index][0] != "A" else "B") + pieces[index][1:]
    payload["envelope"] = ".".join(pieces)
    assert save(f, payload).status_code == 422
    assert save(f, sealed(proof)).status_code == 409
    assert not f.services.chat.keys.ready(p)


def test_expired_session_and_other_session_cannot_submit(f):
    p = setup(f)
    original = f.client.headers["Authorization"]
    proof = challenge(f, p).json()
    bootstrap = f.services.sessions.owner_key().decode()
    other, _ = f.services.sessions.issue(bootstrap)
    f.client.headers["Authorization"] = "Bearer " + other
    assert save(f, sealed(proof)).status_code == 409
    f.client.headers["Authorization"] = original
    f.clock.advance(121)
    assert save(f, sealed(proof)).status_code == 409
    f.clock.advance(1000)
    assert challenge(f, p).status_code == 401


def test_unknown_purpose_profile_plaintext_and_auth_are_rejected(f):
    p = setup(f)
    for value in [
        {"purpose": "arbitrary", "resourceId": p.id, "expectedRevision": 0},
        {"purpose": "model.api_key", "resourceId": p.id, "expectedRevision": 0, "apiKey": KEY},
        {"purpose": "model.api_key", "resourceId": p.id, "expectedRevision": True},
    ]:
        assert f.client.post("/api/v1/secret-submissions/challenges", json=value).status_code == 422
    assert challenge(f, p.model_copy(update={"id": "missing"})).status_code == 503
    proof = challenge(f, p).json()
    assert save(f, {**sealed(proof), "apiKey": KEY}).status_code == 422
    f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    assert challenge(f, p).status_code == 401
    assert save(f, sealed(proof)).status_code == 401


def test_challenge_capacity_expiry_and_restart(f):
    p = setup(f)
    old = challenge(f, p).json()
    for _ in range(3):
        assert challenge(f, p).status_code == 200
    assert challenge(f, p).status_code == 409
    f.clock.advance(121)
    assert challenge(f, p).status_code == 200
    replacement = SecretSubmissionService(
        f.services.sessions, f.services.secret_submissions.handlers, f.clock.now
    )
    previous = f.services.secret_submissions
    f.services.secret_submissions = replacement
    asyncio.run(previous.close())
    assert save(f, sealed(old)).status_code == 409


def test_two_challenges_cli_update_delete_and_aba(f):
    p = setup(f)
    first, second = challenge(f, p).json(), challenge(f, p).json()
    assert save(f, sealed(first)).status_code == 200
    assert save(f, sealed(second)).json()["error"]["code"] == "SECRET_REVISION_CONFLICT"
    assert challenge(f, p, 0).status_code == 409
    stale = challenge(f, p).json()
    f.services.chat.keys.put(p, SecretStr("synthetic-new-key"))
    assert save(f, sealed(stale)).status_code == 409
    f.services.chat.keys.remove(p)
    absent = challenge(f, p).json()
    f.services.chat.keys.put(p, SecretStr("synthetic-another-key"))
    f.services.chat.keys.remove(p)
    assert save(f, sealed(absent)).status_code == 409
    assert not f.services.chat.keys.ready(p)
    assert f.services.chat.keys.revision(p) == 5


def test_rotation_does_not_conflict_but_endpoint_change_does(f):
    p = setup(f)
    f.services.chat.keys.put(p, SecretStr(KEY))
    proof = challenge(f, p).json()
    f.services.secrets.rotate()
    assert f.services.chat.keys.revision(p) == 1
    assert save(f, sealed(proof)).status_code == 200
    proof = challenge(f, p).json()
    f.services.chat.config = ModelConfig(
        profiles=[p.model_copy(update={"base_url": "https://other.invalid/v1"})]
    )
    assert save(f, sealed(proof)).status_code == 409


@pytest.mark.parametrize("value", ["", "x" * 8193, "包含非ASCII", " leading-space"])
def test_invalid_plaintext_never_overwrites_existing_secret(f, value):
    p = setup(f)
    f.services.chat.keys.put(p, SecretStr(KEY))
    proof = challenge(f, p).json()
    response = save(f, sealed(proof, key=value))
    assert response.status_code == 422
    assert f.services.chat.keys.get(p).get_secret_value() == KEY
    assert f.services.chat.keys.revision(p) == 1


def test_maximum_key_is_encrypted_and_saved(f):
    p = setup(f)
    value = "x" * 8192
    assert save(f, sealed(challenge(f, p).json(), key=value)).status_code == 200
    assert f.services.chat.keys.get(p).get_secret_value() == value


def test_parallel_replay_commits_once_and_cancelled_crypto_does_not_commit(f, monkeypatch):
    p = setup(f)
    token = f.client.headers["Authorization"][7:]
    service = f.services.secret_submissions

    async def exercise():
        request = ChallengeRequest(purpose="model.api_key", resource_id=p.id, expected_revision=0)
        proof = (await service.issue(request, token)).model_dump(by_alias=True)
        value = SecretSubmission.model_validate(sealed(proof))
        results = await asyncio.gather(
            service.submit(value, token, AsyncMock(return_value=False)),
            service.submit(value, token, AsyncMock(return_value=False)),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert service.active == 0
        import app.credentials.service as module

        original = module.decrypt

        def slow(*args):
            time.sleep(0.08)
            return original(*args)

        monkeypatch.setattr(module, "decrypt", slow)
        proof = await service.issue(request.model_copy(update={"expected_revision": 1}), token)
        value = SecretSubmission.model_validate(
            sealed(proof.model_dump(by_alias=True), key="synthetic-late-key")
        )
        task = asyncio.create_task(service.submit(value, token, AsyncMock(return_value=False)))
        await asyncio.sleep(0.01)
        task.cancel()
        assert service.active == 1
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.active == 0

    asyncio.run(exercise())
    assert f.services.chat.keys.get(p).get_secret_value() == KEY


def test_disconnect_and_expiry_during_decryption_do_not_save(f, monkeypatch):
    p = setup(f)
    service = f.services.secret_submissions
    token = f.client.headers["Authorization"][7:]

    async def exercise():
        proof = await service.issue(
            ChallengeRequest(purpose="model.api_key", resource_id=p.id, expected_revision=0), token
        )
        with pytest.raises(AppError, match="CANCELLED"):
            await service.submit(
                SecretSubmission.model_validate(sealed(proof.model_dump(by_alias=True))),
                token,
                AsyncMock(return_value=True),
            )
        proof = await service.issue(
            ChallengeRequest(purpose="model.api_key", resource_id=p.id, expected_revision=0), token
        )
        import app.credentials.service as module

        original = module.decrypt

        def expires(*args):
            value = original(*args)
            f.clock.advance(121)
            return value

        monkeypatch.setattr(module, "decrypt", expires)
        with pytest.raises(AppError, match="SECRET_CHALLENGE_INVALID"):
            await service.submit(
                SecretSubmission.model_validate(sealed(proof.model_dump(by_alias=True))),
                token,
                AsyncMock(return_value=False),
            )

    asyncio.run(exercise())
    assert not f.services.chat.keys.ready(p)


def test_migration_backfills_model_versions_without_changing_ciphertext(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    url = f"sqlite:///{tmp_path / 'upgrade.sqlite'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0002_agent_foundations")
    engine = create_engine(url)
    try:
        with engine.begin() as session:
            session.execute(
                text("INSERT INTO secrets VALUES (:reference,:ciphertext,:key)"),
                [
                    {
                        "reference": "llm:test:binding",
                        "ciphertext": b"opaque-original-ciphertext",
                        "key": "old",
                    },
                    {
                        "reference": "connector:test",
                        "ciphertext": b"opaque-connector",
                        "key": "old",
                    },
                ],
            )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as session:
            assert session.execute(
                text("SELECT reference,revision,present FROM credential_states")
            ).all() == [("llm:test:binding", 1, 1)]
            assert (
                session.execute(
                    text("SELECT ciphertext FROM secrets WHERE reference='llm:test:binding'")
                ).scalar()
                == b"opaque-original-ciphertext"
            )
    finally:
        engine.dispose()


def test_authorization_is_rechecked_after_waiting_for_the_writer(f, monkeypatch):
    from contextlib import contextmanager

    p = setup(f)
    proof = challenge(f, p).json()
    original = f.services.db.session
    advanced = False

    @contextmanager
    def delayed(write=False):
        nonlocal advanced
        with original(write=write) as session:
            if write and not advanced:
                advanced = True
                f.clock.advance(121)
            yield session

    monkeypatch.setattr(f.services.db, "session", delayed)
    assert save(f, sealed(proof)).status_code == 409
    assert not f.services.chat.keys.ready(p)


def test_global_challenge_and_active_decryption_capacity(f):
    p = setup(f)
    service = f.services.secret_submissions
    bootstrap = f.services.sessions.owner_key().decode()
    tokens = [f.services.sessions.issue(bootstrap)[0] for _ in range(17)]
    request = ChallengeRequest(purpose="model.api_key", resource_id=p.id, expected_revision=0)

    async def exercise():
        proof = None
        for token in tokens[:16]:
            for _ in range(4):
                proof = await service.issue(request, token)
        assert len(service.challenges) == 64
        with pytest.raises(AppError, match="BUSY"):
            await service.issue(request, tokens[-1])
        service.active = 2
        value = SecretSubmission.model_validate(sealed(proof.model_dump(by_alias=True)))
        with pytest.raises(AppError, match="BUSY"):
            await service.submit(value, tokens[15], AsyncMock(return_value=False))
        service.active = 0
        assert value.challenge_id in service.challenges
        await service.submit(value, tokens[15], AsyncMock(return_value=False))

    asyncio.run(exercise())
