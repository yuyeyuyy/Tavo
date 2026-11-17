"""Bounded model-request policy and diagnostics containing no message data."""

import math
import os
from datetime import UTC
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError

DEEPSEEK_THINKING_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro"})


class ModelRetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    max_retries: int = Field(default=3, strict=True, ge=0, le=3)
    max_output_tokens: int = Field(default=16384, strict=True, ge=128, le=16384)
    base_delay_seconds: float = Field(default=0.25, ge=0, le=5)
    max_delay_seconds: float = Field(default=5, ge=0, le=10)
    timeout_seconds: float = Field(default=60, gt=0, le=60)

    @classmethod
    def from_env(cls):
        try:
            return cls(
                max_retries=int(os.environ.get("TAVO_MODEL_MAX_RETRIES", "3")),
                max_output_tokens=int(
                    os.environ.get("TAVO_MODEL_RETRY_MAX_OUTPUT_TOKENS", "16384")
                ),
                base_delay_seconds=float(os.environ.get("TAVO_MODEL_RETRY_DELAY_SECONDS", "0.25")),
                max_delay_seconds=float(os.environ.get("TAVO_MODEL_RETRY_MAX_DELAY_SECONDS", "5")),
            )
        except ValueError:
            raise ValueError("Invalid model retry configuration") from None


class AttemptFailure(AppError):
    def __init__(self, code, *, retryable=False, wait_hint=None):
        super().__init__(code)
        self.retryable, self.wait_hint = retryable, wait_hint


def thinking_model(profile):
    return profile.provider == "deepseek" and profile.model in DEEPSEEK_THINKING_MODELS


def output_budget(profile, override):
    if override is None:
        return profile.max_output_tokens
    if type(override) is not int or not 128 <= override <= 16384:
        raise ValueError("Invalid model output budget")
    if override > profile.max_output_tokens and not thinking_model(profile):
        raise ValueError("Unsupported model output budget increase")
    return override


def next_output_budget(profile, current, policy):
    if not thinking_model(profile):
        return None
    enlarged = min(current * 2, policy.max_output_tokens)
    return enlarged if enlarged > current else None


def retry_after(value, now):
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            seconds = (
                date.replace(tzinfo=UTC).timestamp() - now
                if date.tzinfo is None
                else date.timestamp() - now
            )
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def contains_secret(value, key):
    if isinstance(value, str):
        return key in value
    if isinstance(value, dict):
        return any(contains_secret(k, key) or contains_secret(v, key) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_secret(v, key) for v in value)
    return False


def quota_exhausted(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("error"), dict):
        return False
    error = raw["error"]
    codes = {"insufficient_quota", "quota_exceeded", "billing_hard_limit_reached"}
    return any(
        isinstance(error.get(field), str) and error[field] in codes for field in ("code", "type")
    )


def response_metadata(raw, protocol):
    """Whitelist scalars only. Never return strings taken from response text/IDs."""
    if not isinstance(raw, dict):
        return {}
    reason, content, reasoning = None, False, False
    usage = raw.get("usage")
    if protocol == "completions":
        choices = raw.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        reason = choice.get("finish_reason")
        message = choice.get("message")
        if isinstance(message, dict):
            content = bool(message.get("content"))
            reasoning = bool(
                message.get("reasoning_content")
                or message.get("reasoning_details")
                or message.get("reasoning")
            )
    elif protocol == "responses":
        reason = raw.get("status")
        for item in raw.get("output", []) if isinstance(raw.get("output"), list) else []:
            if isinstance(item, dict):
                reasoning |= item.get("type") == "reasoning"
                if item.get("type") == "message" and isinstance(item.get("content"), list):
                    content |= any(
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                        and bool(part.get("text"))
                        for part in item["content"]
                    )
    elif protocol == "messages":
        reason = raw.get("stop_reason")
        for item in raw.get("content", []) if isinstance(raw.get("content"), list) else []:
            if isinstance(item, dict):
                content |= item.get("type") == "text" and bool(item.get("text"))
                reasoning |= item.get("type") in ("thinking", "redacted_thinking")
    elif protocol == "gemini":
        candidates = raw.get("candidates")
        candidate = (
            candidates[0]
            if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict)
            else {}
        )
        reason = candidate.get("finishReason")
        parts = candidate.get("content")
        if isinstance(parts, dict) and isinstance(parts.get("parts"), list):
            for part in parts["parts"]:
                if isinstance(part, dict):
                    content |= bool(part.get("text")) and not part.get("thought", False)
                    reasoning |= bool(part.get("thought"))
        usage = raw.get("usageMetadata")
    allowed = {
        "stop",
        "length",
        "tool_calls",
        "content_filter",
        "insufficient_system_resource",
        "aborted",
        "completed",
        "incomplete",
        "failed",
        "end_turn",
        "stop_sequence",
        "max_tokens",
        "tool_use",
        "STOP",
        "MAX_TOKENS",
        "SAFETY",
        "refusal",
    }
    result = {
        "finish_reason": reason if isinstance(reason, str) and reason in allowed else "other",
        "content_present": bool(content),
        "reasoning_present": bool(reasoning),
    }
    if isinstance(usage, dict):
        for source, target in (
            ("completion_tokens", "output_tokens"),
            ("output_tokens", "output_tokens"),
            ("candidatesTokenCount", "output_tokens"),
        ):
            value = usage.get(source)
            if type(value) is int and 0 <= value <= 2_000_000:
                result[target] = value
        detail = usage.get("completion_tokens_details")
        value = detail.get("reasoning_tokens") if isinstance(detail, dict) else None
        if type(value) is int and 0 <= value <= 2_000_000:
            result["reasoning_tokens"] = value
    return result
