"""全局异常处理器"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.schemas.response import fail


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content=fail("BAD_REQUEST", str(exc)))

    @app.exception_handler(Exception)
    async def generic_error_handler(_: Request, exc: Exception):
        return JSONResponse(status_code=500, content=fail("INTERNAL_ERROR", "服务内部错误"))
