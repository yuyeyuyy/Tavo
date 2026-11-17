from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import ServicesDep, authenticated
from app.api.schemas import ErrorResponse
from app.llm.contracts import ChatReply, ChatRequest, ModelCatalog

router = APIRouter(
    prefix="/api/v1/chat",
    dependencies=[Depends(authenticated)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 409, 422, 429, 502, 503, 504)},
)


@router.get("/models", response_model=ModelCatalog)
def models(svc: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return svc.chat.catalog()


@router.post("/completions", response_model=ChatReply)
async def completion(value: ChatRequest, svc: ServicesDep, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await svc.chat.complete(value, request.is_disconnected)
