import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from app.agent.contracts import (
    AssistantMessage,
    Context,
    ContextSummary,
    Decision,
    MessageAccepted,
    MessagePage,
    Run,
    Session,
    SessionPage,
    ToolCall,
    ToolCallMessage,
    ToolResult,
    ToolResultMessage,
    UserMessage,
)
from app.core.errors import AppError, Code
from app.models.entities import (
    AssistantModelTurn,
    AssistantReview,
    AssistantRun,
    AssistantToolCall,
    AssistantToolResult,
    BashExecution,
    ConversationMessage,
    ConversationSession,
    WorkerLease,
)

ACTIVE = ("pending", "running", "waiting_review")
FINAL = ("succeeded", "failed", "cancelled", "interrupted")


def identity():
    return str(uuid4())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def call_digest(name, arguments):
    return digest({"name": name, "arguments": arguments, "version": 1})


def require_leader(db, owner, fence, now):
    lease = db.get(WorkerLease, 1)
    if not lease or lease.owner != owner or lease.fence != fence or lease.expires_at <= now:
        raise AppError(Code.LEASE_LOST)
    return lease


def require_session(db, identity):
    row = db.get(ConversationSession, identity)
    if row is None:
        raise AppError(Code.NOT_FOUND)
    return row


def require_run(db, session_id, run_id):
    row = db.get(AssistantRun, run_id)
    if row is None or row.session_id != session_id:
        raise AppError(Code.NOT_FOUND)
    return row


def require_scope(db, scope, now):
    run = require_run(db, scope.session_id, scope.run_id)
    lease = db.get(WorkerLease, 1)
    if (
        not lease
        or lease.owner != scope.owner
        or lease.fence != scope.fence
        or lease.expires_at <= now
    ):
        raise AppError(Code.LEASE_LOST)
    if (
        run.status != "running"
        or run.worker_owner != scope.owner
        or run.worker_fence != scope.fence
    ):
        raise AppError(Code.CANCELLED if run.status in FINAL else Code.LEASE_LOST)
    return run


class SessionRepository:
    def __init__(self, db, now):
        self.db, self.now = db, now

    def session_out(self, db, row):
        active = db.scalar(
            select(AssistantRun.id).where(
                AssistantRun.session_id == row.id, AssistantRun.status.in_(ACTIVE)
            )
        )
        return Session.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in Session.model_fields
                    if name != "active_run_id"
                },
                "active_run_id": active,
            }
        )

    def create(self, value, before_write):
        with self.db.session(write=True) as db:
            before_write()
            row = ConversationSession(
                id=identity(),
                title="新对话",
                profile_id=value.profile_id,
                review_mode=value.review_mode,
                bash_enabled=value.bash_enabled,
                execution_scope="host_and_sandbox"
                if value.review_mode in {"full_access", "auto_review", "request_approval"}
                else "sandbox_only",
                revision=0,
                next_sequence=1,
                created_at=self.now(),
                updated_at=self.now(),
            )
            db.add(row)
            db.flush()
            return self.session_out(db, row)

    def get(self, session_id):
        with self.db.session() as db:
            return self.session_out(db, require_session(db, session_id))

    def list(self, limit, before):
        with self.db.session() as db:
            query = select(ConversationSession).order_by(
                ConversationSession.created_at.desc(), ConversationSession.id.desc()
            )
            if before:
                cursor = require_session(db, before)
                query = query.where(
                    (ConversationSession.created_at < cursor.created_at)
                    | (
                        (ConversationSession.created_at == cursor.created_at)
                        & (ConversationSession.id < cursor.id)
                    )
                )
            rows = list(db.scalars(query.limit(limit + 1)))
            return SessionPage(
                items=[self.session_out(db, row) for row in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    def add_message(self, db, session, kind, payload, run_id=None, client_id=None):
        message = ConversationMessage(
            id=identity(),
            session_id=session.id,
            run_id=run_id,
            client_message_id=client_id,
            sequence=session.next_sequence,
            kind=kind,
            payload=payload,
            created_at=self.now(),
        )
        session.next_sequence += 1
        session.revision += 1
        session.updated_at = self.now()
        db.add(message)
        db.flush()
        return message

    def new_run(self, db, session, user, previous=None):
        run = AssistantRun(
            id=identity(),
            session_id=session.id,
            user_message_id=user.id,
            previous_run_id=previous,
            status="pending",
            step="model",
            rounds=0,
            tool_count=0,
            active_ms=0,
            revision=1,
            created_at=self.now(),
        )
        db.add(run)
        db.flush()
        session.latest_run_id = run.id
        return run

    def send(self, session_id, value, before_write):
        with self.db.session(write=True) as db:
            before_write()
            session = require_session(db, session_id)
            existing = db.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.session_id == session_id,
                    ConversationMessage.client_message_id == value.client_message_id,
                )
            )
            if existing:
                if existing.payload["content"] != value.content:
                    raise AppError(Code.REVISION_CONFLICT)
                run = require_run(db, session_id, existing.run_id)
                return MessageAccepted(
                    message=self.message_out(db, existing),
                    run=self.run_out(db, run),
                    session=self.session_out(db, session),
                )
            if session.revision != value.expected_revision:
                raise AppError(Code.REVISION_CONFLICT)
            if db.scalar(
                select(AssistantRun.id).where(
                    AssistantRun.session_id == session_id, AssistantRun.status.in_(ACTIVE)
                )
            ):
                raise AppError(Code.BUSY)
            user = self.add_message(
                db, session, "user", {"content": value.content}, client_id=value.client_message_id
            )
            run = self.new_run(db, session, user)
            user.run_id = run.id
            if session.title == "新对话":
                session.title = value.content.strip()[:80]
            return MessageAccepted(
                message=self.message_out(db, user),
                run=self.run_out(db, run),
                session=self.session_out(db, session),
            )

    def call_out(self, db, row):
        decisions = list(
            db.scalars(
                select(AssistantReview)
                .where(AssistantReview.tool_call_id == row.id)
                .order_by(AssistantReview.revision)
            )
        )
        return ToolCall(
            id=row.id,
            run_id=row.run_id,
            round=row.round,
            name=row.name,
            arguments=row.arguments,
            status=row.status,
            review_revision=row.review_revision,
            expires_at=row.expires_at,
            attempts=row.attempts,
            decisions=[Decision.model_validate(v) for v in decisions],
        )

    def message_out(self, db, row):
        base = {
            "id": row.id,
            "session_id": row.session_id,
            "run_id": row.run_id,
            "sequence": row.sequence,
            "created_at": row.created_at,
        }
        if row.kind == "user":
            return UserMessage(**base, **row.payload)
        if row.kind == "assistant":
            return AssistantMessage(**base, **row.payload)
        if row.kind == "tool_call":
            call = db.get(AssistantToolCall, row.payload["toolCallId"])
            if not call or call.run_id != row.run_id:
                raise AppError(Code.INVALID_STATE)
            return ToolCallMessage(**base, call=self.call_out(db, call))
        result = db.get(AssistantToolResult, row.payload["toolResultId"])
        call = db.get(AssistantToolCall, result.tool_call_id) if result else None
        if not call or call.run_id != row.run_id:
            raise AppError(Code.INVALID_STATE)
        return ToolResultMessage(**base, result=ToolResult.model_validate(result))

    def messages(self, session_id, limit, after, before):
        with self.db.session() as db:
            require_session(db, session_id)
            query = select(ConversationMessage).where(ConversationMessage.session_id == session_id)
            if after is not None:
                query = query.where(ConversationMessage.sequence > after)
            if before is not None:
                query = query.where(ConversationMessage.sequence < before)
            # Initial page is the latest page; after reads incrementally in ascending order.
            query = query.order_by(
                ConversationMessage.sequence.asc()
                if after is not None
                else ConversationMessage.sequence.desc()
            )
            rows = list(db.scalars(query.limit(limit + 1)))
            page = rows[:limit]
            if after is None:
                page.reverse()
            return MessagePage(
                items=[self.message_out(db, row) for row in page],
                has_more=len(rows) > limit,
                next_sequence=(page[-1].sequence if after is not None else page[0].sequence)
                if page
                else None,
            )

    def run_out(self, db, row):
        calls = list(
            db.scalars(
                select(AssistantToolCall)
                .where(AssistantToolCall.run_id == row.id)
                .order_by(AssistantToolCall.round, AssistantToolCall.ordinal)
            )
        )
        context = Context.model_validate(row.context) if row.context else None
        summary = (
            ContextSummary(**{name: getattr(context, name) for name in ContextSummary.model_fields})
            if context
            else None
        )
        return Run(
            id=row.id,
            session_id=row.session_id,
            user_message_id=row.user_message_id,
            previous_run_id=row.previous_run_id,
            status=row.status,
            step=row.step,
            rounds=row.rounds,
            tool_count=row.tool_count,
            revision=row.revision,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_code=row.error_code,
            context=summary,
            tool_calls=[self.call_out(db, c) for c in calls],
        )

    def get_run(self, session_id, run_id):
        with self.db.session() as db:
            return self.run_out(db, require_run(db, session_id, run_id))

    def finish(self, db, run, status, error=None):
        run.status = status
        run.error_code = error
        run.finished_at = self.now()
        run.revision += 1
        if status != "succeeded":
            uncertain = db.scalar(
                select(BashExecution.id)
                .join(AssistantToolCall, BashExecution.tool_call_id == AssistantToolCall.id)
                .where(
                    AssistantToolCall.run_id == run.id,
                    (BashExecution.state.in_(("created", "running", "unknown")))
                    | (
                        (BashExecution.state == "finished")
                        & ~select(AssistantToolResult.id)
                        .where(AssistantToolResult.tool_call_id == AssistantToolCall.id)
                        .exists()
                    ),
                )
            )
            if uncertain:
                require_session(db, run.session_id).workspace_unconfirmed = True
            for turn in db.scalars(
                select(AssistantModelTurn).where(
                    AssistantModelTurn.run_id == run.id, AssistantModelTurn.status == "inflight"
                )
            ):
                turn.status = "unknown"
                turn.finished_at = self.now()
            for call in db.scalars(
                select(AssistantToolCall).where(
                    AssistantToolCall.run_id == run.id,
                    AssistantToolCall.status.in_(
                        ("proposed", "waiting_review", "approved", "running")
                    ),
                )
            ):
                self.result(db, run, call, "cancelled", {}, error or "CANCELLED")
        session = require_session(db, run.session_id)
        session.revision += 1
        session.updated_at = self.now()

    def cancel(self, session_id, run_id, before_write):
        with self.db.session(write=True) as db:
            before_write()
            run = require_run(db, session_id, run_id)
            if run.status not in FINAL:
                self.finish(db, run, "cancelled", "CANCELLED")
                for call in db.scalars(
                    select(AssistantToolCall).where(
                        AssistantToolCall.run_id == run.id,
                        AssistantToolCall.status.in_(
                            ("proposed", "waiting_review", "approved", "running")
                        ),
                    )
                ):
                    self.result(db, run, call, "cancelled", {}, "CANCELLED")
            return self.run_out(db, run)

    def retry(self, session_id, run_id, before_write):
        with self.db.session(write=True) as db:
            before_write()
            session = require_session(db, session_id)
            old = require_run(db, session_id, run_id)
            child = db.scalar(select(AssistantRun).where(AssistantRun.previous_run_id == old.id))
            if child:
                return self.run_out(db, child)
            if session.latest_run_id != old.id:
                raise AppError(Code.INVALID_STATE)
            if old.status not in ("failed", "cancelled", "interrupted"):
                raise AppError(Code.INVALID_STATE)
            if db.scalar(
                select(AssistantRun.id).where(
                    AssistantRun.session_id == session_id, AssistantRun.status.in_(ACTIVE)
                )
            ):
                raise AppError(Code.BUSY)
            user = db.get(ConversationMessage, old.user_message_id)
            # Retrying an old turn after new user content would fork its meaning.
            latest = db.scalar(
                select(ConversationMessage.id)
                .where(
                    ConversationMessage.session_id == session_id, ConversationMessage.kind == "user"
                )
                .order_by(ConversationMessage.sequence.desc())
                .limit(1)
            )
            if not user or latest != user.id:
                raise AppError(Code.INVALID_STATE)
            run = self.new_run(db, session, user, old.id)
            session.revision += 1
            session.updated_at = self.now()
            return self.run_out(db, run)

    def result(self, db, run, call, status, output, error=None):
        if (
            not output
            and run.context
            and run.context.get("policyVersion") in {"3", "4", "5"}
            and call.name in {"bash", "sandbox", "host_bash", "workspace_write", "http_request"}
        ):
            from app.agent.bash import bash_result

            output = bash_result(Code(error) if error in Code else Code.EXECUTION_ERROR).model_dump(
                by_alias=True
            )
            output["executionTarget"] = "host" if call.name == "host_bash" else "sandbox"
        if (
            call.name in {"bash", "sandbox", "host_bash", "workspace_write", "http_request"}
            and "exitCode" in output
        ):
            output = {
                **output,
                "executionTarget": "host" if call.name == "host_bash" else "sandbox",
            }
        existing = db.scalar(
            select(AssistantToolResult).where(AssistantToolResult.tool_call_id == call.id)
        )
        if existing:
            return existing
        result = AssistantToolResult(
            id=identity(),
            tool_call_id=call.id,
            status=status,
            output=output,
            error_code=error,
            created_at=self.now(),
        )
        call.status = status
        db.add(result)
        db.flush()
        self.add_message(
            db,
            require_session(db, run.session_id),
            "tool_result",
            {"toolResultId": result.id},
            run.id,
        )
        return result

    def review(self, db, call, action, source, reason, summary=None, policy_version="1"):
        from app.core.startup import audit_tool

        audit_tool(
            "approval"
            if source == "human"
            else "model"
            if source in {"model", "mock"}
            else "parameter"
            if reason in {"INVALID_INPUT", "COMMAND_TOO_LONG"}
            else "policy",
            reason,
            call.id,
        )
        call.review_revision += 1
        value = AssistantReview(
            id=identity(),
            tool_call_id=call.id,
            revision=call.review_revision,
            action=action,
            source=source,
            reason_code=reason,
            policy_version=policy_version,
            summary=summary,
            argument_digest=call.argument_digest,
            created_at=self.now(),
        )
        db.add(value)
        db.flush()
        return value

    def decide(self, session_id, call_id, value, before_write):
        with self.db.session(write=True) as db:
            before_write()
            call = db.get(AssistantToolCall, call_id)
            if call is None:
                raise AppError(Code.NOT_FOUND)
            run = require_run(db, session_id, call.run_id)
            action = "allow" if value.action == "approve" else "deny"
            existing = db.scalar(
                select(AssistantReview)
                .where(AssistantReview.tool_call_id == call.id, AssistantReview.source == "human")
                .order_by(AssistantReview.revision.desc())
                .limit(1)
            )
            if existing and call.review_revision != value.expected_revision:
                if existing.revision == value.expected_revision + 1 and existing.action == action:
                    return Decision.model_validate(existing)
                raise AppError(Code.REVISION_CONFLICT)
            if call.review_revision != value.expected_revision:
                raise AppError(Code.REVISION_CONFLICT)
            if run.status != "waiting_review" or call.status != "waiting_review":
                raise AppError(Code.INVALID_STATE)
            if call.expires_at <= self.now():
                raise AppError(Code.REVIEW_EXPIRED)
            if call_digest(call.name, call.arguments) != call.argument_digest:
                raise AppError(Code.INVALID_STATE)
            if call.name == "bash" and action == "allow":
                require_session(db, session_id).workspace_unconfirmed = False
            from app.models.entities import SandboxOperation

            operation = db.get(SandboxOperation, call.id)
            approval_digest = (
                operation.approval_digest
                if operation and operation.state == "awaiting_commit"
                else None
            )
            decision = self.review(
                db,
                call,
                action,
                "human",
                "USER_APPROVED" if action == "allow" else "USER_REJECTED",
                summary=approval_digest,
                policy_version=Context.model_validate(run.context).policy_version
                if run.context
                else "1",
            )
            call.status = "approved" if action == "allow" else "denied"
            if action == "deny":
                self.result(
                    db,
                    run,
                    call,
                    "denied",
                    {},
                    "POLICY_DENIED"
                    if run.context and run.context.get("policyVersion") in {"3", "4", "5"}
                    else "FORBIDDEN",
                )
            pending = db.scalar(
                select(AssistantToolCall.id).where(
                    AssistantToolCall.run_id == run.id, AssistantToolCall.status == "waiting_review"
                )
            )
            if not pending:
                run.status = "pending"
                run.step = "tools"
            run.revision += 1
            return Decision.model_validate(decision)
