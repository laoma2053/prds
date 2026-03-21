"""FastAPI 应用入口"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.api.v1 import v1_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 - 启动/关闭时执行"""
    settings = get_settings()
    print(f"🚀 PRDS 启动 | 环境={settings.app_env} | 默认网盘={settings.default_pan_type}")
    yield
    from app.core.redis import redis_client
    await redis_client.aclose()
    print("👋 PRDS 关闭")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PRDS - 网盘资源交付中台",
        version="0.1.0",
        docs_url="/docs" if settings.app_debug else None,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(v1_router)

    # 管理后台页面
    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    # 挂载静态文件目录
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = create_app()
