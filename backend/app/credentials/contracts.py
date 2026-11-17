from typing import Annotated, Literal

from pydantic import Field

from app.api.base import DTO
from app.llm.contracts import ProfileId

Identity = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{32}$")]
Purpose = Literal["model.api_key", "sandbox.api_key"]
Revision = Annotated[int, Field(strict=True, ge=0)]


class ChallengeRequest(DTO):
    purpose: Purpose
    resource_id: ProfileId
    expected_revision: Revision


class PublicJWK(DTO):
    kty: Literal["RSA"] = "RSA"
    n: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{512}$")]
    e: Literal["AQAB"] = "AQAB"
    alg: Literal["RSA-OAEP-256"] = "RSA-OAEP-256"
    ext: Literal[True] = True
    key_ops: list[Literal["wrapKey"]] = Field(default_factory=lambda: ["wrapKey"], max_length=1)


class ProtectedHeader(DTO):
    v: Literal[1] = 1
    typ: Literal["tavo-secret+jwe"] = "tavo-secret+jwe"
    alg: Literal["RSA-OAEP-256"] = "RSA-OAEP-256"
    enc: Literal["A256GCM"] = "A256GCM"
    kid: Identity
    challenge_id: Identity
    purpose: Purpose
    resource_id: ProfileId


class ChallengeOut(DTO):
    version: Literal[1] = 1
    challenge_id: Identity
    expires_at: str
    purpose: Purpose
    resource_id: ProfileId
    credential_revision: Revision
    kid: Identity
    public_jwk: PublicJWK


class SecretSubmission(DTO):
    challenge_id: Identity
    envelope: Annotated[str, Field(min_length=100, max_length=16384, pattern=r"^[A-Za-z0-9_.-]+$")]


class SubmissionAck(DTO):
    resource_id: ProfileId
    credential_revision: Revision
    ready: Literal[True] = True
