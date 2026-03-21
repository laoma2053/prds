"""Provider 注册表 - 根据 pan_type 获取对应 Provider 实例"""

from app.providers.base import BaseProvider
from app.providers.quark import QuarkProvider

# 已注册的 Provider（后续新增 BaiduProvider / AliyunProvider 时在此注册）
_REGISTRY: dict[str, BaseProvider] = {
    "quark": QuarkProvider(),
}


def get_provider(pan_type: str) -> BaseProvider | None:
    """根据网盘类型获取 Provider，未注册返回 None"""
    return _REGISTRY.get(pan_type)


def registered_types() -> list[str]:
    """已注册的网盘类型列表"""
    return list(_REGISTRY.keys())
