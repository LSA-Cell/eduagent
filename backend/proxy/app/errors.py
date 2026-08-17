"""代理错误：与推理网关一致的标准 OpenAI 错误格式 {"error": {message, type, code}}。

代理的 4xx 响应体会被推理网关原样透传给调用方，因此错误格式保持 OpenAI 约定，
且任何错误消息都不得包含 API Key 明文。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from eduagent_shared.logging import get_logger

log = get_logger("proxy.errors")


class ProxyError(Exception):
    """业务错误：message 对外可见，type/code 遵循 OpenAI 错误约定。"""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_type: str = "server_error",
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.code = code


def openai_error_body(
    message: str, error_type: str = "server_error", code: str | None = None
) -> dict:
    return {"error": {"message": message, "type": error_type, "code": code}}


async def _proxy_error_handler(request: Request, exc: ProxyError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=openai_error_body(exc.message, exc.error_type, exc.code),
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=openai_error_body(
            f"请求参数不合法: {exc.errors()}", "invalid_request_error", "validation_error"
        ),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content=openai_error_body("代理内部错误", "server_error", "internal_error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProxyError, _proxy_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
