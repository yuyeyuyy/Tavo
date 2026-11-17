from fastapi import APIRouter, Depends, Query, Request, Response

from app.agent.contracts import (
    AgentRuntime,
    ApprovalPreview,
    BashStatus,
    Decision,
    DecisionWrite,
    HostBashStatus,
    MessageAccepted,
    MessagePage,
    NetworkTargetList,
    NetworkTargetStatus,
    PoolStatus,
    Run,
    Session,
    SessionCreate,
    SessionPage,
    ToolList,
    UserMessageWrite,
)
from app.api.dependencies import ServicesDep, SessionToken, authenticated
from app.api.schemas import ErrorResponse
from app.core.errors import AppError, Code

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(authenticated)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 410, 413, 422, 503)},
)


@router.get("/sandbox-network-targets", response_model=NetworkTargetList)
def network_targets(svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    keys = svc.bash.manager.egress.keys
    return NetworkTargetList(
        mode=svc.settings.sandbox.network.mode,
        items=[
            NetworkTargetStatus(
                id=t.id,
                hostname=t.hostname,
                capability=t.capability,
                methods=t.methods,
                paths=t.paths,
                credential_required=t.credential,
                credential_revision=keys.revision(t) if t.credential else 0,
                ready=keys.ready(t) if t.credential else True,
            )
            for t in svc.settings.sandbox.network.targets
        ],
    )


@router.get("/sessions/{session_id}/tool-calls/{call_id}/approval", response_model=ApprovalPreview)
def approval_preview(session_id: str, call_id: str, svc: ServicesDep, response: Response):
    import base64

    from sqlalchemy import select

    from app.agent.egress import binding
    from app.models.entities import AssistantToolCall, SandboxNetworkRequest, SandboxOperation
    from app.repositories.sessions import require_run

    response.headers["Cache-Control"] = "no-store"
    with svc.db.session() as db:
        call = db.get(AssistantToolCall, call_id)
        if not call:
            raise AppError(Code.NOT_FOUND)
        require_run(db, session_id, call.run_id)
        operation = db.get(SandboxOperation, call_id)
        if operation and operation.approval_detail and operation.approval_digest:
            return ApprovalPreview(
                kind="workspace_commit",
                description="工作区大范围变更；批准只提交此版本，不重跑脚本。",
                digest=operation.approval_digest,
                details=operation.approval_detail,
            )
        record = db.scalar(
            select(SandboxNetworkRequest).where(SandboxNetworkRequest.tool_call_id == call_id)
        )
        if not record:
            raise AppError(Code.NOT_FOUND)
        payload = svc.bash.manager.egress.private.open(binding(record), record.ciphertext)
        from sandbox.runtime.tavo_sandbox.network import NetworkFailure, target_for

        try:
            target = target_for(
                record.target, svc.bash.manager.egress.network_for_run(record.run_id)
            )
        except NetworkFailure:
            raise AppError(Code.NETWORK_DENIED) from None
        return ApprovalPreview(
            kind="network",
            description="授权这一个 HTTP 请求；不会重新运行父脚本。",
            digest=record.request_digest,
            details={
                "requestId": record.id,
                "target": record.target,
                "hostname": target["hostname"],
                "method": record.method,
                "path": payload["path"],
                "body": base64.b64decode(payload["body"]).decode("utf-8", "replace"),
                "headers": payload["headers"],
            },
        )


@router.get("/tools", response_model=ToolList)
def tools(svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return ToolList(items=svc.assistant.registry.specs("host_and_sandbox"))


@router.post("/sessions", response_model=Session, status_code=201)
def create(value: SessionCreate, svc: ServicesDep, token: SessionToken, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.create(value, token)


@router.get("/sessions", response_model=SessionPage)
def sessions(
    svc: ServicesDep,
    response: Response,
    limit: int = Query(30, ge=1, le=100),
    before: str | None = Query(None, max_length=120),
):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.list(limit, before)


@router.get("/sessions/{session_id}", response_model=Session)
def session(session_id: str, svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.get(session_id)


@router.get("/sessions/{session_id}/messages", response_model=MessagePage)
def messages(
    session_id: str,
    svc: ServicesDep,
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    after_sequence: int | None = Query(None, alias="afterSequence", ge=0),
    before_sequence: int | None = Query(None, alias="beforeSequence", ge=1),
):
    response.headers["Cache-Control"] = "no-store"
    if after_sequence is not None and before_sequence is not None:
        raise AppError(Code.INVALID_INPUT)
    return svc.session_service.messages(session_id, limit, after_sequence, before_sequence)


@router.post("/sessions/{session_id}/messages", response_model=MessageAccepted, status_code=202)
def send(
    session_id: str,
    value: UserMessageWrite,
    svc: ServicesDep,
    token: SessionToken,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.send(session_id, value, token)


@router.get("/sessions/{session_id}/runs/{run_id}", response_model=Run)
def run(session_id: str, run_id: str, svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.get_run(session_id, run_id)


@router.post("/sessions/{session_id}/runs/{run_id}/cancel", response_model=Run)
def cancel(session_id: str, run_id: str, svc: ServicesDep, token: SessionToken, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.cancel(session_id, run_id, token)


@router.post("/sessions/{session_id}/runs/{run_id}/retry", response_model=Run, status_code=202)
def retry(session_id: str, run_id: str, svc: ServicesDep, token: SessionToken, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.retry(session_id, run_id, token)


@router.post("/sessions/{session_id}/tool-calls/{call_id}/decision", response_model=Decision)
def decide(
    session_id: str,
    call_id: str,
    value: DecisionWrite,
    svc: ServicesDep,
    token: SessionToken,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return svc.session_service.decide(session_id, call_id, value, token)


@router.get("/agent-runtime", response_model=AgentRuntime)
async def runtime(svc: ServicesDep, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    await svc.bash.probe()
    pool = request.app.state.worker.pool
    slots = pool.occupied()
    return AgentRuntime(
        host_bash=HostBashStatus(
            enabled=svc.settings.host_bash_enabled and svc.settings.sandbox.host.enabled,
            available=svc.host.available,
            reason_code=None if svc.host.available else "HOST_UNAVAILABLE",
        ),
        pool=PoolStatus(
            capacity=pool.capacity,
            active_runs=len(pool.active),
            occupied_slots=len(slots),
            cleanup_slots=len(slots - set(pool.active)),
        ),
        bash=BashStatus(
            enabled=svc.settings.bash_enabled,
            available=svc.bash.available,
            reason_code=svc.bash.reason,
            network_policy=svc.settings.sandbox.network.mode,
            external_network=svc.settings.sandbox.network.mode == "proxy" and svc.bash.available,
        ),
    )
