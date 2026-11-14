from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.application import Services

bearer = HTTPBearer(auto_error=False, scheme_name="ClientSession")


def services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(services)]


def authenticated(
    request: Request,
    svc: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    token = (
        credentials.credentials
        if credentials and credentials.scheme.lower() == "bearer"
        else request.cookies.get("tavo_session")
    )
    svc.sessions.require(token)
    return token
