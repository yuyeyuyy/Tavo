from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.dependencies import ServicesDep, authenticated
from app.api.schemas import ErrorResponse, Health, SessionOut, SystemStatus
from app.core.errors import AppError, Code

errors = {
    status: {"model": ErrorResponse}
    for status in (400, 401, 403, 404, 409, 410, 413, 422, 500, 503)
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
def status(svc: ServicesDep):
    return svc.status()


# Explicit retired boundaries; no account DTOs, queries, or background work remain.
@router.api_route(
    "/accounts/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
@router.api_route("/accounts", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/connectors/{path:path}", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/connectors", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/sync-tasks/{path:path}", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/sync-tasks", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/events/{path:path}", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/events", methods=["GET", "POST"], include_in_schema=False)
@router.api_route(
    "/agent/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False
)
@router.api_route("/snapshot", methods=["GET"], include_in_schema=False)
@router.api_route("/settings", methods=["GET", "PATCH"], include_in_schema=False)
def retired():
    raise AppError(Code.FEATURE_REMOVED)
