from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class SecurityMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        host = headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        permitted = {value.strip("[]") for value in self.settings.hosts}
        origin = headers.get("origin")
        same_origin = f"{scope.get('scheme', 'http')}://{host}"
        if hostname not in permitted or (
            origin and origin not in self.settings.origins and origin != same_origin
        ):
            return await JSONResponse(
                {"error": {"code": "FORBIDDEN", "message": "请求来源或权限不被允许。"}},
                status_code=403,
            )(scope, receive, send)
        try:
            size = int(headers.get("content-length", "0"))
        except ValueError:
            size = 32769
        if size > 32768:
            return await JSONResponse(
                {"error": {"code": "INVALID_INPUT", "message": "请求体过大。"}}, status_code=413
            )(scope, receive, send)
        # Buffer bounded JSON bodies to also enforce the limit without Content-Length.
        body = b""
        if scope["method"] in {"POST", "PATCH", "PUT"}:
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                body += chunk.get("body", b"")
                if len(body) > 32768:
                    return await JSONResponse(
                        {"error": {"code": "INVALID_INPUT", "message": "请求体过大。"}},
                        status_code=413,
                    )(scope, receive, send)
                if not chunk.get("more_body"):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, bounded_receive, send)
        return await self.app(scope, receive, send)
