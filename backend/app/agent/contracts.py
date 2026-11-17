from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from app.api.base import DTO, Id
from app.llm.contracts import ProfileId, TokenUsage

ReviewMode = Literal[
    "full_access",
    "auto_review",
    "request_approval",
    "auto_readonly",
    "ask",
    "disabled",
    "auto_sandbox",
]
COMMAND_PERMISSION_MODES = frozenset({"full_access", "auto_review", "request_approval"})
RunStatus = Literal[
    "pending", "running", "waiting_review", "succeeded", "failed", "cancelled", "interrupted"
]
CallStatus = Literal[
    "proposed",
    "waiting_review",
    "approved",
    "running",
    "succeeded",
    "failed",
    "denied",
    "cancelled",
]
Revision = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class SessionCreate(DTO):
    profile_id: ProfileId = "mock"
    review_mode: ReviewMode = "full_access"
    bash_enabled: Annotated[bool, Field(strict=True)] = True

    @model_validator(mode="after")
    def capabilities(self):
        if self.review_mode == "disabled" and "bash_enabled" not in self.model_fields_set:
            self.bash_enabled = False
        if self.bash_enabled and self.review_mode == "disabled":
            raise ValueError("Bash requires tools")
        if self.review_mode == "auto_sandbox" and not self.bash_enabled:
            raise ValueError("Explicit Bash capability required")
        return self


class Session(DTO):
    id: Id
    title: Annotated[str, Field(min_length=1, max_length=80)]
    profile_id: ProfileId
    review_mode: ReviewMode
    bash_enabled: bool = False
    execution_scope: Literal["sandbox_only", "host_and_sandbox"] = "sandbox_only"
    revision: int
    created_at: str
    updated_at: str
    active_run_id: str | None = None
    latest_run_id: str | None = None


class SessionPage(DTO):
    items: list[Session]
    next_cursor: str | None


class UserMessageWrite(DTO):
    client_message_id: Id
    content: Text
    expected_revision: Revision

    @model_validator(mode="after")
    def nonblank(self):
        self.content.encode("utf-8")
        if not self.content.strip():
            raise ValueError("blank message")
        return self


class DecisionWrite(DTO):
    action: Literal["approve", "reject"]
    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class Decision(DTO):
    id: Id
    tool_call_id: Id
    revision: int
    action: Literal["allow", "deny", "ask"]
    source: Literal["policy", "human", "model", "mock"]
    reason_code: str
    summary: Annotated[str, Field(max_length=240)] | None = None
    policy_version: str = "1"
    created_at: str


class ToolCall(DTO):
    id: Id
    run_id: Id
    round: int
    name: Annotated[str, Field(min_length=1, max_length=128)]
    arguments: dict[str, JsonValue] | None
    status: CallStatus
    review_revision: int
    expires_at: str | None
    attempts: int
    decisions: list[Decision] = Field(default_factory=list)


class ToolResult(DTO):
    id: Id
    tool_call_id: Id
    status: Literal["succeeded", "failed", "denied", "cancelled"]
    output: dict[str, JsonValue]
    error_code: str | None
    created_at: str


class MessageBase(DTO):
    id: Id
    session_id: Id
    run_id: Id | None
    sequence: int
    created_at: str


class UserMessage(MessageBase):
    kind: Literal["user"] = "user"
    content: Text


class AssistantMessage(MessageBase):
    kind: Literal["assistant"] = "assistant"
    content: Annotated[str, Field(min_length=1, max_length=16000)]
    final: bool
    truncated: bool = False
    usage: TokenUsage | None = None


class ToolCallMessage(MessageBase):
    kind: Literal["tool_call"] = "tool_call"
    call: ToolCall


class ToolResultMessage(MessageBase):
    kind: Literal["tool_result"] = "tool_result"
    result: ToolResult


Message = Annotated[
    UserMessage | AssistantMessage | ToolCallMessage | ToolResultMessage,
    Field(discriminator="kind"),
]


class MessagePage(DTO):
    items: list[Message]
    has_more: bool
    next_sequence: int | None


class Context(DTO):
    schema_version: Literal[1] = 1
    bash_enabled: bool = False
    policy_version: Literal["1", "2", "3", "4", "5"] = "2"
    sandbox_policy: dict | None = None
    host_policy: dict | None = None
    run_id: Id
    user_message_id: Id
    user_text: Text
    selected_run_ids: list[Id]
    from_sequence: int
    to_sequence: int
    dropped_turns: int
    context_bytes: int
    tool_names: list[str]
    review_mode: ReviewMode
    captured_at: str


class ContextSummary(DTO):
    policy_version: str
    from_sequence: int
    to_sequence: int
    dropped_turns: int
    context_bytes: int
    tool_names: list[str]


class Run(DTO):
    id: Id
    session_id: Id
    user_message_id: Id
    previous_run_id: Id | None
    status: RunStatus
    step: str
    rounds: int
    tool_count: int
    revision: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_code: str | None
    context: ContextSummary | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class MessageAccepted(DTO):
    message: UserMessage
    run: Run
    session: Session


class ToolSpec(DTO):
    name: str
    description: str
    version: Literal[1] = 1
    read_only: bool = True
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class ToolList(DTO):
    items: list[ToolSpec]


class NetworkTargetStatus(DTO):
    id: str
    hostname: str
    capability: str
    methods: list[str]
    paths: list[str]
    credential_required: bool
    credential_revision: int
    ready: bool


class NetworkTargetList(DTO):
    mode: Literal["none", "proxy"]
    items: list[NetworkTargetStatus]


class ApprovalPreview(DTO):
    kind: Literal["network", "workspace_commit"]
    description: str
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    details: dict[str, JsonValue]


@dataclass(frozen=True)
class RunScope:
    # Scope comes from the Worker lease, never from client/model arguments.
    run_id: str
    session_id: str
    owner: str
    fence: int


class PoolStatus(DTO):
    capacity: int
    active_runs: int
    occupied_slots: int
    cleanup_slots: int


class BashStatus(DTO):
    enabled: bool
    available: bool
    reason_code: str | None
    workspace_policy: Literal["session-volume"] = "session-volume"
    external_network: bool = False
    network_policy: Literal["none", "proxy"] = "none"


class HostBashStatus(DTO):
    enabled: bool
    available: bool
    reason_code: str | None = None
    execution_target: Literal["backend-host"] = "backend-host"


class AgentRuntime(DTO):
    pool: PoolStatus
    bash: BashStatus
    host_bash: HostBashStatus
