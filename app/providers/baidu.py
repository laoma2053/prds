"""百度网盘 Provider

参考实现: https://github.com/675061370/xinyue-search/tree/main/extend/netdisk/pan

API 端点:
- Cookie验证:  GET  https://pan.baidu.com/api/gettemplatevariable
- 分享信息:    GET  https://pan.baidu.com/api/shorturlinfo
- 提取码验证:  POST https://pan.baidu.com/share/verify
- 文件列表:    GET  https://pan.baidu.com/share/list
- 创建目录:    POST https://pan.baidu.com/api/create
- 转存文件:    POST https://pan.baidu.com/share/transfer
- 创建分享:    POST https://pan.baidu.com/share/set
- 删除文件:    POST https://pan.baidu.com/api/filemanager

注意:
- POST 接口均使用 form-encoded（非 JSON）
- file_id 存储文件路径（非fid），供 delete_resource 使用
- 分享固定密码 6666
"""

import asyncio
import json
import logging
import re
import time
import urllib.parse

import httpx

from app.providers.base import BaseProvider, SaveResult, ShareResult, DeleteResult

logger = logging.getLogger(__name__)

BASE_URL = "https://pan.baidu.com"
APP_ID = "250528"
SHARE_PWD = "6666"
REQUEST_TIMEOUT = 20.0

_SHORT_URL_RE = re.compile(r"/s/([A-Za-z0-9_-]+)")


def _build_headers(cookie: str) -> dict:
    return {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://pan.baidu.com/disk/home",
        "Accept": "application/json, text/plain, */*",
    }


def _form_headers(cookie: str) -> dict:
    return {**_build_headers(cookie), "Content-Type": "application/x-www-form-urlencoded"}


def _extract_shorturl(share_url: str) -> str:
    m = _SHORT_URL_RE.search(share_url)
    return m.group(1) if m else ""


def _extract_pwd_from_url(share_url: str) -> str:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(share_url).query)
    return qs.get("pwd", [""])[0]


def _inject_bdclnd(cookie: str, randsk: str) -> str:
    """将 randsk 注入 Cookie 的 BDCLND 字段"""
    encoded = urllib.parse.quote(randsk, safe="")
    if "BDCLND=" in cookie:
        return re.sub(r"BDCLND=[^;]*", f"BDCLND={encoded}", cookie)
    return cookie + f"; BDCLND={encoded}"


def _logid() -> str:
    return str(int(time.time() * 1000))


class BaiduProvider(BaseProvider):
    """百度网盘 Provider"""

    pan_type = "baidu"

    async def check_cookie(self, cookie: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{BASE_URL}/api/gettemplatevariable",
                    params={"fields": '["bdstoken"]', "clienttype": "0", "app_id": APP_ID},
                    headers=_build_headers(cookie),
                )
                data = resp.json()
                valid = data.get("errno") == 0 and bool(data.get("result", {}).get("bdstoken"))
                logger.info(f"🔑 百度 Cookie 验证: {'有效' if valid else '无效'}")
                return valid
        except Exception as e:
            logger.warning(f"⚠️ 百度 Cookie 验证异常: {e}")
            return False

    async def save_share(self, share_url: str, cookie: str, save_folder_id: str = "/来自搜索站") -> SaveResult:
        shorturl = _extract_shorturl(share_url)
        if not shorturl:
            return SaveResult(success=False, error="无法解析百度分享链接")
        pwd = _extract_pwd_from_url(share_url)

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                headers = _build_headers(cookie)

                # 1. 获取 bdstoken
                bdstoken = await self._get_bdstoken(client, headers)
                if not bdstoken:
                    return SaveResult(success=False, error="获取 bdstoken 失败，Cookie 可能已失效")
                logger.info("🔑 [百度转存] 1/5 bdstoken 获取成功")

                # 2. 获取分享的 shareid + uk
                share_info = await self._get_share_info(client, headers, shorturl)
                if not share_info:
                    return SaveResult(success=False, error="获取分享信息失败，链接可能已失效")
                shareid, uk = share_info["shareid"], share_info["uk"]
                logger.info(f"📎 [百度转存] 2/5 shareid={shareid} uk={uk}")

                # 3. 有提取码则验证并更新 BDCLND
                if pwd:
                    randsk = await self._verify_pwd(client, headers, shareid, pwd)
                    if randsk:
                        cookie = _inject_bdclnd(cookie, randsk)
                        headers = _build_headers(cookie)

                # 4. 获取文件列表（fsids）
                fsids, file_name = await self._get_fsids(client, headers, shareid, uk, pwd)
                if not fsids:
                    return SaveResult(success=False, error="获取分享文件列表失败")
                logger.info(f"📂 [百度转存] 3/5 文件: {file_name}, fsids: {fsids[:3]}")

                # 5. 确保目标目录存在，执行转存
                await self._ensure_dir(client, headers, bdstoken, save_folder_id)
                ok = await self._transfer(client, headers, bdstoken, shareid, uk, fsids, save_folder_id)
                if not ok:
                    return SaveResult(success=False, error="转存失败，可能超出空间限制或频率限制")
                logger.info(f"💾 [百度转存] 4/5 转存任务已提交")

                # 6. 等待转存完成，查找目标文件路径
                await asyncio.sleep(2)
                saved_path = await self._find_latest_file(client, headers, save_folder_id)
                if not saved_path:
                    saved_path = f"{save_folder_id.rstrip('/')}/{file_name}"

                logger.info(f"✅ [百度转存] 5/5 完成: {saved_path}")
                return SaveResult(success=True, file_id=saved_path, file_name=file_name)

        except Exception as e:
            logger.error(f"💥 百度转存异常: {e}")
            return SaveResult(success=False, error=str(e))

    async def create_share(self, file_id: str, file_name: str, cookie: str) -> ShareResult:
        """创建百度分享，固定密码 6666"""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                headers = _build_headers(cookie)
                bdstoken = await self._get_bdstoken(client, headers)
                if not bdstoken:
                    return ShareResult(success=False, error="获取 bdstoken 失败")

                resp = await client.post(
                    f"{BASE_URL}/share/set",
                    params={"channel": "chunlei", "web": "1", "app_id": APP_ID,
                            "bdstoken": bdstoken, "logid": _logid()},
                    data={"schannel": "3", "channel_list": "[]", "period": "0",
                          "pwd": SHARE_PWD, "path_list": json.dumps([file_id])},
                    headers=_form_headers(cookie),
                )
                data = resp.json()
                if data.get("errno") != 0:
                    return ShareResult(success=False, error=f"创建分享失败: errno={data.get('errno')}")

                shorturl = data.get("shorturl", "")
                if not shorturl:
                    return ShareResult(success=False, error="未获取到分享 shorturl")

                share_url = f"https://pan.baidu.com/s/{shorturl}?pwd={SHARE_PWD}"
                logger.info(f"✅ 百度分享创建成功: {share_url}")
                return ShareResult(success=True, share_url=share_url, share_password=SHARE_PWD)

        except Exception as e:
            logger.error(f"💥 百度创建分享异常: {e}")
            return ShareResult(success=False, error=str(e))

    async def delete_resource(self, file_id: str, cookie: str) -> DeleteResult:
        """删除文件，file_id 为文件路径字符串"""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                headers = _build_headers(cookie)
                bdstoken = await self._get_bdstoken(client, headers)
                if not bdstoken:
                    return DeleteResult(success=False, error="获取 bdstoken 失败")

                resp = await client.post(
                    f"{BASE_URL}/api/filemanager",
                    params={"opera": "delete", "async": "2", "channel": "chunlei",
                            "web": "1", "app_id": APP_ID, "bdstoken": bdstoken, "logid": _logid()},
                    data={"filelist": json.dumps([file_id])},
                    headers=_form_headers(cookie),
                )
                data = resp.json()
                if data.get("errno") != 0:
                    return DeleteResult(success=False, error=f"删除失败: errno={data.get('errno')}")

                logger.info(f"🗑️ 百度删除成功: {file_id}")
                return DeleteResult(success=True)

        except Exception as e:
            logger.error(f"💥 百度删除异常: {e}")
            return DeleteResult(success=False, error=str(e))

    # ── 内部方法 ──────────────────────────────────────────

    async def _get_bdstoken(self, client: httpx.AsyncClient, headers: dict) -> str:
        resp = await client.get(
            f"{BASE_URL}/api/gettemplatevariable",
            params={"fields": '["bdstoken"]', "clienttype": "0", "app_id": APP_ID},
            headers=headers,
        )
        return resp.json().get("result", {}).get("bdstoken", "")

    async def _get_share_info(self, client: httpx.AsyncClient, headers: dict, shorturl: str) -> dict | None:
        resp = await client.get(
            f"{BASE_URL}/api/shorturlinfo",
            params={"shorturl": shorturl, "clienttype": "0", "app_id": APP_ID},
            headers=headers,
        )
        data = resp.json()
        if data.get("errno") != 0:
            return None
        return {"shareid": str(data.get("shareid", "")), "uk": str(data.get("uk", ""))}

    async def _verify_pwd(self, client: httpx.AsyncClient, headers: dict, shareid: str, pwd: str) -> str:
        """验证提取码，返回 randsk（用于设置 BDCLND）"""
        resp = await client.post(
            f"{BASE_URL}/share/verify",
            params={"clienttype": "0", "app_id": APP_ID},
            data={"shareid": shareid, "pwd": pwd, "t": shareid, "channel_list": "[]"},
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        return resp.json().get("randsk", "")

    async def _get_fsids(self, client: httpx.AsyncClient, headers: dict, shareid: str, uk: str, pwd: str) -> tuple[list, str]:
        params = {"shareid": shareid, "uk": uk, "page": "1", "num": "100",
                  "order": "other", "desc": "1", "clienttype": "0", "app_id": APP_ID}
        if pwd:
            params["pwd"] = pwd
        resp = await client.get(f"{BASE_URL}/share/list", params=params, headers=headers)
        data = resp.json()
        if data.get("errno") != 0:
            return [], ""
        file_list = data.get("list", [])
        if not file_list:
            return [], ""
        fsids = [str(f["fs_id"]) for f in file_list if f.get("fs_id")]
        file_name = file_list[0].get("server_filename", "")
        return fsids, file_name

    async def _ensure_dir(self, client: httpx.AsyncClient, headers: dict, bdstoken: str, path: str) -> None:
        await client.post(
            f"{BASE_URL}/api/create",
            params={"a": "commit", "channel": "chunlei", "web": "1",
                    "app_id": APP_ID, "bdstoken": bdstoken},
            data={"path": path, "isdir": "1", "block_list": "[]"},
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )

    async def _transfer(self, client: httpx.AsyncClient, headers: dict, bdstoken: str,
                        shareid: str, uk: str, fsids: list, save_dir: str) -> bool:
        resp = await client.post(
            f"{BASE_URL}/share/transfer",
            params={"shareid": shareid, "from": uk, "ondup": "newcopy", "async": "1",
                    "channel": "chunlei", "web": "1", "app_id": APP_ID,
                    "bdstoken": bdstoken, "logid": _logid()},
            data={"fsidlist": json.dumps([int(f) for f in fsids]), "path": save_dir},
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        data = resp.json()
        errno = data.get("errno", -1)
        if errno != 0:
            logger.error(f"❌ 百度转存失败: errno={errno}")
        return errno == 0

    async def _find_latest_file(self, client: httpx.AsyncClient, headers: dict, dir_path: str) -> str:
        """查询目标目录，返回最新修改的文件路径"""
        resp = await client.get(
            f"{BASE_URL}/api/list",
            params={"dir": dir_path, "order": "time", "desc": "1", "start": "0",
                    "limit": "5", "clienttype": "0", "app_id": APP_ID},
            headers=headers,
        )
        data = resp.json()
        files = data.get("list", [])
        return files[0].get("path", "") if files else ""

    async def list_folders(self, cookie: str, parent_id: str = "/") -> list[dict]:
        """列出百度网盘文件夹"""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{BASE_URL}/api/list",
                    params={"dir": parent_id, "order": "name", "start": "0",
                            "limit": "100", "clienttype": "0", "app_id": APP_ID},
                    headers=_build_headers(cookie),
                )
                data = resp.json()
                if data.get("errno") != 0:
                    return []
                return [
                    {"id": f["path"], "name": f["server_filename"]}
                    for f in data.get("list", [])
                    if f.get("isdir") == 1
                ]
        except Exception as e:
            logger.warning(f"⚠️ 百度获取文件夹列表失败: {e}")
            return []
