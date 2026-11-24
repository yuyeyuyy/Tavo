import asyncio
import json
import logging
import time
from uuid import uuid4

import httpx

from app.core.errors import AppError, Code
from app.core.json_data import strict_loads
from app.llm.contracts import AssistantMessage, ChatReply, TokenUsage
from app.llm.prompts import CONVERSATION_STYLE
from app.llm.retry import (
    AttemptFailure,
    ModelRetryPolicy,
    contains_secret,
    next_output_budget,
    quota_exhausted,
    response_metadata,
    retry_after,
    thinking_model,
)
from app.llm.retry import (
    output_budget as checked_output_budget,
)

SYSTEM = (
    "你是 Tavo，一个帮助用户思考问题的助手。当前请求只生成文本，没有提供操作执行通道，不得声称已执行操作。\n"
    + CONVERSATION_STYLE
)
RESPONSE_BYTES = 262144


# 解析url
def request_for(profile, messages, key, *, output_budget=None):
    budget = checked_output_budget(profile, output_budget)
    rows = [m.model_dump() for m in messages]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": profile.model}
    if profile.protocol == "responses":
        path = "/responses"
        body.update(
            input=rows,
            instructions=SYSTEM,
            max_output_tokens=budget,
            store=False,
            stream=False,
        )
    elif profile.protocol == "messages":
        path = "/messages"
        headers["anthropic-version"] = "2026-10-21"
        body.update(messages=rows, system=SYSTEM, max_tokens=budget, stream=False)
    elif profile.protocol == "gemini":
        path = f"/models/{profile.model}:generateContent"
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content}],
                }
                for m in messages
            ],
            "generationConfig": {"maxOutputTokens": budget},
        }
    else:
        path = "/chat/completions"
        body.update(
            messages=[{"role": "system", "content": SYSTEM}, *rows],
            max_tokens=budget,
            stream=False,
        )
    if thinking_model(profile):
        body["thinking"] = {"type": "enabled"}
    return profile.endpoint + path, headers, body


def usage(value, input_name, output_name):
    if not isinstance(value, dict):
        return None
    result = TokenUsage(input_tokens=value.get(input_name), output_tokens=value.get(output_name))
    return result if result.input_tokens is not None or result.output_tokens is not None else None


# 结构化回复
def parse_reply(profile, raw):
    """Select final text only; never return reasoning, tool calls or raw upstream objects."""
    text, limited, tokens = [], False, None
    if profile.protocol == "responses":
        status = raw["status"]
        if status not in {"completed", "incomplete"}:
            raise AppError(Code.MODEL_REJECTED)
        limited = status == "incomplete"
        if limited and raw.get("incomplete_details", {}).get("reason") != "max_output_tokens":
            raise AppError(Code.MODEL_REJECTED)
        for item in raw["output"]:
            if item["type"] == "reasoning":
                continue
            if item["type"] != "message" or item.get("role") != "assistant":
                raise AppError(Code.MODEL_REJECTED)
            for part in item["content"]:
                if part["type"] != "output_text":
                    raise AppError(Code.MODEL_REJECTED)
                text.append(part["text"])
        tokens = usage(raw.get("usage"), "input_tokens", "output_tokens")
    elif profile.protocol == "messages":
        reason = raw["stop_reason"]
        if reason not in {"end_turn", "stop_sequence", "max_tokens"}:
            raise AppError(Code.MODEL_REJECTED)
        limited = reason == "max_tokens"
        for part in raw["content"]:
            if part["type"] in {"thinking", "redacted_thinking"}:
                continue
            if part["type"] != "text":
                raise AppError(Code.MODEL_REJECTED)
            text.append(part["text"])
        tokens = usage(raw.get("usage"), "input_tokens", "output_tokens")
    elif profile.protocol == "gemini":
        candidate = raw.get("candidates", [])[0]
        reason = candidate["finishReason"]
        if reason not in {"STOP", "MAX_TOKENS"}:
            raise AppError(Code.MODEL_REJECTED)
        limited = reason == "MAX_TOKENS"
        for part in candidate["content"]["parts"]:
            if part.get("thought") is True:
                continue
            if "text" not in part:
                raise AppError(Code.MODEL_REJECTED)
            text.append(part["text"])
        tokens = usage(raw.get("usageMetadata"), "promptTokenCount", "candidatesTokenCount")
    else:
        choice = raw["choices"][0]
        if choice["finish_reason"] not in {"stop", "length"}:
            raise AppError(Code.MODEL_REJECTED)
        limited = choice["finish_reason"] == "length"
        message = choice["message"]
        if (
            message.get("tool_calls")
            or message.get("refusal")
            or message.get("role") != "assistant"
        ):
            raise AppError(Code.MODEL_REJECTED)
        value = message.get("content")
        if value is not None and not isinstance(value, str):
            raise AppError(Code.MODEL_RESPONSE_INVALID)
        text = [value or ""]
        tokens = usage(raw.get("usage"), "prompt_tokens", "completion_tokens")
    content = "".join(text).strip()
    if not content:
        raise AppError(Code.MODEL_OUTPUT_LIMIT if limited else Code.MODEL_EMPTY_RESPONSE)
    truncated = limited or len(content) > 16000
    return ChatReply(
        profile_id=profile.id,
        provider=profile.provider,
        model=profile.model,
        message=AssistantMessage(content=content[:16000]),
        finish_reason="length" if truncated else "stop",
        truncated=truncated,
        usage=tokens,
    )


LOGGER = logging.getLogger("uvicorn.error.tavo.model")


# 模型网关
class ModelGateway:
    def __init__(
        self,
        transport=None,
        *,
        policy=None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
        wall_time=time.time,
    ):
        self.policy = policy or ModelRetryPolicy()
        self.sleep, self.clock, self.wall_time = sleep, clock, wall_time
        # 异步客户端
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    async def reply(self, profile, messages, credential):
        key = credential.get_secret_value()
        return await self._call(
            profile,
            key,
            lambda budget: request_for(profile, messages, key, output_budget=budget),
            lambda raw: parse_reply(profile, raw),
            "text",
        )

    # 执行一轮模型交互
    """
    credential 相当于apikey
    """

    async def turn(self, profile, history, specs, credential, *, system=None):
        from app.llm.turns import parse_turn, turn_request

        key = credential.get_secret_value()

        def build(budget):
            # 拼接http请求
            url, headers, _ = request_for(profile, [], key)
            # 配置，历史对话，specs，系统提示词
            body = turn_request(
                profile,
                history,
                specs,
                **({"system": system} if system else {}),
                output_budget=budget,
            )
            return url, headers, body

        # 进行一次模型call
        return await self._call(
            profile,
            key,
            build,
            lambda raw: parse_turn(profile, raw),
            "review" if system is not None else "assistant",
        )

    async def _read(self, response, maximum=RESPONSE_BYTES):
        data = bytearray()
        async for chunk in response.aiter_bytes(65536):
            data.extend(chunk)
            if len(data) > maximum:
                raise AppError(Code.MODEL_RESPONSE_INVALID)
        return data

    #
    async def _attempt(self, profile, key, build, parse, budget, event):
        # 解析出url, headers, body
        url, headers, body = build(budget)
        # 修改当前阶段状态，正在发送中
        event["stage"] = "transport"
        # post请求
        async with self.client.stream("POST", url, headers=headers, json=body) as response:
            # 获取响应状态
            status = response.status_code
            # 更新事件状态
            event.update(stage="read", http_status=status)
            # 处理响应状态。
            if status != 200:
                quota = False
                # 如果请求过多或者额度用光
                if status == 429:
                    # Only inspect bounded structured error codes, never echo provider messages.
                    try:
                        # 判断是否是额度问题
                        quota = quota_exhausted(strict_loads(await self._read(response, 8192)))
                    except (AppError, ValueError):
                        pass
                code = (
                    Code.MODEL_AUTH_FAILED
                    if status in {401, 403}
                    else Code.MODEL_RATE_LIMITED
                    if status in {402, 429}
                    else Code.MODEL_REQUEST_INVALID
                    if status in {400, 413, 422}
                    else Code.MODEL_TIMEOUT
                    if status == 408
                    else Code.MODEL_UNAVAILABLE
                    if status == 404 or status >= 500
                    else Code.MODEL_RESPONSE_INVALID
                )
                raise AttemptFailure(
                    code,
                    retryable=(
                        status in {408, 500, 502, 503, 504} or (status == 429 and not quota)
                    ),
                    wait_hint=retry_after(response.headers.get("retry-after"), self.wall_time()),
                )
            # 返回格式不对
            if "application/json" not in response.headers.get("content-type", "").lower():
                raise AppError(Code.MODEL_RESPONSE_INVALID)
            # 结构化解析结构
            raw = strict_loads(await self._read(response))
            # 防止泄露厂商返回key导致泄露
            if contains_secret(raw, key):
                raise AppError(Code.MODEL_RESPONSE_INVALID)
            # 更新状态
            event.update(stage="parse", **response_metadata(raw, profile.protocol))
            # 针对的deepseek的情况
            # TODO 不应该放在这里，放在模型适配层比较合适
            if profile.provider == "deepseek" and event.get("finish_reason") in {
                "insufficient_system_resource",
                "aborted",
            }:
                raise AttemptFailure(Code.MODEL_UNAVAILABLE, retryable=True)
            # 返回解析后的结果。
            return parse(raw)

    def _record(self, event, started, **fields):
        metadata = {**event, **fields, "duration_ms": max(0, int((self.clock() - started) * 1000))}
        # All fields are constructed here or come from response_metadata's scalar whitelist.
        LOGGER.info("model_request %s", json.dumps(metadata, separators=(",", ":")))

    """
    配置文件，key，构建http请求，解析模型返回，类型（review/系统提示词/助理）
    """

    async def _call(self, profile, key, build, parse, kind):
        # 这次模型调用的最后
        deadline = self.clock() + self.policy.timeout_seconds
        # 上下文窗口
        budget = profile.max_output_tokens
        # 标签
        call_tag = uuid4().hex[:16]  # Independent of sessions, users and provider IDs.
        event = {}
        # 开始时间
        started = self.clock()
        try:
            # 最大重试数
            for attempt in range(1, self.policy.max_retries + 2):
                started = self.clock()
                # 记录当前的情况
                event = {
                    "call": call_tag,
                    "kind": kind,
                    "provider": profile.provider,
                    "protocol": profile.protocol,
                    "attempt": attempt,
                    "output_budget": budget,
                    "stage": "construct",
                }
                # 还剩多长时间
                remaining = deadline - started
                # 如果到期直接返回错误
                # TODO 不该直接返回错误，而是整理整理回复用户
                if remaining <= 0:
                    self._record(
                        event, started, outcome="deadline", error_code=Code.MODEL_TIMEOUT.value
                    )
                    raise AppError(Code.MODEL_TIMEOUT)
                try:
                    # 在这个时间内
                    async with asyncio.timeout(remaining):
                        # 发送消息
                        result = await self._attempt(profile, key, build, parse, budget, event)
                    # 更新记录，成功返回
                    self._record(event, started, outcome="success")
                    return result
                except AttemptFailure as error:
                    failure = error
                except AppError as error:
                    failure = AttemptFailure(
                        error.code,
                        retryable=error.code
                        in {Code.MODEL_OUTPUT_LIMIT, Code.MODEL_EMPTY_RESPONSE},
                    )
                except (httpx.TimeoutException, TimeoutError):
                    failure = AttemptFailure(Code.MODEL_TIMEOUT, retryable=True)
                except httpx.RequestError as error:
                    failure = AttemptFailure(
                        Code.MODEL_UNAVAILABLE,
                        retryable=not isinstance(
                            error, (httpx.LocalProtocolError, httpx.UnsupportedProtocol)
                        ),
                    )
                except Exception:
                    failure = AttemptFailure(
                        Code.MODEL_UNAVAILABLE
                        if kind == "text" and event["stage"] == "transport"
                        else Code.MODEL_RESPONSE_INVALID
                    )
                # 重试
                can_retry = failure.retryable and attempt <= self.policy.max_retries
                next_budget = budget
                if failure.code == Code.MODEL_OUTPUT_LIMIT:
                    next_budget = next_output_budget(profile, budget, self.policy)
                    can_retry &= next_budget is not None
                delay = min(
                    self.policy.base_delay_seconds * 2 ** (attempt - 1),
                    self.policy.max_delay_seconds,
                )
                if failure.wait_hint is not None:
                    if failure.wait_hint > self.policy.max_delay_seconds:
                        can_retry = False
                    delay = max(delay, failure.wait_hint)
                can_retry &= delay < deadline - self.clock()
                self._record(
                    event,
                    started,
                    outcome="retry" if can_retry else "failed",
                    error_code=failure.code.value,
                    retry_delay_ms=int(delay * 1000) if can_retry else 0,
                )
                if not can_retry:
                    raise AppError(failure.code) from None
                event["stage"] = "retry_wait"
                await self.sleep(delay)
                budget = next_budget
        except asyncio.CancelledError:
            self._record(event, started, outcome="cancelled")
            raise

    async def close(self):
        await self.client.aclose()
