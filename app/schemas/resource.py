"""请求/响应 Schema - 资源搜索与交付"""

from pydantic import BaseModel, Field


class SearchAndDeliverRequest(BaseModel):
    """搜索并交付请求"""

    keyword: str = Field(..., min_length=1, max_length=200, description="搜索关键词")
    pan_type: str | None = Field(None, description="指定网盘类型: quark/baidu/aliyun，为空则默认夸克")
    limit: int | None = Field(None, ge=1, le=20, description="返回资源数量，不传则使用系统默认值(5)")
    client_id: str = Field("default", description="调用方标识")


class TaskStatusResponse(BaseModel):
    """任务状态"""

    task_id: str
    status: str  # pending / processing / completed / failed
    result: dict | None = None
    error: str | None = None
