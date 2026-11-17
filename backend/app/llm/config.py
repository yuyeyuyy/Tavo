import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, model_validator

from app.api.base import DTO
from app.core.errors import AppError, Code
from app.llm.contracts import ModelId, ProfileId, Provider

# Protocol and endpoint are server-owned. No client-provided headers or URLs.
PRESETS = {
    "openai": ("responses", "https://api.openai.com/v1"),
    "anthropic": ("messages", "https://api.anthropic.com/v1"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com/v1beta"),
    "deepseek": ("completions", "https://api.deepseek.com"),
    "qwen": ("completions", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "kimi": ("completions", "https://api.moonshot.cn/v1"),
    "zhipu": ("completions", "https://open.bigmodel.cn/api/paas/v4"),
    "openrouter": ("completions", "https://openrouter.ai/api/v1"),
    "xai": ("responses", "https://api.x.ai/v1"),
}

PROVIDER_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "kimi": "Kimi",
    "zhipu": "智谱 GLM",
    "openrouter": "OpenRouter",
    "xai": "Grok / xAI",
}


class ModelProfile(DTO):
    model_config = {**DTO.model_config, "frozen": True}
    id: ProfileId
    provider: Provider
    model: ModelId
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    base_url: str | None = Field(default=None, max_length=512)
    max_output_tokens: int = Field(default=2048, strict=True, ge=128, le=8192)

    @property
    def endpoint(self):
        return (self.base_url or PRESETS[self.provider][1]).rstrip("/")

    @property
    def protocol(self):
        return PRESETS[self.provider][0]

    @model_validator(mode="after")
    def validate_endpoint(self):
        url = urlsplit(self.endpoint)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or "\\" in self.endpoint
            or any(part in {".", ".."} for part in url.path.split("/"))
            or not (
                url.scheme == "https"
                or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})
            )
        ):
            raise ValueError("invalid model endpoint")
        if self.provider == "gemini" and ("/" in self.model or ":" in self.model):
            raise ValueError("Gemini requires a model name without path")
        if self.id == "mock":
            raise ValueError("reserved profile")
        if self.enabled and self.model == "SET_MODEL_ID":
            raise ValueError("configure a model ID before enabling the profile")
        return self


class ModelConfig(DTO):
    profiles: list[ModelProfile] = Field(default_factory=list, max_length=32)
    default_profile_id: ProfileId | None = None

    @model_validator(mode="after")
    def unique_profiles(self):
        ids = [p.id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate profile")
        if self.default_profile_id and not any(
            p.id == self.default_profile_id and p.enabled for p in self.profiles
        ):
            raise ValueError("invalid default profile")
        return self

    def profile(self, identity, *, enabled=True):
        for profile in self.profiles:
            if profile.id == identity and (profile.enabled or not enabled):
                return profile
        raise AppError(Code.MODEL_NOT_CONFIGURED)

    def snapshot(self):
        # Allows a pure configuration to be injected into a ChatService test.
        return self


def load_models(path: Path):
    try:
        if not path.exists():
            return ModelConfig()
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("invalid file")
        return ModelConfig.model_validate(json.loads(path.read_bytes()))
    except (OSError, ValueError, ValidationError):
        raise AppError(Code.MODEL_CONFIG_INVALID) from None
