import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.api.base import DTO

Provider = Literal[
    "openai", "anthropic", "gemini", "deepseek", "qwen", "kimi", "zhipu", "openrouter", "xai"
]
ProfileId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
ModelId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:/-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=16000)]


class ChatMessage(DTO):
    role: Literal["user", "assistant"]
    content: Text

    @model_validator(mode="after")
    def nonblank(self):
        if not self.content.strip():
            raise ValueError("blank message")
        if self.role == "user" and len(self.content) > 4000:
            raise ValueError("user message too long")
        return self


class ChatRequest(DTO):
    profile_id: ProfileId
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def bounded_turns(self):
        if self.messages[-1].role != "user" or self.messages[0].role != "user":
            raise ValueError("user boundary required")
        if any(a.role == b.role for a, b in zip(self.messages, self.messages[1:])):
            raise ValueError("alternating turns required")
        if len(json.dumps(self.model_dump(by_alias=True), ensure_ascii=False).encode()) > 24576:
            raise ValueError("chat too large")
        return self


class TokenUsage(DTO):
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None


class AssistantMessage(DTO):
    role: Literal["assistant"] = "assistant"
    content: Text


class ChatReply(DTO):
    profile_id: ProfileId
    provider: Provider
    model: ModelId
    message: AssistantMessage
    finish_reason: Literal["stop", "length"]
    truncated: bool
    usage: TokenUsage | None = None


class ModelOption(DTO):
    id: ProfileId
    label: Annotated[str, Field(min_length=1, max_length=80)]
    provider: Provider
    model: ModelId
    ready: bool
    credential_revision: Annotated[int, Field(ge=0)] = 0


class ModelCatalog(DTO):
    items: Annotated[list[ModelOption], Field(max_length=32)]
    default_profile_id: ProfileId | None = None


class ProviderOption(DTO):
    id: Provider
    label: Annotated[str, Field(min_length=1, max_length=80)]
    model_hint: str = "填写服务商控制台中的文本模型 ID"


class ProviderList(DTO):
    items: Annotated[list[ProviderOption], Field(max_length=9)]


class ModelProfileCreate(DTO):
    provider: Provider
    model: ModelId
    label: Annotated[str, Field(min_length=1, max_length=80)] | None = None

    @model_validator(mode="after")
    def meaningful_label(self):
        if self.label is not None and not self.label.strip():
            raise ValueError("blank label")
        return self


class ModelProfileOut(ModelOption):
    enabled: bool


class ModelProfileList(DTO):
    items: Annotated[list[ModelProfileOut], Field(max_length=32)]
    default_profile_id: ProfileId | None = None
