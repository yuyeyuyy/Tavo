from typing import Literal

from pydantic import Field

from app.api.base import DTO


class SecurityOut(DTO):
    available: bool
    backend: str
    data_location: str


class SessionOut(DTO):
    access_token: str = Field(repr=False)
    expires_at: str
    token_type: Literal["bearer"] = "bearer"


class Health(DTO):
    status: Literal["ok"] = "ok"
    version: str = "0.1.0"


class SystemStatus(DTO):
    security: SecurityOut
    worker_active: bool
    mode: Literal["single-owner"] = "single-owner"


class ErrorDetail(DTO):
    code: str
    message: str


class ErrorResponse(DTO):
    error: ErrorDetail
