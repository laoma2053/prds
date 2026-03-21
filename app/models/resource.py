"""资源模型 - resource_assets / resource_instances 表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ResourceAsset(Base, TimestampMixin):
    """资源主表 - 代表一个搜索到的原始资源

    通过 resource_key (keyword + 原始链接 hash) 去重。
    一个 asset 可产生多个 instance (不同网盘账号的转存副本)。
    """

    __tablename__ = "resource_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="资源唯一标识(hash)")
    keyword: Mapped[str] = mapped_column(String(200), nullable=False, comment="搜索关键词")
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="资源标题")
    original_url: Mapped[str] = mapped_column(Text, nullable=False, comment="PanSou 返回的原始链接")
    pan_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="网盘类型")
    file_size: Mapped[int] = mapped_column(Integer, default=0, comment="文件大小(bytes)")

    instances: Mapped[list["ResourceInstance"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class ResourceInstance(Base, TimestampMixin):
    """资源实例 - 某个账号对资源的一次转存/分享

    生命周期: 创建 → 分享 → TTL到期 → 删除
    """

    __tablename__ = "resource_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("resource_assets.id"), nullable=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("pan_accounts.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False,
        comment="状态: pending/saving/saved/sharing/shared/deleting/deleted/failed"
    )
    saved_file_id: Mapped[str | None] = mapped_column(String(200), comment="网盘中转存后的文件ID")
    share_url: Mapped[str | None] = mapped_column(Text, comment="生成的分享链接")
    share_password: Mapped[str | None] = mapped_column(String(20), comment="分享密码")
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="分享过期时间")
    delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="计划删除时间")
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败原因")

    asset: Mapped["ResourceAsset"] = relationship(back_populates="instances")
