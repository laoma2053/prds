"""百度网盘 Provider

参考实现:
- BaiduWork.php  (675061370/xinyue-search)
- pcs.py         (PeterDing/BaiduPCS-Py) — baidupcs-py 库核心实现

核心流程 (对齐 baidupcs-py):
1. 用户 bdstoken       → gettemplatevariable（用于 createShare / deleteFile）
2. verifyPassCode      → POST /share/verify, surl = shorturl去掉开头的"1"
3. GET 分享页 HTML     → 解析 yunData.setData JSON，提取 shareid / uk / share_bdstoken
4. list_shared_paths   → GET /share/list, bdstoken=null（字面量）, dir, 随机 t
5. transferFile        → POST /share/transfer, 用 share_bdstoken，正确 Headers
6. getDirList          → GET /api/list, 找转存后文件的 fs_id
7. createShare         → POST /share/set, fid_list=[fs_id], 用用户 bdstoken

关键：分享页的 bdstoken ≠ 用户自己的 bdstoken，两者用途不同
"""

import asyncio
import json
import logging
import random
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Referer": "https://pan.baidu.com",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }


def _form_headers(cookie: str) -> dict:
    return {**_build_headers(cookie), "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}


def _page_headers(cookie: str) -> dict:
    return {
        **_build_headers(cookie),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }


def _extract_shorturl(share_url: str) -> str:
    m = _SHORT_URL_RE.search(share_url)
    return m.group(1) if m else ""


def _extract_surl(shorturl: str) -> str:
    """surl = shorturl 去掉开头的 '1'（baidupcs-py 和 PHP 参考实现均如此）"""
    return shorturl[1:] if shorturl.startswith("1") else shorturl


def _extract_pwd_from_url(share_url: str) -> str:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(share_url).query)
    return qs.get("pwd", [""])[0]


def _strip_query(share_url: str) -> str:
    p = urllib.parse.urlparse(share_url)
    return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _inject_bdclnd(cookie: str, randsk: str) -> str:
    encoded = urllib.parse.quote(randsk, safe="")
    if "BDCLND=" in cookie:
        return re.sub(r"BDCLND=[^;]*", f"BDCLND={encoded}", cookie)
    return cookie + f"; BDCLND={encoded}"


def _logid() -> str:
    return str(int(time.time() * 1000))


class BaiduProvider(BaseProvider):
    pan_type = "baidu"

    async def check_cookie(self, cookie: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{BASE_URL}/api/gettemplatevariable",
                    params={"fields": '["bdstoken"]', "clienttype": "0", "app_id": APP_ID, "web": "1"},
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
        bare_url = _strip_query(share_url)
        logger.info(f"🔍 [百度] shorturl={shorturl}, pwd={'***' if pwd else '(空)'}")

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                headers = _build_headers(cookie)

                # 1. 用户自己的 bdstoken（用于 createShare / deleteFile）
                user_bdstoken = await self._get_user_bdstoken(client, headers)
                if not user_bdstoken:
                    return SaveResult(success=False, error="获取 bdstoken 失败，Cookie 可能已失效")

                # 2. 有提取码先验证（surl = shorturl 去掉开头的"1"）
                if pwd:
                    randsk = await self._verify_pwd(client, headers, shorturl, pwd)
                    if randsk:
                        cookie = _inject_bdclnd(cookie, randsk)
                        headers = _build_headers(cookie)
                        logger.info("🔓 提取码验证成功")

                # 3. GET 分享页 HTML → 解析 shareid / uk / share_bdstoken
                page_info = await self._parse_share_page(client, cookie, bare_url)
                if not page_info:
                    return SaveResult(success=False, error="解析分享页面失败，链接可能已失效")
                shareid = page_info["shareid"]
                uk = page_info["uk"]
                share_bdstoken = page_info["bdstoken"]
                fsids = page_info["fsids"]
                file_name = page_info["file_name"]
                logger.info(f"📎 shareid={shareid}, fsids数量={len(fsids)}, file={file_name}")

                if not fsids:
                    return SaveResult(success=False, error="分享页未找到文件，链接可能已失效或需要提取码")

                # 5. 转存（bdstoken 用用户自己的，对齐 PHP 参考实现）
                await self._ensure_dir(client, headers, user_bdstoken, save_folder_id)
                ok = await self._transfer(client, cookie, shareid, uk, fsids, save_folder_id, user_bdstoken, bare_url)
                if not ok:
                    return SaveResult(success=False, error="转存失败，可能超出空间或频率限制")

                # 6. 获取转存后文件的 fs_id
                await asyncio.sleep(2)
                saved = await self._find_latest_file(client, headers, user_bdstoken, save_folder_id)
                if not saved:
                    return SaveResult(success=False, error="转存后未找到文件")

                file_id = f"{saved['path']}|{saved['fs_id']}"
                logger.info(f"✅ 百度转存完成: {saved['path']}")
                return SaveResult(success=True, file_id=file_id, file_name=file_name)

        except Exception as e:
            logger.error(f"💥 百度转存异常: {e}")
            return SaveResult(success=False, error=str(e))

    async def create_share(self, file_id: str, file_name: str, cookie: str) -> ShareResult:
        """file_id 格式: 'path|fs_id'"""
        try:
            fs_id = file_id.split("|")[1] if "|" in file_id else ""
            if not fs_id:
                return ShareResult(success=False, error="无法提取 fs_id")

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                headers = _build_headers(cookie)
                bdstoken = await self._get_user_bdstoken(client, headers)
                if not bdstoken:
                    return ShareResult(success=False, error="获取 bdstoken 失败")

                resp = await client.post(
                    f"{BASE_URL}/share/set",
                    params={"channel": "chunlei", "bdstoken": bdstoken,
                            "clienttype": "0", "app_id": APP_ID, "web": "1"},
                    data={"period": "0", "pwd": SHARE_PWD, "eflag_disable": "true",
                          "channel_list": "[]", "schannel": "4",
                          "fid_list": f"[{fs_id}]"},
                    headers=_form_headers(cookie),
                )
                data = resp.json()
                if data.get("errno") != 0:
                    return ShareResult(success=False, error=f"创建分享失败: errno={data.get('errno')}")

                link = data.get("link", "") or f"https://pan.baidu.com/s/{data.get('shorturl', '')}"
                share_url = f"{link}?pwd={SHARE_PWD}" if link else ""
                if not share_url:
                    return ShareResult(success=False, error="未获取到分享链接")

                logger.info(f"✅ 百度分享创建成功: {share_url}")
                return ShareResult(success=True, share_url=share_url, share_password=SHARE_PWD)

        except Exception as e:
            logger.error(f"💥 百度创建分享异常: {e}")
            return ShareResult(success=False, error=str(e))

    async def delete_resource(self, file_id: str, cookie: str) -> DeleteResult:
        """file_id 格式: 'path|fs_id'，删除用路径"""
        try:
            path = file_id.split("|")[0] if "|" in file_id else file_id
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                headers = _build_headers(cookie)
                bdstoken = await self._get_user_bdstoken(client, headers)
                if not bdstoken:
                    return DeleteResult(success=False, error="获取 bdstoken 失败")

                resp = await client.post(
                    f"{BASE_URL}/api/filemanager",
                    params={"async": "2", "onnest": "fail", "opera": "delete",
                            "bdstoken": bdstoken, "newVerify": "1",
                            "clienttype": "0", "app_id": APP_ID, "web": "1"},
                    data={"filelist": json.dumps([path])},
                    headers=_form_headers(cookie),
                )
                data = resp.json()
                if data.get("errno") != 0:
                    return DeleteResult(success=False, error=f"删除失败: errno={data.get('errno')}")
                logger.info(f"🗑️ 百度删除成功: {path}")
                return DeleteResult(success=True)

        except Exception as e:
            logger.error(f"💥 百度删除异常: {e}")
            return DeleteResult(success=False, error=str(e))

    # ── 内部方法 ──────────────────────────────────────────

    async def _get_user_bdstoken(self, client: httpx.AsyncClient, headers: dict) -> str:
        resp = await client.get(
            f"{BASE_URL}/api/gettemplatevariable",
            params={"fields": '["bdstoken"]', "clienttype": "0", "app_id": APP_ID, "web": "1"},
            headers=headers,
        )
        return resp.json().get("result", {}).get("bdstoken", "")

    async def _verify_pwd(self, client: httpx.AsyncClient, headers: dict,
                          shorturl: str, pwd: str) -> str:
        """surl = shorturl 去掉开头的 '1'（baidupcs-py: share/init?surl=<surl>）"""
        surl = _extract_surl(shorturl)
        resp = await client.post(
            f"{BASE_URL}/share/verify",
            params={"surl": surl, "t": _logid(), "channel": "chunlei",
                    "web": "1", "bdstoken": "null", "clienttype": "0"},
            data={"pwd": pwd, "vcode": "", "vcode_str": ""},
            headers=_form_headers(headers.get("Cookie", "")),
        )
        data = resp.json()
        logger.info(f"verify_pwd: errno={data.get('errno')}, randsk={'有' if data.get('randsk') else '无'}")
        return data.get("randsk", "")

    async def _parse_share_page(self, client: httpx.AsyncClient, cookie: str,
                                 bare_url: str) -> dict | None:
        """GET 分享页，解析 yunData.setData / locals.mset JSON（baidupcs-py 同款）
        同时提取 shareid / uk / bdstoken / fsids / file_name，无需额外 API 调用
        """
        resp = await client.get(bare_url, headers=_page_headers(cookie))
        html = resp.text

        page_data: dict = {}
        for pattern in [r'yunData\.setData\((\{.+?\})\)', r'locals\.mset\((\{.+?\})\)']:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    page_data = json.loads(m.group(1))
                    break
                except json.JSONDecodeError:
                    pass

        shareid = str(page_data.get("shareid", "") or "")
        uk = str(page_data.get("uk", "") or "")
        bdstoken = str(page_data.get("bdstoken", "") or "")

        # 降级：regex（PHP parseResponse 风格）
        if not shareid:
            m = re.search(r'"shareid"\s*:\s*"?(\d+)', html)
            shareid = m.group(1) if m else ""
        if not uk:
            m = re.search(r'"share_uk"\s*:\s*"?(\d+)', html)
            uk = m.group(1) if m else ""
        if not bdstoken:
            m = re.search(r'"bdstoken"\s*:\s*"([^"]+)"', html)
            bdstoken = m.group(1) if m else "null"

        if not shareid or not uk:
            logger.error(f"❌ 分享页解析失败: HTTP={resp.status_code}, len={len(html)}")
            logger.debug(f"页面片段: {html[:800]}")
            return None

        # 从 yunData JSON 提取文件列表（baidupcs-py: shared_paths 同时返回 fs_id）
        file_list = page_data.get("file_list") or page_data.get("list") or []
        fsids = [str(f["fs_id"]) for f in file_list if f.get("fs_id")]
        file_name = file_list[0].get("server_filename", "") if file_list else ""

        # 降级：regex 提取 fs_id（PHP parseResponse 同款）
        if not fsids:
            fsids = re.findall(r'"fs_id"\s*:\s*(\d+)', html)
            filenames = re.findall(r'"server_filename"\s*:\s*"([^"]+)"', html)
            file_name = filenames[0] if filenames else ""

        logger.info(f"分享页: shareid={shareid}, uk={uk}, fsids数量={len(fsids)}, file={file_name}")
        return {"shareid": shareid, "uk": uk, "bdstoken": bdstoken,
                "fsids": fsids, "file_name": file_name}

    async def _list_shared_files(self, client: httpx.AsyncClient, headers: dict,
                                  shareid: str, uk: str, bdstoken: str) -> tuple[list, str]:
        """GET /share/list 获取分享文件列表（baidupcs-py: list_shared_paths）"""
        resp = await client.get(
            f"{BASE_URL}/share/list",
            params={
                "channel": "chunlei", "clienttype": "0", "web": "1",
                "page": "1", "num": "100", "dir": "/",
                "t": str(random.random()),
                "uk": uk, "shareid": shareid,
                "desc": "1", "order": "other",
                "bdstoken": bdstoken if bdstoken and bdstoken != "null" else "null",
                "showempty": "0",
            },
            headers=headers,
        )
        data = resp.json()
        if data.get("errno") != 0:
            logger.error(f"❌ list_shared_files 失败: errno={data.get('errno')}, shareid={shareid}")
            return [], ""
        file_list = data.get("list", [])
        if not file_list:
            return [], ""
        fsids = [str(f["fs_id"]) for f in file_list if f.get("fs_id")]
        return fsids, file_list[0].get("server_filename", "")

    async def _ensure_dir(self, client: httpx.AsyncClient, headers: dict,
                          bdstoken: str, path: str) -> None:
        """确保目录存在（先检查，只在不存在时才创建，对齐 PHP getDirList → createDir 逻辑）"""
        check = await client.get(
            f"{BASE_URL}/api/list",
            params={"dir": path, "page": "1", "num": "1", "web": "1", "bdstoken": bdstoken},
            headers=headers,
        )
        if check.json().get("errno") == 0:
            return  # 目录已存在，跳过创建
        await client.post(
            f"{BASE_URL}/api/create",
            params={"a": "commit", "bdstoken": bdstoken},
            data={"path": path, "isdir": "1", "block_list": "[]"},
            headers=_form_headers(headers.get("Cookie", "")),
        )

    async def _transfer(self, client: httpx.AsyncClient, cookie: str,
                        shareid: str, uk: str, fsids: list, save_dir: str,
                        share_bdstoken: str, share_url: str) -> bool:
        """转存（baidupcs-py: transfer_shared_paths）— 用 share_bdstoken + 正确 Headers"""
        path = save_dir if save_dir.startswith("/") else f"/{save_dir}"
        headers = {
            **_build_headers(cookie),
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://pan.baidu.com",
            "Referer": share_url,
        }
        resp = await client.post(
            f"{BASE_URL}/share/transfer",
            params={"shareid": shareid, "from": uk,
                    "bdstoken": share_bdstoken if share_bdstoken and share_bdstoken != "null" else "null",
                    "channel": "chunlei", "clienttype": "0", "web": "1", "ondup": "newcopy"},
            data={"fsidlist": "[" + ",".join(fsids) + "]", "path": path},
            headers=headers,
        )
        data = resp.json()
        errno = data.get("errno", -1)
        if errno != 0:
            logger.error(f"❌ 百度转存失败: errno={errno}")
        return errno == 0

    async def _find_latest_file(self, client: httpx.AsyncClient, headers: dict,
                                bdstoken: str, dir_path: str) -> dict | None:
        resp = await client.get(
            f"{BASE_URL}/api/list",
            params={"order": "time", "desc": "1", "showempty": "0", "web": "1",
                    "page": "1", "num": "10", "dir": dir_path, "bdstoken": bdstoken},
            headers=headers,
        )
        files = resp.json().get("list", [])
        if not files:
            return None
        return {"path": files[0].get("path", ""), "fs_id": str(files[0].get("fs_id", ""))}

    async def list_folders(self, cookie: str, parent_id: str = "/") -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{BASE_URL}/api/list",
                    params={"order": "name", "showempty": "0", "web": "1",
                            "page": "1", "num": "100", "dir": parent_id},
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
