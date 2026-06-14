import logging
import random
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # 并行环境下单次超时缩短，更快淘汰死链


class BaseLinkChecker(ABC):
    @abstractmethod
    async def check(self, url: str, pan_type: str, password: str = "") -> bool | None:
        """True=有效, False=失效, None=不确定/接口失败"""


class PanCheckChecker(BaseLinkChecker):
    """自建 PanCheck: POST /api/v1/links/check"""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def check(self, url: str, pan_type: str, password: str = "") -> bool | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/links/check",
                    json={"links": [url], "selectedPlatforms": [pan_type]},
                )
                resp.raise_for_status()
                data = resp.json()
            if url in data.get("valid_links", []):
                return True
            if url in data.get("invalid_links", []):
                return False
            return None
        except Exception as e:
            logger.debug(f"PanCheckChecker failed: {e}")
            return None


class PanSouChecker(BaseLinkChecker):
    """PanSou 内置检测: POST /api/check/links"""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def check(self, url: str, pan_type: str, password: str = "") -> bool | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/api/check/links",
                    json={"items": [{"disk_type": pan_type, "url": url, "password": password}]},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
            if not results:
                return None
            state = results[0].get("state", "uncertain")
            if state == "ok":
                return True
            if state == "bad":
                return False
            return None
        except Exception as e:
            logger.debug(f"PanSouChecker failed: {e}")
            return None


class ThirdPartyChecker(BaseLinkChecker):
    """116818 第三方检测: POST https://api.116818.xyz/api/pancheck_links"""

    async def check(self, url: str, pan_type: str, password: str = "") -> bool | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://api.116818.xyz/api/pancheck_links",
                    json={"links": [url], "selected_platforms": [pan_type]},
                )
                resp.raise_for_status()
                data = resp.json()
            if url in data.get("valid_links", []):
                return True
            if url in data.get("invalid_links", []):
                return False
            return None
        except Exception as e:
            logger.debug(f"ThirdPartyChecker failed: {e}")
            return None


class LinkCheckOrchestrator:
    """随机轮换 + 顺序降级。全部不确定/失败时返回 None（调用方应放行转存）"""

    def __init__(self, checkers: list[BaseLinkChecker]):
        self._checkers = checkers

    async def check(self, url: str, pan_type: str, password: str = "") -> bool | None:
        """随机顺序尝试所有检测器（单链接场景）"""
        order = self._checkers[:]
        random.shuffle(order)
        for checker in order:
            result = await checker.check(url, pan_type, password)
            if result is not None:
                return result
        return None

    async def check_indexed(self, url: str, pan_type: str, password: str, index: int) -> bool | None:
        """按 index 轮转分配首选检测器，分散并行请求压力（并行场景）"""
        n = len(self._checkers)
        order = [self._checkers[(index + i) % n] for i in range(n)]
        for checker in order:
            result = await checker.check(url, pan_type, password)
            if result is not None:
                return result
        return None


def build_orchestrator(pancheck_url: str, pansou_base_url: str) -> LinkCheckOrchestrator:
    return LinkCheckOrchestrator([
        PanCheckChecker(pancheck_url),
        PanSouChecker(pansou_base_url),
        ThirdPartyChecker(),
    ])
