import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError
from sqlalchemy import select

from app.agent.bash import bash_result
from app.agent.bash_policy import classify
from app.agent.context import ContextBuilder, json_size, turn_binding
from app.agent.contracts import Context
from app.agent.graph import create_graph
from app.agent.mock import MockAssistantModel
from app.agent.self_review import REVIEW_SYSTEM, ReviewItem, parse_review, review_history
from app.agent.tools import ToolCallReviewer, ToolContext, ToolRegistry
from app.core.errors import AppError, Code
from app.llm.config import ModelProfile
from app.llm.keys import ModelKeys
from app.llm.turns import AssistantTurn
from app.models.entities import (
    AssistantModelTurn,
    AssistantToolCall,
    AssistantToolResult,
    BashExecution,
    SandboxNetworkRequest,
    SandboxOperation,
)
from app.repositories.sessions import call_digest, identity, require_scope, require_session
from app.repositories.time import plus_seconds


class Assistant:
    def __init__(
        self, db, repository, secrets, models, gateway, now, registry=None, bash=None, host=None
    ):
        self.db, self.repo, self.models, self.gateway, self.now = (
            db,
            repository,
            models,
            gateway,
            now,
        )
        self.bash = bash
        self.host = host
        self.registry = registry or ToolRegistry(
            bash_enabled=bool(bash and bash.settings.bash_enabled),
            host_enabled=bool(host and host.available),
            policy=bash.settings.sandbox if bash else None,
        )
        self.reviewer = ToolCallReviewer(self.registry)
        self.context = ContextBuilder(db, self.registry, secrets, now)
        self.keys = ModelKeys(secrets)
        self.mock = MockAssistantModel()
        self.tool_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tavo-tools")
        self.readers = {}
        self.tool_timeout = 2

    def busy_slots(self):
        return (
            set(self.readers)
            | (self.bash.busy_slots() if self.bash else set())
            | (self.host.busy_slots() if self.host else set())
        )

    def budget(self, run, name, legacy):
        value = (run.context or {}).get("sandboxPolicy")
        return (
            min(value[name], getattr(self.registry.policy, name))
            if value and (run.context or {}).get("policyVersion") in {"3", "4", "5"}
            else legacy
        )

    def budget_ms(self, run):
        return self.budget(run, "max_active_seconds", 180) * 1000

    async def run(self, scope):
        try:
            with self.db.session() as db:
                row = require_scope(db, scope, self.now())
                step = row.step
            await create_graph(self, scope).ainvoke(
                {"step": step, "done": False},
                {"recursion_limit": max(64, self.registry.policy.max_rounds * 4 + 8)},
            )
        except BaseException as error:
            code = (
                Code.INTERRUPTED
                if isinstance(error, asyncio.CancelledError)
                else error.code
                if isinstance(error, AppError)
                else Code.INTERNAL
            )
            try:
                with self.db.session(write=True) as db:
                    run = require_scope(db, scope, self.now())
                    for turn in db.scalars(
                        select(AssistantModelTurn).where(
                            AssistantModelTurn.run_id == run.id,
                            AssistantModelTurn.status == "inflight",
                        )
                    ):
                        turn.status = "unknown"
                        turn.finished_at = self.now()
                    self.repo.finish(
                        db,
                        run,
                        "interrupted"
                        if code in (Code.INTERRUPTED, Code.TOOL_EXECUTION_UNKNOWN)
                        else "failed",
                        code.value,
                    )
            except AppError as rejected:
                if rejected.code not in (Code.LEASE_LOST, Code.CANCELLED, Code.NOT_FOUND):
                    raise
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            if self.bash and self.bash.manager:
                await self.bash.manager.finish_run(scope)

    async def model(self, scope):
        profile, context, history, specs = self.context.prepare(scope)
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            if run.rounds >= self.budget(run, "max_rounds", 4) or run.active_ms >= self.budget_ms(
                run
            ):
                raise AppError(Code.TOOL_BUDGET_EXCEEDED)
            run.rounds += 1
            run.revision += 1
            round_number = run.rounds
            turn_id = identity()
            db.add(
                AssistantModelTurn(
                    id=turn_id,
                    run_id=run.id,
                    round=round_number,
                    status="inflight",
                    created_at=self.now(),
                )
            )
            binding = turn_binding(run, round_number)
            remaining = (self.budget_ms(run) - run.active_ms) / 1000
        started = time.monotonic()
        try:
            async with asyncio.timeout(min(60, remaining)):
                turn = (
                    await self.gateway.turn(profile, history, specs, self.keys.get(profile))
                    if profile
                    else await self.mock.turn(None, history, specs)
                )
        except TimeoutError:
            raise AppError(Code.MODEL_TIMEOUT) from None
        key_id, ciphertext = self.context.private.seal(binding, turn.model_dump())
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            run.active_ms += int((time.monotonic() - started) * 1000)
            if run.active_ms >= self.budget_ms(run) or run.tool_count + len(
                turn.calls
            ) > self.budget(run, "max_tool_calls", 8):
                raise AppError(Code.TOOL_BUDGET_EXCEEDED)
            row = db.get(AssistantModelTurn, turn_id)
            row.status = "completed"
            row.key_id = key_id
            row.ciphertext = ciphertext
            row.finished_at = self.now()
            session = require_session(db, scope.session_id)
            if turn.text:
                self.repo.add_message(
                    db,
                    session,
                    "assistant",
                    {
                        "content": turn.text,
                        "final": not bool(turn.calls),
                        "truncated": turn.truncated,
                        "usage": turn.usage.model_dump(by_alias=True) if turn.usage else None,
                    },
                    run.id,
                )
            for index, proposed in enumerate(turn.calls):
                call = AssistantToolCall(
                    id=identity(),
                    run_id=run.id,
                    round=round_number,
                    ordinal=index,
                    provider_call_id=proposed.provider_id,
                    name=proposed.name,
                    arguments=proposed.arguments,
                    argument_digest=call_digest(proposed.name, proposed.arguments),
                    status="proposed",
                    review_revision=0,
                    attempts=0,
                    created_at=self.now(),
                )
                db.add(call)
                db.flush()
                self.repo.add_message(db, session, "tool_call", {"toolCallId": call.id}, run.id)
            run.tool_count += len(turn.calls)
            if not turn.calls:
                self.repo.finish(db, run, "succeeded")
                return {"step": "model", "done": True}
            run.step = "review"
            return {"step": "review", "done": False}

    async def review(self, scope):
        with self.db.session() as db:
            run = require_scope(db, scope, self.now())
            context = Context.model_validate(run.context)
            calls = list(
                db.scalars(
                    select(AssistantToolCall)
                    .where(
                        AssistantToolCall.run_id == run.id,
                        AssistantToolCall.round == run.rounds,
                        AssistantToolCall.status == "proposed",
                    )
                    .order_by(AssistantToolCall.ordinal)
                )
            )
            policy = {
                c.id: self.reviewer.review(
                    c.name, c.arguments, context.tool_names, context.review_mode
                )
                for c in calls
            }
            valid = [c for c in calls if policy[c.id][0] != "deny"]
            version = context.policy_version
        if version in {"3", "4", "5"}:
            return await self.review_v3(scope, context, calls)
        decisions = {}
        if valid and version == "2":
            decisions = await self.self_review(scope, context, valid)
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            waiting = False
            session = require_session(db, scope.session_id)
            for old in calls:
                call = db.get(AssistantToolCall, old.id)
                if call_digest(call.name, call.arguments) != old.argument_digest:
                    raise AppError(Code.FORBIDDEN)
                action, reason = policy[call.id]
                self.repo.review(db, call, action, "policy", reason, policy_version=version)
                reviewed = decisions.get(call.id)
                if reviewed:
                    self.repo.review(
                        db,
                        call,
                        reviewed.action,
                        "model" if run.profile else "mock",
                        reviewed.reason_code,
                        reviewed.summary,
                        version,
                    )
                    if reviewed.action == "deny":
                        action = "deny"
                    elif reviewed.action == "ask" and action != "deny":
                        action = "ask"
                if call.name == "bash" and session.workspace_unconfirmed and action != "deny":
                    action = "ask"
                    self.repo.review(
                        db, call, "ask", "policy", "WORKSPACE_STATE_UNKNOWN", policy_version=version
                    )
                if action == "deny":
                    self.repo.result(db, run, call, "denied", {}, "FORBIDDEN")
                elif action == "ask":
                    call.status = "waiting_review"
                    call.expires_at = plus_seconds(self.now(), 600)
                    waiting = True
                else:
                    call.status = "approved"
                # A repeated failed/denied identical request has made no progress.
                previous = list(
                    db.scalars(
                        select(AssistantToolCall)
                        .where(
                            AssistantToolCall.run_id == run.id, AssistantToolCall.round < run.rounds
                        )
                        .order_by(AssistantToolCall.round.desc(), AssistantToolCall.ordinal.desc())
                        .limit(8)
                    )
                )
                if (
                    previous
                    and previous[0].argument_digest == call.argument_digest
                    and previous[0].status in ("failed", "denied")
                ):
                    raise AppError(Code.TOOL_LOOP_DETECTED)
            run.step = "tools"
            run.revision += 1
            if waiting:
                run.status = "waiting_review"
            return {"step": "tools", "done": waiting}

    async def self_review(self, scope, context, calls):
        with self.db.session() as db:
            run = require_scope(db, scope, self.now())
            profile = ModelProfile.model_validate(run.profile) if run.profile else None
            number = run.rounds
            remaining = (self.budget_ms(run) - run.active_ms) / 1000
            existing = db.scalar(
                select(AssistantModelTurn).where(
                    AssistantModelTurn.run_id == run.id,
                    AssistantModelTurn.purpose == "self_review",
                    AssistantModelTurn.round == number,
                )
            )
            binding = {**turn_binding(run, number), "purpose": "self_review"}
            if existing:
                if existing.status != "completed":
                    raise AppError(Code.INTERRUPTED)
                return parse_review(
                    AssistantTurn.model_validate(
                        self.context.private.open(binding, existing.ciphertext)
                    ),
                    [c.id for c in calls],
                )
            previous = list(
                db.scalars(
                    select(AssistantToolResult)
                    .join(
                        AssistantToolCall, AssistantToolResult.tool_call_id == AssistantToolCall.id
                    )
                    .where(AssistantToolCall.run_id == run.id)
                    .order_by(AssistantToolCall.round.desc(), AssistantToolCall.ordinal.desc())
                    .limit(2)
                )
            )
            recent = [
                {"status": r.status, "output": r.output, "errorCode": r.error_code}
                for r in previous
            ]
        if remaining <= 0:
            raise AppError(Code.TOOL_BUDGET_EXCEEDED)
        items = [
            {
                "toolCallId": c.id,
                "name": c.name,
                "version": 1,
                "arguments": c.arguments,
                "readOnly": self.registry.definitions[c.name].spec.read_only,
            }
            for c in calls
        ]
        if not profile:
            # Deterministic testing/demo reviewer; not a paid model or a security authority.
            return {
                c.id: ReviewItem(
                    tool_call_id=c.id,
                    action="allow",
                    reason_code="GOAL_ALIGNED",
                    summary="Mock 复核通过；执行权限仍由后端规则限制。",
                )
                for c in calls
            }
        history = review_history(profile, context.user_text, items, recent)
        with self.db.session(write=True) as db:
            require_scope(db, scope, self.now())
            turn_id = identity()
            db.add(
                AssistantModelTurn(
                    id=turn_id,
                    run_id=scope.run_id,
                    purpose="self_review",
                    round=number,
                    status="inflight",
                    created_at=self.now(),
                )
            )
        started = time.monotonic()
        try:
            async with asyncio.timeout(min(30, remaining)):
                turn = await self.gateway.turn(
                    profile, history, [], self.keys.get(profile), system=REVIEW_SYSTEM
                )
            decisions = parse_review(turn, [c.id for c in calls])
        except TimeoutError:
            raise AppError(Code.TOOL_REVIEW_FAILED) from None
        key_id, ciphertext = self.context.private.seal(binding, turn.model_dump())
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            run.active_ms += int((time.monotonic() - started) * 1000)
            if run.active_ms >= self.budget_ms(run):
                raise AppError(Code.TOOL_BUDGET_EXCEEDED)
            row = db.get(AssistantModelTurn, turn_id)
            row.status, row.key_id, row.ciphertext, row.finished_at = (
                "completed",
                key_id,
                ciphertext,
                self.now(),
            )
        return decisions

    async def tools(self, scope):
        with self.db.session() as db:
            run = require_scope(db, scope, self.now())
            ids = list(
                db.scalars(
                    select(AssistantToolCall.id)
                    .where(
                        AssistantToolCall.run_id == run.id,
                        AssistantToolCall.round == run.rounds,
                        AssistantToolCall.status.in_(("approved", "running")),
                    )
                    .order_by(AssistantToolCall.ordinal)
                )
            )
        for call_id in ids:
            with self.db.session() as db:
                current = require_scope(db, scope, self.now())
                host_call = (current.context or {}).get("policyVersion") == "5" and db.get(
                    AssistantToolCall, call_id
                ).name == "host_bash"
                managed = (current.context or {}).get("policyVersion") in {
                    "3",
                    "4",
                    "5",
                } and db.get(AssistantToolCall, call_id).name in {
                    "bash",
                    "sandbox",
                    "workspace_write",
                    "http_request",
                }
            if host_call:
                await self.host_tool(scope, call_id)
                continue
            if managed:
                waiting = await self.managed_tool(scope, call_id)
                if waiting:
                    return {"step": "tools", "done": True}
                continue
            future = None
            started = time.monotonic()
            status = "succeeded"
            output = {}
            error = None
            with self.db.session(write=True) as db:
                run = require_scope(db, scope, self.now())
                call = db.get(AssistantToolCall, call_id)
                context = Context.model_validate(run.context)
                action, _ = self.reviewer.review(
                    call.name, call.arguments, context.tool_names, context.review_mode
                )
                if (
                    action == "deny"
                    or call_digest(call.name, call.arguments) != call.argument_digest
                ):
                    raise AppError(Code.FORBIDDEN)
                if call.attempts >= (
                    1 if call.name == "bash" else 2
                ) or run.active_ms >= self.budget_ms(run):
                    raise AppError(Code.TOOL_BUDGET_EXCEEDED)
                tool, args = self.registry.validate(call.name, call.arguments)
                call.status = "running"
                call.attempts += 1
            if call.name == "bash":
                try:
                    if not self.bash or not context.bash_enabled:
                        raise AppError(Code.FORBIDDEN)
                    # Recovery never repeats an invocation with a durable execution intent.
                    with self.db.session() as db:
                        if db.scalar(
                            select(BashExecution).where(BashExecution.tool_call_id == call.id)
                        ):
                            raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
                    async with asyncio.timeout(
                        max(0.001, (self.budget_ms(run) - run.active_ms) / 1000)
                    ):
                        result, error = await self.bash.execute(scope, call.id, args)
                    output = result.model_dump(by_alias=True)
                    status = "failed" if error else "succeeded"
                except TimeoutError:
                    raise AppError(Code.TOOL_BUDGET_EXCEEDED) from None
                except AppError as failure:
                    if failure.code == Code.TOOL_EXECUTION_UNKNOWN:
                        raise
                    status, output, error = "failed", {}, failure.code.value
                with self.db.session(write=True) as db:
                    run = require_scope(db, scope, self.now())
                    run.active_ms += int((time.monotonic() - started) * 1000)
                    if json_size(output) > 8192:
                        raise AppError(Code.CONTEXT_TOO_LARGE)
                    self.repo.result(
                        db, run, db.get(AssistantToolCall, call_id), status, output, error
                    )
                continue
            try:
                if scope.run_id in self.readers or len(self.readers) >= 2:
                    raise AppError(Code.TOOL_BUDGET_EXCEEDED)
                future = asyncio.get_running_loop().run_in_executor(
                    self.tool_pool, tool.execute, args, ToolContext(self.now)
                )
                self.readers[scope.run_id] = future

                def release(completed, run_id=scope.run_id):
                    if self.readers.get(run_id) is completed:
                        self.readers.pop(run_id, None)
                    if not completed.cancelled():
                        completed.exception()

                future.add_done_callback(release)
                done, _ = await asyncio.wait({future}, timeout=self.tool_timeout)
                if not done:
                    raise AppError(Code.TIMEOUT)
                output = tool.result.model_validate(future.result()).model_dump(by_alias=True)
                if json_size(output) > 8192:
                    raise AppError(Code.CONTEXT_TOO_LARGE)
            except asyncio.CancelledError:
                raise
            except Exception as failure:
                status = "failed"
                output = {}
                error = (
                    failure.code.value
                    if isinstance(failure, AppError)
                    else Code.INVALID_DATA.value
                    if isinstance(failure, ValidationError)
                    else Code.TOOL_FAILED.value
                )
            with self.db.session(write=True) as db:
                run = require_scope(db, scope, self.now())
                run.active_ms += int((time.monotonic() - started) * 1000)
                self.repo.result(db, run, db.get(AssistantToolCall, call_id), status, output, error)
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            run.step = "model"
            run.revision += 1
        return {"step": "model", "done": False}

    async def close(self):
        await asyncio.to_thread(self.tool_pool.shutdown, wait=True)

    async def review_v3(self, scope, context, calls):
        risks = {
            c.id: classify(
                c.name, c.arguments, context.review_mode, context.tool_names, self.registry
            )
            for c in calls
        }
        uncertain = [c for c in calls if risks[c.id].model_review and risks[c.id].action != "deny"]
        decisions = await self.self_review(scope, context, uncertain) if uncertain else {}
        waiting = False
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            for old in calls:
                call = db.get(AssistantToolCall, old.id)
                risk = risks[call.id]
                action, reason = risk.action, risk.reason
                if call_digest(call.name, call.arguments) != call.argument_digest:
                    action, reason = "deny", Code.POLICY_DENIED
                if call.name == "http_request" and action != "deny":
                    request = db.get(SandboxNetworkRequest, call.arguments["requestId"])
                    if (
                        not request
                        or request.run_id != scope.run_id
                        or request.tool_call_id not in {None, call.id}
                    ):
                        action, reason = "deny", Code.POLICY_DENIED
                    else:
                        request.tool_call_id = call.id
                        if self.bash.manager.egress.network_for_run(scope.run_id)[
                            "require_write_approval"
                        ]:
                            action, reason = "ask", Code.APPROVAL_REQUIRED
                summary = f"risk={risk.level}; stage=policy"
                self.repo.review(
                    db, call, action, "policy", reason, summary, context.policy_version
                )
                model = decisions.get(call.id)
                if model:
                    self.repo.review(
                        db,
                        call,
                        model.action,
                        "model" if run.profile else "mock",
                        model.reason_code,
                        model.summary,
                        context.policy_version,
                    )
                    if model.action == "deny":
                        action, reason = "deny", Code.MODEL_REVIEW_DENIED
                    elif model.action == "ask" and action != "deny":
                        action, reason = "ask", Code.APPROVAL_REQUIRED
                if (
                    require_session(db, scope.session_id).workspace_unconfirmed
                    and call.name in {"bash", "workspace_write"}
                    and action != "deny"
                    and context.policy_version not in {"4", "5"}
                ):
                    action, reason = "ask", Code.APPROVAL_REQUIRED
                    self.repo.review(
                        db,
                        call,
                        "ask",
                        "policy",
                        "WORKSPACE_STATE_UNKNOWN",
                        policy_version=context.policy_version,
                    )
                if action == "deny":
                    code = reason if reason in Code else Code.POLICY_DENIED
                    self.repo.result(
                        db,
                        run,
                        call,
                        "denied",
                        bash_result(code).model_dump(by_alias=True),
                        str(code),
                    )
                    if self.bash:
                        self.bash.manager.audit(
                            "model"
                            if code == Code.MODEL_REVIEW_DENIED
                            else "parameter"
                            if code in {Code.COMMAND_TOO_LONG, Code.INVALID_INPUT}
                            else "policy",
                            code,
                            call.id,
                        )
                elif action == "ask":
                    call.status, call.expires_at = "waiting_review", plus_seconds(self.now(), 600)
                    waiting = True
                else:
                    call.status = "approved"
                previous = list(
                    db.scalars(
                        select(AssistantToolCall)
                        .where(
                            AssistantToolCall.run_id == run.id, AssistantToolCall.round < call.round
                        )
                        .order_by(AssistantToolCall.round.desc(), AssistantToolCall.ordinal.desc())
                        .limit(self.budget(run, "identical_failures", 2))
                    )
                )
                count = self.budget(run, "identical_failures", 2)
                if len(previous) >= count and all(
                    c.argument_digest == call.argument_digest and c.status in {"failed", "denied"}
                    for c in previous
                ):
                    raise AppError(Code.TOOL_LOOP_DETECTED)
            run.step, run.revision = "tools", run.revision + 1
            if waiting:
                run.status = "waiting_review"
        return {"step": "tools", "done": waiting}

    async def managed_tool(self, scope, call_id):
        started = time.monotonic()
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            call = db.get(AssistantToolCall, call_id)
            context = Context.model_validate(run.context)
            risk = classify(
                call.name, call.arguments, context.review_mode, context.tool_names, self.registry
            )
            if (
                risk.action == "deny"
                or call_digest(call.name, call.arguments) != call.argument_digest
            ):
                raise AppError(Code.POLICY_DENIED)
            op = db.get(SandboxOperation, call_id)
            resume = op is not None and op.state == "awaiting_commit"
            if (call.attempts and not resume) or run.active_ms >= self.budget_ms(run):
                raise AppError(
                    Code.TOOL_EXECUTION_UNKNOWN if call.attempts else Code.TOOL_BUDGET_EXCEEDED
                )
            _, args = self.registry.validate(call.name, call.arguments)
            call.status = "running"
            if not resume:
                call.attempts += 1
        try:
            if not self.bash or not context.bash_enabled or not self.bash.settings.bash_enabled:
                raise AppError(Code.POLICY_DENIED)
            if resume:
                output, error = await self.bash.manager.resume_commit(scope, call_id)
            elif call.name == "http_request":
                result, error = await self.bash.manager.egress.execute(
                    scope, call_id, args.request_id
                )
                output = result.model_dump(by_alias=True)
            else:
                result, error = await self.bash.manager.execute(
                    scope, call_id, args, kind="bash" if call.name == "sandbox" else call.name
                )
                output = result.model_dump(by_alias=True)
        except AppError as failure:
            if failure.code == Code.TOOL_EXECUTION_UNKNOWN:
                raise
            error, output = str(failure.code), bash_result(failure.code).model_dump(by_alias=True)
        except TimeoutError:
            error, output = str(Code.TIMEOUT), bash_result(Code.TIMEOUT).model_dump(by_alias=True)
        output["executionTarget"] = "sandbox"
        output["executionId"] = output.get("sandboxId")
        waiting = False
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            call = db.get(AssistantToolCall, call_id)
            run.active_ms += output.get("durationMs", int((time.monotonic() - started) * 1000))
            op = db.get(SandboxOperation, call_id)
            if op and op.state == "awaiting_commit":
                self.repo.review(
                    db,
                    call,
                    "ask",
                    "policy",
                    Code.APPROVAL_REQUIRED,
                    op.approval_digest,
                    context.policy_version,
                )
                call.status, call.expires_at = "waiting_review", plus_seconds(self.now(), 600)
                waiting = True
            elif error == Code.APPROVAL_REQUIRED and op is None:
                call.attempts = max(0, call.attempts - 1)
                self.repo.review(
                    db,
                    call,
                    "ask",
                    "policy",
                    "WORKSPACE_STATE_UNKNOWN",
                    policy_version=context.policy_version,
                )
                call.status, call.expires_at = "waiting_review", plus_seconds(self.now(), 600)
                waiting = True
            else:
                pending_approval = db.scalar(
                    select(SandboxNetworkRequest.id).where(
                        SandboxNetworkRequest.run_id == run.id,
                        SandboxNetworkRequest.parent_call_id == call.id,
                        SandboxNetworkRequest.tool_call_id.is_(None),
                    )
                )
                if pending_approval and error == Code.EXECUTION_ERROR:
                    from app.core.errors import MESSAGES

                    error = str(Code.APPROVAL_REQUIRED)
                    output.update(errorCode=error, errorMessage=MESSAGES[Code.APPROVAL_REQUIRED])
                self.repo.result(db, run, call, "failed" if error else "succeeded", output, error)
            pending = list(
                db.scalars(
                    select(SandboxNetworkRequest).where(
                        SandboxNetworkRequest.run_id == run.id,
                        SandboxNetworkRequest.tool_call_id.is_(None),
                        SandboxNetworkRequest.state == "pending",
                    )
                )
            )
            for request in pending:
                if run.tool_count >= self.budget(run, "max_tool_calls", 8):
                    raise AppError(Code.TOOL_BUDGET_EXCEEDED)
                ordinal = (
                    max(
                        (
                            c.ordinal
                            for c in db.scalars(
                                select(AssistantToolCall).where(
                                    AssistantToolCall.run_id == run.id,
                                    AssistantToolCall.round == run.rounds,
                                )
                            )
                        ),
                        default=-1,
                    )
                    + 1
                )
                arguments = {"requestId": request.id}
                key = identity()
                nested = AssistantToolCall(
                    id=key,
                    run_id=run.id,
                    round=run.rounds,
                    ordinal=ordinal,
                    provider_call_id="internal-" + key,
                    name="http_request",
                    arguments=arguments,
                    argument_digest=call_digest("http_request", arguments),
                    status="waiting_review",
                    review_revision=0,
                    attempts=0,
                    expires_at=plus_seconds(self.now(), 600),
                    created_at=self.now(),
                )
                db.add(nested)
                db.flush()
                request.tool_call_id = key
                self.repo.add_message(
                    db,
                    require_session(db, scope.session_id),
                    "tool_call",
                    {"toolCallId": key},
                    run.id,
                )
                self.repo.review(
                    db,
                    nested,
                    "ask",
                    "policy",
                    Code.APPROVAL_REQUIRED,
                    f"risk=HIGH; target={request.target}; method={request.method}; digest={request.request_digest}",
                    context.policy_version,
                )
                run.tool_count += 1
                waiting = True
            if waiting:
                run.status, run.step = "waiting_review", "tools"
            run.revision += 1
        return waiting

    async def host_tool(self, scope, call_id):
        started = time.monotonic()
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            call = db.get(AssistantToolCall, call_id)
            context = Context.model_validate(run.context)
            risk = classify(
                call.name, call.arguments, context.review_mode, context.tool_names, self.registry
            )
            if risk.action == "deny" or call.argument_digest != call_digest(
                call.name, call.arguments
            ):
                raise AppError(Code.POLICY_DENIED)
            if call.attempts:
                raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
            _, args = self.registry.validate(call.name, call.arguments)
            call.status, call.attempts = "running", 1
        try:
            if not self.host:
                raise AppError(Code.HOST_UNAVAILABLE)
            result, error = await self.host.execute(scope, call_id, args)
        except AppError as failure:
            if failure.code in {Code.TOOL_EXECUTION_UNKNOWN, Code.LEASE_LOST, Code.CANCELLED}:
                raise
            error = str(failure.code)
            result = bash_result(failure.code)
            result.execution_target = "host"
        with self.db.session(write=True) as db:
            run = require_scope(db, scope, self.now())
            run.active_ms += int((time.monotonic() - started) * 1000)
            self.repo.result(
                db,
                run,
                db.get(AssistantToolCall, call_id),
                "failed" if error else "succeeded",
                result.model_dump(by_alias=True),
                error,
            )
