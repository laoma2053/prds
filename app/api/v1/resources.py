"""API v1 路由 - 资源搜索"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.core.database import get_db
from app.core.redis import get_redis
from app.schemas.resource import SearchAndDeliverRequest
from app.schemas.response import ok, fail
from app.services.resource_service import ResourceService

router = APIRouter(tags=["search"])


@router.post("/search")
async def search(
    req: SearchAndDeliverRequest,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
):
    """搜索并交付资源 - 核心接口

    流程: PanSou搜索 -> 按时间排序取最新N条 -> 账号池调度 -> 转存 -> 分享 -> 返回链接
    无账号配置时直接返回 PanSou 原始链接。
    """
    service = ResourceService(db, redis_client)
    try:
        result = await service.search_and_deliver(
            keyword=req.keyword,
            pan_type=req.pan_type,
            limit=req.limit,
            client_id=req.client_id,
        )
        return ok(data=result)
    except Exception as e:
        return fail("SEARCH_ERROR", str(e))
