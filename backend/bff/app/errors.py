"""统一异常格式：{code, message, detail}。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from eduagent_shared.logging import get_logger

log = get_logger("bff.errors")


class AppError(Exception):
    """业务错误：携带 HTTP 状态码与统一错误体。"""

    def __init__(
        self, status_code: int, code: str, message: str, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def error_body(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, exc.detail),
    )


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body("http_error", message, None if isinstance(exc.detail, str) else exc.detail),
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    try:
        detail = jsonable_encoder(exc.errors())
    except Exception:  # ctx 中可能含不可序列化对象
        detail = str(exc.errors())
    return JSONResponse(
        status_code=422,
        content=error_body("validation_error", "请求参数校验失败", detail),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_body("internal_error", "服务器内部错误"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册统一异常处理器。"""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
