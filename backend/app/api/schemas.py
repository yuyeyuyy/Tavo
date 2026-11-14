from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_serializer, model_validator
from pydantic.alias_generators import to_camel

Id = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9:_-]+$")]
Money = Annotated[str, Field(pattern=r"^\d{1,12}\.\d{2}$")]
Status = Literal["disconnected", "connecting", "connected", "expired", "error"]
TaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
Scenario = Literal[
    "success",
    "network_once",
    "rate_limit_once",
    "server_once",
    "timeout",
    "auth",
    "forbidden",
    "invalid",
]
EventType = Literal[
    "account.connected",
    "account.disconnected",
    "sync.started",
    "sync.completed",
    "sync.failed",
    "data.changed",
    "sync.retry_scheduled",
    "sync.cancelled",
    "agent.started",
    "agent.progress",
    "agent.completed",
    "agent.failed",
]


class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, from_attributes=True, alias_generator=to_camel
    )


class MockConfig(DTO):
    balance: Money = "8.50"
    currency: Literal["USD", "CNY"] = "USD"
    usage_units: Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)] = 100
    resource_status: Literal["healthy", "degraded"] = "healthy"
    scenario: Scenario = "success"


class AddAccount(DTO):
    connector_id: Literal["mock"]
    external_account_id: Id
    display_name: Annotated[str, Field(min_length=1, max_length=100, strict=True)]

    @model_validator(mode="after")
    def normalize(self):
        self.display_name = self.display_name.strip()
        if not self.display_name:
            raise ValueError("empty display name")
        return self


class AccountOut(AddAccount):
    connector_id: Id
    id: Id
    status: Status
    credential_configured: bool
    last_sync_at: str | None
    next_sync_at: str | None
    error_code: str | None
    created_at: str
    mock: MockConfig

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "connected" and (not self.credential_configured or self.error_code):
            raise ValueError("inconsistent state")
        if self.status in {"disconnected", "connecting"} and self.credential_configured:
            raise ValueError("inconsistent state")
        return self


class DataOut(DTO):
    connection_id: Id
    provider_account_id: Id
    balance: Money
    currency: Literal["USD", "CNY"]
    usage_units: Annotated[int, Field(ge=0, le=1_000_000_000)]
    resource_status: Literal["healthy", "degraded"]
    revision: Annotated[int, Field(ge=1)]
    updated_at: str
    source: Literal["mock"] = "mock"


class SyncResult(DTO):
    data: DataOut | None
    next_cursor: Annotated[str, Field(min_length=1, max_length=512)]


class TaskOut(DTO):
    id: Id
    connection_id: Id
    status: TaskStatus
    trigger: Literal["manual", "scheduled"]
    attempts: int
    retry_count: int
    max_attempts: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    next_attempt_at: str
    error_code: str | None


class Analysis(DTO):
    event_id: Id
    severity: Literal["info", "warning"]
    summary: str
    evidence: list[str]
    recommendation: str
    mode: Literal["deterministic-mock"] = "deterministic-mock"


class AnalysisInput(DTO):
    id: Id
    balance: Money
    currency: Literal["USD", "CNY"]


class EventPayload(DTO):
    error_code: str | None = None
    attempt: int | None = None
    next_attempt_at: str | None = None
    changed_fields: list[Literal["balance", "currency", "usageUnits", "resourceStatus"]] | None = (
        None
    )
    previous: DataOut | None = None
    current: DataOut | None = None
    run_id: Id | None = None
    stage: str | None = None
    progress: int | None = None
    result: Analysis | None = None


class EventOut(DTO):
    event_id: Id
    sequence: int
    event_type: EventType
    source_id: Id
    account_id: Id
    occurred_at: str
    payload: EventPayload
    correlation_id: Id
    analysis: Analysis | None

    @field_serializer("payload")
    def serialize_payload(self, value: EventPayload):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)


class ConnectorOut(DTO):
    id: Id
    name: str
    capabilities: list[str]
    permissions: list[str]
    last_sync_at: str | None = None


class AppSettings(DTO):
    auto_sync: StrictBool = False
    sync_interval_seconds: Annotated[int, Field(strict=True, ge=10, le=86400)] = 300
    start_minimized: StrictBool = False  # 保留迁移兼容；原生窗口设置由客户端单独保存。


class SecurityOut(DTO):
    available: bool
    backend: str
    data_location: str


class LegacyAccount(DTO):
    id: str
    connector_id: str
    provider_account_id: str
    name: str
    balance: Money
    currency: Literal["USD", "CNY"]
    updated_at: str
    source: Literal["mock"]


class LegacyEvent(DTO):
    id: str
    connector_id: str
    account_id: str
    provider_event_id: str
    kind: Literal["balance.observed"]
    balance: Money
    currency: Literal["USD", "CNY"]
    occurred_at: str
    source: Literal["mock"]
    analysis: Analysis | None


class LegacyTask(DTO):
    id: str
    connector_id: str
    status: Literal["running", "succeeded", "failed", "interrupted"]
    started_at: str
    finished_at: str | None
    error_code: str | None


class Snapshot(DTO):
    connectors: list[ConnectorOut]
    accounts: list[LegacyAccount]
    events: list[LegacyEvent]
    tasks: list[LegacyTask]
    connections: list[AccountOut]
    data: list[DataOut]
    jobs: list[TaskOut]
    business_events: list[EventOut]
    settings: AppSettings
    security: SecurityOut


class AccountPage(DTO):
    items: list[AccountOut]
    total: int
    limit: int
    offset: int


class TaskPage(DTO):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int


class EventPage(DTO):
    items: list[EventOut]
    next_cursor: int | None


class AnalyzeRequest(DTO):
    event_id: Id


class RunOut(DTO):
    id: Id
    event_id: Id
    status: TaskStatus
    progress: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_code: str | None
    result: Analysis | None


class RunPage(DTO):
    items: list[RunOut]
    total: int
    limit: int
    offset: int


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
