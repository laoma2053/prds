"""网盘账号模型 - pan_accounts 表"""

from sqlalchemy import String, Integer, Float, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PanAccount(Base, TimestampMixin):
    """网盘账号池

    存储各类网盘的登录凭证与调度参数。
    账号池调度依据: cookie有效 → 空间足够 → 并发未超限 → health_score + weight 排序
    """

    __tablename__ = "pan_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pan_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True, comment="网盘类型: quark/baidu/aliyun")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="账号名称/备注")
    cookie: Mapped[str] = mapped_column(Text, nullable=False, comment="登录凭证 cookie")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    cookie_valid: Mapped[bool] = mapped_column(Boolean, default=True, comment="cookie 是否有效")
    total_space: Mapped[int] = mapped_column(Integer, default=0, comment="总空间(MB)")
    used_space: Mapped[int] = mapped_column(Integer, default=0, comment="已用空间(MB)")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=3, comment="最大并发数")
    health_score: Mapped[float] = mapped_column(Float, default=100.0, comment="健康评分 0-100")
    weight: Mapped[int] = mapped_column(Integer, default=1, comment="调度权重")
    save_folder_id: Mapped[str] = mapped_column(String(200), default="", comment="转存目标文件夹ID")
