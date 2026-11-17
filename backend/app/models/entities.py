from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Secret(Base):
    __tablename__ = "secrets"
    reference: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str]


class CredentialState(Base):
    __tablename__ = "credential_states"
    __table_args__ = (CheckConstraint("revision >= 1", name="credential_revision_positive"),)
    reference: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int]
    present: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[str]


class ModelProfileRecord(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        CheckConstraint("max_output_tokens BETWEEN 128 AND 8192", name="model_output_budget"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str]
    model: Mapped[str]
    label: Mapped[str]
    enabled: Mapped[bool]
    base_url: Mapped[str | None]
    max_output_tokens: Mapped[int]
    position: Mapped[int]
    created_at: Mapped[str]


class ModelCatalogState(Base):
    __tablename__ = "model_catalog_state"
    __table_args__ = (CheckConstraint("id = 1", name="model_catalog_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    default_profile_id: Mapped[str | None] = mapped_column(ForeignKey("model_profiles.id"))


class WorkerLease(Base):
    __tablename__ = "worker_lease"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str]
    fence: Mapped[int]
    expires_at: Mapped[str]


class ClientSession(Base):
    __tablename__ = "client_sessions"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[str]
    owner_fingerprint: Mapped[str]


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    execution_scope: Mapped[str] = mapped_column(
        default="sandbox_only", server_default="sandbox_only"
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str]
    profile_id: Mapped[str]
    review_mode: Mapped[str]
    latest_run_id: Mapped[str | None]
    bash_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    workspace_unconfirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int]
    next_sequence: Mapped[int]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        UniqueConstraint("session_id", "client_message_id"),
        CheckConstraint("sequence >= 1", name="message_positive_sequence"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"))
    client_message_id: Mapped[str | None]
    sequence: Mapped[int]
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[str]


class AssistantRun(Base):
    __tablename__ = "assistant_runs"
    __table_args__ = (
        Index(
            "assistant_active_session",
            "session_id",
            unique=True,
            sqlite_where=text("status IN ('pending','running','waiting_review')"),
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"))
    user_message_id: Mapped[str]
    previous_run_id: Mapped[str | None]
    status: Mapped[str]
    step: Mapped[str]
    rounds: Mapped[int]
    tool_count: Mapped[int]
    active_ms: Mapped[int]
    revision: Mapped[int]
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    worker_owner: Mapped[str | None]
    worker_fence: Mapped[int | None]
    created_at: Mapped[str]
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]
    error_code: Mapped[str | None]


class AssistantToolCall(Base):
    __tablename__ = "assistant_tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "round", "ordinal"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"))
    round: Mapped[int]
    ordinal: Mapped[int]
    provider_call_id: Mapped[str]
    name: Mapped[str]
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    argument_digest: Mapped[str]
    status: Mapped[str]
    review_revision: Mapped[int]
    expires_at: Mapped[str | None]
    attempts: Mapped[int]
    created_at: Mapped[str]


class AssistantToolResult(Base):
    __tablename__ = "assistant_tool_results"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("assistant_tool_calls.id"), unique=True)
    status: Mapped[str]
    output: Mapped[dict[str, Any]] = mapped_column(JSON)
    error_code: Mapped[str | None]
    created_at: Mapped[str]


class AssistantReview(Base):
    __tablename__ = "assistant_reviews"
    __table_args__ = (UniqueConstraint("tool_call_id", "revision"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("assistant_tool_calls.id"))
    revision: Mapped[int]
    action: Mapped[str]
    source: Mapped[str]
    reason_code: Mapped[str]
    policy_version: Mapped[str]
    summary: Mapped[str | None]
    argument_digest: Mapped[str]
    created_at: Mapped[str]


class AssistantModelTurn(Base):
    __tablename__ = "assistant_model_turns"
    purpose: Mapped[str] = mapped_column(default="assistant")
    __table_args__ = (
        UniqueConstraint("run_id", "purpose", "round", name="model_turn_purpose_round"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"))
    round: Mapped[int]
    status: Mapped[str]
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_id: Mapped[str | None]
    created_at: Mapped[str]
    finished_at: Mapped[str | None]


class BashExecution(Base):
    __tablename__ = "bash_executions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("assistant_tool_calls.id"), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"))
    container_name: Mapped[str] = mapped_column(unique=True)
    container_id: Mapped[str | None]
    state: Mapped[str]
    worker_owner: Mapped[str]
    worker_fence: Mapped[int]
    created_at: Mapped[str]
    finished_at: Mapped[str | None]
    error_code: Mapped[str | None]


class HostExecution(Base):
    __tablename__ = "host_executions"
    __table_args__ = (
        Index(
            "host_single_live_slot", "slot", unique=True, sqlite_where=text("state != 'finished'")
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("assistant_tool_calls.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"))
    slot: Mapped[int] = mapped_column(default=1)
    state: Mapped[str]
    worker_owner: Mapped[str]
    worker_fence: Mapped[int]
    pid: Mapped[int | None]
    created_at: Mapped[str]
    finished_at: Mapped[str | None]
    error_code: Mapped[str | None]


class SandboxInstance(Base):
    __tablename__ = "sandbox_instances"
    __table_args__ = (
        Index(
            "sandbox_single_live_slot", "slot", unique=True, sqlite_where=text("state != 'closed'")
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    slot: Mapped[int] = mapped_column(default=1)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_sessions.id"))
    container_name: Mapped[str] = mapped_column(unique=True)
    work_volume: Mapped[str | None]
    socket_volume: Mapped[str | None]
    gateway_name: Mapped[str | None]
    state: Mapped[str]
    worker_owner: Mapped[str]
    worker_fence: Mapped[int]
    baseline_revision: Mapped[str | None]
    resource_profile: Mapped[str]
    created_at: Mapped[str]
    last_used_at: Mapped[str]
    error_code: Mapped[str | None]
    network_count: Mapped[int] = mapped_column(default=0)
    network_reservation: Mapped[int] = mapped_column(default=0)
    network_window: Mapped[list] = mapped_column(JSON, default=list)


class SandboxOperation(Base):
    __tablename__ = "sandbox_operations"
    tool_call_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_tool_calls.id"), primary_key=True
    )
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandbox_instances.id"))
    kind: Mapped[str]
    state: Mapped[str]
    argument_digest: Mapped[str]
    approval_digest: Mapped[str | None]
    approval_detail: Mapped[dict | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[str]
    finished_at: Mapped[str | None]


class SandboxNetworkRequest(Base):
    __tablename__ = "sandbox_network_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"))
    parent_call_id: Mapped[str] = mapped_column(ForeignKey("assistant_tool_calls.id"))
    tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_tool_calls.id"), unique=True
    )
    target: Mapped[str]
    method: Mapped[str]
    request_digest: Mapped[str]
    state: Mapped[str]
    key_id: Mapped[str]
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    result_key_id: Mapped[str | None]
    result_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[str]
    error_code: Mapped[str | None]


class WorkspaceCheckpoint(Base):
    __tablename__ = "sandbox_workspace_checkpoints"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id"), primary_key=True
    )
    revision: Mapped[str | None]
    retained_revisions: Mapped[list] = mapped_column(JSON, default=list)
