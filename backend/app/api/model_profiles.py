from fastapi import APIRouter, Depends, Response

from app.api.dependencies import ServicesDep, SessionToken, authenticated
from app.api.schemas import ErrorResponse
from app.llm.contracts import ModelProfileCreate, ModelProfileList, ModelProfileOut, ProviderList

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(authenticated)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 409, 422, 503)},
)


@router.get("/model-providers", response_model=ProviderList)
def providers(svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.models.providers()


@router.get("/model-profiles", response_model=ModelProfileList)
def profiles(svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.models.list()


@router.post("/model-profiles", response_model=ModelProfileOut, status_code=201)
def create(value: ModelProfileCreate, svc: ServicesDep, token: SessionToken, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.models.create(value, lambda: svc.sessions.require(token))
