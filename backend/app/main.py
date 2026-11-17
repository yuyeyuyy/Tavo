import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.credentials import router as credentials_router
from app.api.dependencies import ServicesDep, authenticated
from app.api.model_profiles import router as model_profiles_router
from app.api.routes import auth_router, router
from app.api.sessions import router as sessions_router
from app.core.config import Settings
from app.core.errors import MESSAGES, AppError, Code, status_code
from app.core.http_security import SecurityMiddleware
from app.core.startup import report_startup
from app.services.application import Services
from app.workers.scheduler import Scheduler


# 创建app实例
def create_app(settings: Settings | None = None, services_factory=Services):
    # 获取配置，如果没有提供，则从环境变量中加载
    config = settings or Settings.from_env()
    """
    创建应用生命周期
    """

    @asynccontextmanager
    async def lifespan(app):
        svc = services_factory(config)
        worker = None
        try:
            await svc.initialize()
            worker = Scheduler(svc)
            app.state.services, app.state.worker = svc, worker
            if config.worker_enabled:
                worker.start()
            report_startup("后端初始化完成，正在启动 HTTP 监听。")
            yield
        except AppError as error:
            if worker is None:
                logging.getLogger("uvicorn.error").error("后端启动已停止：%s", MESSAGES[error.code])
            raise
        finally:
            try:
                if worker is not None:
                    await worker.close()
            finally:
                await svc.close()

    app = FastAPI(
        title="Tavo API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SecurityMiddleware, settings=config)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(AppError)
    async def application_error(_request, error):
        return JSONResponse(
            {"error": {"code": error.code, "message": MESSAGES[error.code]}},
            status_code=status_code(error.code),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        # FastAPI's default error body can echo input values. Never include it.
        return JSONResponse(
            {"error": {"code": Code.INVALID_INPUT, "message": MESSAGES[Code.INVALID_INPUT]}},
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        code = (
            Code.UNAUTHENTICATED
            if error.status_code == 401
            else Code.NOT_FOUND
            if error.status_code == 404
            else Code.INVALID_INPUT
        )
        return JSONResponse(
            {"error": {"code": code, "message": MESSAGES[code]}},
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _error):
        return JSONResponse(
            {"error": {"code": Code.INTERNAL, "message": MESSAGES[Code.INTERNAL]}}, status_code=500
        )

    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(chat_router)
    app.include_router(credentials_router)
    app.include_router(model_profiles_router)
    app.include_router(sessions_router)
    basic = HTTPBasic(auto_error=False)

    @app.get("/docs", include_in_schema=False)
    def docs(
        request: Request,
        svc: ServicesDep,
        credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    ):
        token = request.cookies.get("tavo_session")
        try:
            svc.sessions.require(token)
        except AppError:
            if not credentials or credentials.username != "owner":
                raise HTTPException(
                    401, headers={"WWW-Authenticate": 'Basic realm="Tavo local API docs"'}
                ) from None
            token, _expires = svc.sessions.issue(credentials.password)
        response = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="Tavo API",
            swagger_js_url="/_docs/swagger-ui-bundle.js",
            swagger_css_url="/_docs/swagger-ui.css",
            swagger_favicon_url="",
        )
        response.set_cookie(
            "tavo_session",
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=config.session_seconds,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(authenticated)])
    def openapi():
        return app.openapi()

    @app.get("/_docs/{filename}", include_in_schema=False, dependencies=[Depends(authenticated)])
    def docs_asset(filename: str):
        if filename not in {"swagger-ui-bundle.js", "swagger-ui.css"}:
            raise AppError(Code.NOT_FOUND)
        return FileResponse(Path(__file__).parent / "static" / filename)

    return app
