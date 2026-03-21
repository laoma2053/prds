"""API v1 路由汇总"""

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.resources import router as search_router
from app.api.v1.admin import router as admin_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health_router)
v1_router.include_router(search_router)
v1_router.include_router(admin_router)
