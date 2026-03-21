from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置，从环境变量加载"""

    # 应用
    app_env: str = "development"
    app_debug: bool = False
    app_secret_key: str = "change-me-in-production"

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "prds"
    postgres_password: str = "prds_secret"
    postgres_db: str = "prds"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # PanSou
    pansou_base_url: str = "http://localhost:8080"

    # Worker
    delete_delay_minutes: int = 20
    resource_ttl_minutes: int = 10

    # 缓存
    search_cache_ttl: int = 300  # 搜索结果缓存秒数（5分钟）
    resource_cache_ttl: int = 600  # 已转存资源缓存秒数（10分钟）

    # 默认网盘类型（用户未指定时使用）
    default_pan_type: str = "quark"

    # 搜索结果数量限制
    default_search_limit: int = 5  # 默认返回最新的前N条资源
    max_search_limit: int = 20  # 前端可请求的最大数量上限

    # 管理后台密码
    admin_password: str = "admin123"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
