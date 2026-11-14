import asyncio

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.dependencies import ServicesDep, authenticated
from app.api.schemas import (
    AccountOut,
    AccountPage,
    AddAccount,
    AnalyzeRequest,
    AppSettings,
    ConnectorOut,
    ErrorResponse,
    EventPage,
    EventType,
    Health,
    MockConfig,
    RunOut,
    RunPage,
    SessionOut,
    Snapshot,
    Status,
    SystemStatus,
    TaskOut,
    TaskPage,
    TaskStatus,
)
from app.core.errors import AppError, Code

errors = {
    status: {"model": ErrorResponse} for status in (400, 401, 403, 404, 409, 413, 422, 500, 503)
}
router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticated)], responses=errors)
auth_router = APIRouter(prefix="/api/v1/auth", responses=errors)


@auth_router.post("/session", response_model=SessionOut)
def session(
    svc: ServicesDep,
    response: Response,
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(Code.UNAUTHENTICATED)
    token, expires = svc.sessions.issue(authorization[7:])
    response.set_cookie(
        "tavo_session",
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=svc.settings.session_seconds,
    )
    response.headers["Cache-Control"] = "no-store"
    return SessionOut(access_token=token, expires_at=expires)


@router.get("/health", response_model=Health)
def health():
    return Health()


@router.get("/system/status", response_model=SystemStatus)
def system_status(svc: ServicesDep):
    return svc.status()


@router.get("/snapshot", response_model=Snapshot)
def snapshot(svc: ServicesDep):
    return svc.snapshot()


@router.get("/accounts", response_model=AccountPage)
def accounts(
    svc: ServicesDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Status | None = None,
    connector_id: str | None = Query(None, alias="connectorId", max_length=120),
):
    return svc.accounts.list(limit=limit, offset=offset, status=status, connector_id=connector_id)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def add_account(value: AddAccount, svc: ServicesDep):
    return svc.accounts.add(value)


@router.get("/accounts/{account_id}", response_model=AccountOut)
def account(account_id: str, svc: ServicesDep):
    return svc.accounts.get(account_id)


@router.delete("/accounts/{account_id}", status_code=204)
def remove_account(account_id: str, svc: ServicesDep):
    svc.accounts.disconnect(account_id, remove=True)


@router.post("/accounts/{account_id}/disconnect", response_model=AccountOut)
def disconnect(account_id: str, svc: ServicesDep):
    return svc.accounts.disconnect(account_id)


@router.post("/accounts/{account_id}/reconnect", response_model=AccountOut)
def reconnect(account_id: str, svc: ServicesDep):
    return svc.accounts.reconnect(account_id)


@router.patch("/accounts/{account_id}/mock", response_model=AccountOut)
def mock(account_id: str, value: MockConfig, svc: ServicesDep):
    return svc.accounts.configure(account_id, value)


@router.post("/accounts/{account_id}/sync", response_model=TaskOut, status_code=202)
def sync(account_id: str, svc: ServicesDep):
    return svc.accounts.enqueue(account_id)


@router.post("/accounts/{account_id}/sync/cancel", status_code=204)
def cancel(account_id: str, svc: ServicesDep):
    svc.accounts.cancel(account_id)


@router.get("/sync-tasks", response_model=TaskPage)
def tasks(
    svc: ServicesDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    account_id: str | None = Query(None, alias="accountId", max_length=120),
    status: TaskStatus | None = None,
):
    return svc.tasks(limit=limit, offset=offset, account_id=account_id, status=status)


@router.get("/sync-tasks/{task_id}", response_model=TaskOut)
def task(task_id: str, svc: ServicesDep):
    return svc.task(task_id)


@router.get("/connectors", response_model=list[ConnectorOut])
def connectors(svc: ServicesDep):
    return svc.registry.list()


@router.get("/connectors/{connector_id}/capabilities", response_model=ConnectorOut)
def capabilities(connector_id: str, svc: ServicesDep):
    return svc.registry.get(connector_id).info


@router.get("/settings", response_model=AppSettings)
def settings(svc: ServicesDep):
    return svc.accounts.settings()


@router.patch("/settings", response_model=AppSettings)
def update_settings(value: AppSettings, svc: ServicesDep):
    return svc.accounts.update_settings(value)


@router.get("/events", response_model=EventPage)
def events(
    svc: ServicesDep,
    limit: int = Query(50, ge=1, le=300),
    before: int | None = Query(None, ge=1),
    account_id: str | None = Query(None, alias="accountId", max_length=120),
    correlation_id: str | None = Query(None, alias="correlationId", max_length=120),
    event_type: EventType | None = Query(None, alias="eventType"),
):
    return svc.events.list(
        limit=limit,
        before=before,
        account_id=account_id,
        correlation_id=correlation_id,
        event_type=event_type,
    )


@router.get("/events/stream")
async def stream(
    request: Request,
    svc: ServicesDep,
    token: str = Depends(authenticated),
    after: int = Query(0, ge=0, le=9223372036854775807),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    try:
        resumed = int(last_event_id or 0)
        if resumed < 0 or resumed > 9223372036854775807:
            raise ValueError()
        sequence = max(after, resumed)
        if sequence < 0 or sequence > 9223372036854775807:
            raise ValueError()
    except ValueError:
        raise AppError(Code.INVALID_INPUT) from None

    async def generate():
        cursor = sequence
        while not await request.is_disconnected():
            try:
                svc.sessions.require(token)
            except AppError:
                yield 'event: session.expired\ndata: {"code":"UNAUTHENTICATED"}\n\n'
                return
            batch = svc.events.after(cursor)
            for event in batch:
                cursor = event.sequence
                yield f"id: {cursor}\nevent: business\ndata: {event.model_dump_json(by_alias=True)}\n\n"
            if not batch:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.1 if batch else 0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/analyze", response_model=RunOut, status_code=202)
def analyze(value: AnalyzeRequest, svc: ServicesDep):
    return svc.agent.enqueue(value.event_id)


@router.get("/agent/runs", response_model=RunPage)
def runs(svc: ServicesDep, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return svc.agent.list(limit=limit, offset=offset)


@router.get("/agent/runs/{run_id}", response_model=RunOut)
def run(run_id: str, svc: ServicesDep):
    return svc.agent.get(run_id)
