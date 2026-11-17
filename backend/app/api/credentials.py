from fastapi import APIRouter, Request, Response

from app.api.dependencies import ServicesDep, SessionToken
from app.api.schemas import ErrorResponse
from app.credentials.contracts import (
    ChallengeOut,
    ChallengeRequest,
    SecretSubmission,
    SubmissionAck,
)

router = APIRouter(
    prefix="/api/v1/secret-submissions",
    responses={s: {"model": ErrorResponse} for s in (401, 403, 409, 413, 422, 503)},
)


@router.post("/challenges", response_model=ChallengeOut)
async def challenge(
    value: ChallengeRequest, svc: ServicesDep, token: SessionToken, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await svc.secret_submissions.issue(value, token)


@router.post("", response_model=SubmissionAck)
async def submit(
    value: SecretSubmission,
    svc: ServicesDep,
    token: SessionToken,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return await svc.secret_submissions.submit(value, token, request.is_disconnected)
