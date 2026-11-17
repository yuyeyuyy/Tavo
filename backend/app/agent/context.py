import json
from pathlib import Path

from sqlalchemy import func, select

from app.agent.contracts import COMMAND_PERMISSION_MODES, Context
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


class ContextBuilder:
    def __init__(self, db, registry, secrets, now, budget=CONTEXT_BYTES):
        self.db, self.registry, self.now, self.budget = db, registry, now, budget
        self.private = PrivateStateStore(secrets)

    def specs(self, mode, bash_enabled=False, execution_scope="sandbox_only"):
        return [
            s
            for s in self.registry.specs(execution_scope)
            if mode != "disabled" and (s.read_only or s.name == "host_bash" or bash_enabled)
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

    def prepare(self, scope):
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            session = db.get(ConversationSession, scope.session_id)
            modern = session.execution_scope == "host_and_sandbox"
            profile = ModelProfile.model_validate(run.profile) if run.profile else None
            protocol = profile.protocol if profile else "completions"
            user = db.get(ConversationMessage, run.user_message_id)
            if not run.context:
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
                        json_size(
                            turn_request(
                                profile,
                                history,
                                self.specs(
                                    session.review_mode,
                                    session.bash_enabled,
                                    session.execution_scope,
                                ),
                            )
                        )
                        if profile
                        else json_size(
                            {
                                "history": history,
                                "tools": [
                                    s.model_dump()
                                    for s in self.specs(
                                        session.review_mode,
                                        session.bash_enabled,
                                        session.execution_scope,
                                    )
                                ],
                            }
                        )
                    )
                    budget = (
                        self.registry.policy.context_bytes
                        if modern or (session.bash_enabled and "bash" in self.registry.definitions)
                        else self.budget
                    )
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
                policy = (
                    self.registry.policy.model_dump()
                    if modern or (session.bash_enabled and "bash" in self.registry.definitions)
                    else None
                )
                if policy and session.review_mode not in COMMAND_PERMISSION_MODES:
                    policy["network"].update(allow_public_https=False, require_write_approval=True)
                context = Context(
                    policy_version="5"
                    if modern
                    else "4"
                    if session.review_mode in COMMAND_PERMISSION_MODES
                    else "3"
                    if modern or (session.bash_enabled and "bash" in self.registry.definitions)
                    else "2",
                    sandbox_policy=policy,
                    host_policy={
                        **self.registry.policy.host.model_dump(),
                        "default_cwd": str(Path(self.registry.policy.host.default_cwd).resolve()),
                        "enabled": self.registry.policy.host.enabled
                        and "host_bash" in self.registry.definitions,
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
                    tool_names=[
                        s.name
                        for s in self.specs(
                            session.review_mode, session.bash_enabled, session.execution_scope
                        )
                    ],
                    review_mode=session.review_mode,
                    bash_enabled=session.bash_enabled,
                    captured_at=self.now(),
                )
                run.context = context.model_dump(by_alias=True)
            else:
                context = Context.model_validate(run.context)
                history = self.history_in(db, context.selected_run_ids + [run.id], protocol)
                size = (
                    json_size(
                        turn_request(
                            profile,
                            history,
                            self.specs(
                                context.review_mode,
                                context.bash_enabled,
                                "host_and_sandbox"
                                if context.policy_version == "5"
                                else "sandbox_only",
                            ),
                        )
                    )
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
            return (
                profile,
                context,
                history,
                [
                    spec
                    for spec in self.specs(
                        context.review_mode,
                        context.bash_enabled,
                        "host_and_sandbox" if context.policy_version == "5" else "sandbox_only",
                    )
                    if spec.name in context.tool_names
                ],
            )
