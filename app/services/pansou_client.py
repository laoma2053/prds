from __future__ import annotations

"""PanSou 搜索客户端

对接 PanSou (fish2018/pansou) 的 /api/search 接口。
PanSou 返回结构: {code: 0, message: "success", data: {total, merged_by_type, results}}
数据在 data 字段内，需要解包。
"""

import json
import httpx
from typing import Any

from app.core.config import get_settings


class PanSouClient:
    """PanSou HTTP 客户端"""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self._base_url = (base_url or settings.pansou_base_url).rstrip("/")
        self._timeout = timeout

    async def search(
        self,
        keyword: str,
        pan_type: str | None = None,
        refresh: bool = False,
    ) -> PanSouSearchResponse:
        """搜索资源"""
        params: dict[str, Any] = {
            "kw": keyword,
            "res": "all",
        }
        if refresh:
            params["refresh"] = True

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/api/search",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            try:
                raw = resp.json()
            except Exception:
                text = resp.content.decode("utf-8", errors="replace")
                raw = json.loads(text)

        # PanSou 返回 {code, message, data: {...}}，实际数据在 data 内
        if "data" in raw and isinstance(raw["data"], dict):
            data = raw["data"]
        else:
            data = raw

        response = PanSouSearchResponse.from_raw(data)

        if pan_type:
            response.filter_by_type(pan_type)

        return response

    async def health(self) -> bool:
        """PanSou 健康检查"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/api/health")
                return resp.status_code == 200
        except Exception:
            return False


class PanSouLink:
    """单条资源链接"""

    def __init__(self, pan_type: str, url: str, password: str | None = None, note: str = "", datetime_str: str = ""):
        self.pan_type = pan_type
        self.url = url
        self.password = password
        self.note = note
        self.datetime_str = datetime_str

    def to_dict(self) -> dict:
        return {
            "pan_type": self.pan_type,
            "url": self.url,
            "password": self.password,
            "note": self.note,
            "datetime": self.datetime_str,
        }


class PanSouResult:
    """单条搜索结果"""

    def __init__(self, unique_id: str, title: str, content: str, links: list[PanSouLink], datetime_str: str = ""):
        self.unique_id = unique_id
        self.title = title
        self.content = content
        self.links = links
        self.datetime_str = datetime_str

    @classmethod
    def from_raw(cls, raw: dict) -> PanSouResult:
        links = [
            PanSouLink(
                pan_type=lnk.get("type", "unknown"),
                url=lnk.get("url", ""),
                password=lnk.get("password"),
            )
            for lnk in raw.get("links", [])
        ]
        return cls(
            unique_id=raw.get("unique_id", ""),
            title=raw.get("title", ""),
            content=raw.get("content", ""),
            links=links,
            datetime_str=raw.get("datetime", ""),
        )


class PanSouSearchResponse:
    """PanSou 搜索响应"""

    def __init__(self, total: int, results: list[PanSouResult], merged_by_type: dict[str, list[PanSouLink]]):
        self.total = total
        self.results = results
        self.merged_by_type = merged_by_type

    @classmethod
    def from_raw(cls, data: dict) -> PanSouSearchResponse:
        results = [PanSouResult.from_raw(r) for r in data.get("results", [])]

        merged: dict[str, list[PanSouLink]] = {}
        for ptype, items in data.get("merged_by_type", {}).items():
            merged[ptype] = [
                PanSouLink(
                    pan_type=ptype,
                    url=item.get("url", ""),
                    password=item.get("password"),
                    note=item.get("note", ""),
                    datetime_str=item.get("datetime", ""),
                )
                for item in items
            ]

        return cls(total=data.get("total", 0), results=results, merged_by_type=merged)

    def filter_by_type(self, pan_type: str) -> None:
        """只保留指定网盘类型的结果"""
        self.merged_by_type = {k: v for k, v in self.merged_by_type.items() if k == pan_type}

        for result in self.results:
            result.links = [lnk for lnk in result.links if lnk.pan_type == pan_type]
        self.results = [r for r in self.results if r.links]
        self.total = len(self.results)

    def get_links_by_type(self, pan_type: str, limit: int | None = None) -> list[PanSouLink]:
        """获取指定类型的链接，按时间降序排列，取前 limit 条"""
        links = self.merged_by_type.get(pan_type, [])
        # 按 datetime 降序排序（最新在前），空日期排最后
        links.sort(key=lambda x: x.datetime_str or "0001", reverse=True)
        if limit:
            links = links[:limit]
        return links

    def all_links_flat(self) -> list[PanSouLink]:
        """获取所有链接（扁平列表）"""
        links = []
        for type_links in self.merged_by_type.values():
            links.extend(type_links)
        return links

    @property
    def available_types(self) -> list[str]:
        """当前结果中包含的网盘类型"""
        return [k for k, v in self.merged_by_type.items() if v]
