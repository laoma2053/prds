"""API v1 健康检查"""

from fastapi import APIRouter
from app.schemas.response import ok

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """健康检查端点"""
    return ok(data={"status": "healthy"})
