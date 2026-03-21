"""统一响应模型 - 所有 API 接口使用统一返回结构"""

from typing import Any
from pydantic import BaseModel, Field
from uuid import uuid4


class ResponseModel(BaseModel):
    """统一返回结构: {success, code, message, request_id, data}"""

    success: bool = True
    code: str = "OK"
    message: str = "success"
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    data: Any = None


def ok(data: Any = None, message: str = "success") -> dict:
    return ResponseModel(success=True, code="OK", message=message, data=data).model_dump()


def fail(code: str, message: str, data: Any = None) -> dict:
    return ResponseModel(success=False, code=code, message=message, data=data).model_dump()
