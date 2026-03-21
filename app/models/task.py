"""任务模型 - delete_tasks / request_logs / client_profiles 表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DeleteTask(Base, TimestampMixin):
    """删除任务表 - 记录待执行的资源删除"""

    __tablename__ = "delete_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="关联 resource_instances.id")
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="执行删除的账号ID")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="pending/processing/completed/failed")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="计划执行时间")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="实际执行时间")
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败原因")


class ClientProfile(Base, TimestampMixin):
    """客户端配置表 - 接入方信息与限流配置"""

    __tablename__ = "client_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="客户端唯一标识")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="客户端名称")
    api_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="API 密钥")
    rate_limit: Mapped[int] = mapped_column(Integer, default=60, comment="每分钟请求上限")
    is_active: Mapped[bool] = mapped_column(default=True, comment="是否启用")


class RequestLog(Base):
    """请求日志表"""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    pan_type: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, comment="success/failed")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="处理耗时(ms)")
    result_data: Mapped[dict | None] = mapped_column(JSON, comment="返回结果摘要")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
