import json
from pathlib import Path

from sqlalchemy import func, select

from app.agent.contracts import COMMAND_PERMISSION_MODES, Context
from app.agent.tools.builtin.web import WEB_NAMES
from app.core.errors import AppError, Code
from app.core.private_state import PrivateStateStore
from app.llm.config import ModelProfile
from app.llm.turns import AssistantTurn, append_native, turn_request, user_native
from app.models.entities import (
    AssistantModelTurn,
    AssistantRun,
    AssistantToolCall,
    AssistantToolResult,
    ConversationMessage,
    ConversationSession,
)
from app.repositories.sessions import require_scope

CONTEXT_BYTES = 65536


def json_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def turn_binding(run, round_number):
    return {
        "runId": run.id,
        "sessionId": run.session_id,
        "round": round_number,
        "profile": run.profile,
    }


# 构建上下文
class ContextBuilder:
    def __init__(self, db, registry, secrets, now, budget=CONTEXT_BYTES):
        self.db, self.registry, self.now, self.budget = db, registry, now, budget
        self.private = PrivateStateStore(secrets)

    def specs(self, mode, legacy_bash_enabled=True, execution_scope="sandbox_only"):
        return [
            s
            for s in self.registry.specs(execution_scope)
            if mode != "disabled"
            and (s.read_only or s.name == "host_bash" or legacy_bash_enabled)
            and (s.name not in WEB_NAMES or mode in COMMAND_PERMISSION_MODES)
        ]

    def frozen_specs(self, context):
        scope = "host_and_sandbox" if context.policy_version == "5" else "sandbox_only"
        allowed = set(context.tool_names)
        return [
            spec
            for spec in self.specs(context.review_mode, context.bash_enabled, scope)
            if spec.name in allowed
        ]

    def history_in(self, db, run_ids, protocol):
        history = []
        for run_id in run_ids:
            run = db.get(AssistantRun, run_id)
            user = db.get(ConversationMessage, run.user_message_id)
            history.append(user_native(protocol, user.payload["content"]))
            for row in db.scalars(
                select(AssistantModelTurn)
                .where(
                    AssistantModelTurn.run_id == run.id,
                    AssistantModelTurn.status == "completed",
                    AssistantModelTurn.purpose == "assistant",
                )
                .order_by(AssistantModelTurn.round)
            ):
                turn = AssistantTurn.model_validate(
                    self.private.open(turn_binding(run, row.round), row.ciphertext)
                )
                calls = list(
                    db.scalars(
                        select(AssistantToolCall)
                        .where(
                            AssistantToolCall.run_id == run.id, AssistantToolCall.round == row.round
                        )
                        .order_by(AssistantToolCall.ordinal)
                    )
                )
                results = []
                internal = []
                for call in calls:
                    result = db.scalar(
                        select(AssistantToolResult).where(
                            AssistantToolResult.tool_call_id == call.id
                        )
                    )
                    if result is None:
                        raise AppError(Code.INVALID_STATE)
                    payload = {
                        "status": result.status,
                        "output": result.output,
                        "errorCode": result.error_code,
                    }
                    if call.provider_call_id.startswith("internal-"):
                        internal.append((call, payload))
                    else:
                        results.append(payload)
                append_native(history, protocol, turn, results)
                for call, payload in internal:
                    arguments = json.dumps(call.arguments, ensure_ascii=False)
                    if protocol == "responses":
                        native = [
                            {
                                "type": "function_call",
                                "call_id": call.provider_call_id,
                                "name": call.name,
                                "arguments": arguments,
                            }
                        ]
                    elif protocol == "messages":
                        native = {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": call.provider_call_id,
                                    "name": call.name,
                                    "input": call.arguments,
                                }
                            ],
                        }
                    elif protocol == "gemini":
                        native = {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "id": call.provider_call_id,
                                        "name": call.name,
                                        "args": call.arguments,
                                    }
                                }
                            ],
                        }
                    else:
                        native = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": call.provider_call_id,
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": arguments},
                                }
                            ],
                        }
                    from app.llm.turns import ProposedCall

                    generated = AssistantTurn(
                        native=native,
                        calls=[
                            ProposedCall(
                                provider_id=call.provider_call_id,
                                name=call.name,
                                arguments=call.arguments,
                                argument_source=arguments,
                            )
                        ],
                    )
                    append_native(history, protocol, generated, [payload])
        return history

    # 用执行凭证验证身份并定位到这一单，组装出本轮需要的数据
    def prepare(self, scope):
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            session = db.get(ConversationSession, scope.session_id)
            modern = session.execution_scope == "host_and_sandbox"
            profile = ModelProfile.model_validate(run.profile) if run.profile else None
            protocol = profile.protocol if profile else "completions"
            user = db.get(ConversationMessage, run.user_message_id)
            if not run.context:
                specs = self.specs(
                    session.review_mode, session.bash_enabled, session.execution_scope
                )
                names = [spec.name for spec in specs]
                has_sandbox = "sandbox" in names
                use_policy = modern or has_sandbox or bool(WEB_NAMES.intersection(names))
                query = (
                    select(AssistantRun)
                    .join(
                        ConversationMessage, AssistantRun.user_message_id == ConversationMessage.id
                    )
                    .where(
                        AssistantRun.session_id == scope.session_id,
                        AssistantRun.status == "succeeded",
                        ConversationMessage.sequence < user.sequence,
                    )
                )
                count = db.scalar(select(func.count()).select_from(query.subquery()))
                previous = list(
                    db.scalars(query.order_by(ConversationMessage.sequence.desc()).limit(20))
                )
                previous.reverse()
                selected = [r.id for r in previous]
                dropped = count - len(selected)
                while True:
                    history = self.history_in(db, selected + [run.id], protocol)
                    size = (
                        json_size(turn_request(profile, history, specs))
                        if profile
                        else json_size(
                            {
                                "history": history,
                                "tools": [spec.model_dump() for spec in specs],
                            }
                        )
                    )
                    budget = self.registry.policy.context_bytes if use_policy else self.budget
                    if size <= budget:
                        break
                    if not selected:
                        raise AppError(Code.CONTEXT_TOO_LARGE)
                    selected.pop(0)
                    dropped += 1
                first = (
                    db.get(
                        ConversationMessage, db.get(AssistantRun, selected[0]).user_message_id
                    ).sequence
                    if selected
                    else user.sequence
                )
                policy = self.registry.policy.model_dump() if use_policy else None
                if policy and session.review_mode not in COMMAND_PERMISSION_MODES:
                    policy["network"].update(allow_public_https=False, require_write_approval=True)
                context = Context(
                    policy_version="5"
                    if modern
                    else "4"
                    if session.review_mode in COMMAND_PERMISSION_MODES
                    else "3"
                    if has_sandbox
                    else "2",
                    sandbox_policy=policy,
                    host_policy={
                        **self.registry.policy.host.model_dump(),
                        "default_cwd": str(Path(self.registry.policy.host.default_cwd).resolve()),
                        "enabled": self.registry.policy.host.enabled and "host_bash" in names,
                    }
                    if modern
                    else None,
                    run_id=run.id,
                    user_message_id=user.id,
                    user_text=user.payload["content"],
                    selected_run_ids=selected,
                    from_sequence=first,
                    to_sequence=user.sequence,
                    dropped_turns=dropped,
                    context_bytes=size,
                    tool_names=names,
                    review_mode=session.review_mode,
                    bash_enabled=session.bash_enabled,
                    captured_at=self.now(),
                )
                run.context = context.model_dump(by_alias=True)
            else:
                context = Context.model_validate(run.context)
                specs = self.frozen_specs(context)
                history = self.history_in(db, context.selected_run_ids + [run.id], protocol)
                size = (
                    json_size(turn_request(profile, history, specs))
                    if profile
                    else json_size(history)
                )
                if size > (
                    context.sandbox_policy["context_bytes"]
                    if context.sandbox_policy
                    else self.budget
                ):
                    raise AppError(Code.CONTEXT_TOO_LARGE)
                if context.run_id != run.id or context.user_message_id != user.id:
                    raise AppError(Code.INVALID_STATE)
            return profile, context, history, specs
