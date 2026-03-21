"""Provider 基类 - 所有网盘 Provider 必须实现此接口

参考文档: docs/08_Provider抽象设计.md
实现: check_cookie / save_share / create_share / delete_resource
"""

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def generate_timestamp(length: int = 13) -> int:
    ts = str(time.time() * 1000)
    return int(ts[:length])


@dataclass
class SaveResult:
    """转存结果"""
    success: bool
    file_id: str = ""
    file_name: str = ""
    error: str = ""


@dataclass
class ShareResult:
    """分享结果"""
    success: bool
    share_url: str = ""
    share_password: str = ""
    share_id: str = ""
    error: str = ""


@dataclass
class DeleteResult:
    """删除结果"""
    success: bool
    error: str = ""


class BaseProvider(ABC):
    """网盘 Provider 基类

    所有网盘实现必须继承此类并实现四个核心方法。
    """

    pan_type: str = ""

    @abstractmethod
    async def check_cookie(self, cookie: str) -> bool:
        """验证 cookie 是否有效"""
        ...

    @abstractmethod
    async def save_share(self, share_url: str, cookie: str, save_folder_id: str = "0") -> SaveResult:
        """转存分享资源到自己的网盘"""
        ...

    @abstractmethod
    async def create_share(self, file_id: str, file_name: str, cookie: str) -> ShareResult:
        """为已转存的文件创建分享链接"""
        ...

    @abstractmethod
    async def delete_resource(self, file_id: str, cookie: str) -> DeleteResult:
        """删除网盘中的文件"""
        ...
