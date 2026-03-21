"""夸克网盘 Provider

参考实现: 
- https://github.com/laoma2053/kuakeso (src/lib/quark-api.ts)
- https://github.com/Cp0204/quark-auto-save (quark_auto_save.py)
- https://github.com/ucmao/search-ucmao (src/clients/quark_client.py)

API 端点:
- 账号验证: GET  https://pan.quark.cn/account/info
- 获取stoken: POST https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token
- 分享详情: GET  https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail
- 转存文件: POST https://drive.quark.cn/1/clouddrive/share/sharepage/save
- 任务查询: GET  https://drive-pc.quark.cn/1/clouddrive/task
- 创建分享: POST https://drive-pc.quark.cn/1/clouddrive/share
- 获取链接: POST https://drive-pc.quark.cn/1/clouddrive/share/password
- 删除文件: POST https://drive-pc.quark.cn/1/clouddrive/file/delete

关键差异（对比之前版本）:
- BASE_URL 改为 https://drive-h.quark.cn（带 -h，非 -pc）
- 转存轮询 30 次（15秒），分享轮询 10 次（10秒）
- 分享任务单独轮询逻辑（检查 share_id 而非 status==2）
- 每个请求带超时 + 重试机制
- 校验 response.code === 0
- detail 接口补全参数
"""

import re
import asyncio
import logging
import random

import httpx

from app.providers.base import (
    BaseProvider,
    SaveResult,
    ShareResult,
    DeleteResult,
    generate_timestamp,
)

logger = logging.getLogger(__name__)

_PWD_ID_PATTERN = re.compile(r"/s/(\w+)")

BASE_URL = "https://drive-h.quark.cn"
PAN_URL = "https://pan.quark.cn"
_COMMON_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}

REQUEST_TIMEOUT = 15.0
MAX_RETRIES = 2
RETRY_DELAY = 1.0


def _build_headers(cookie: str) -> dict:
    return {
        "cookie": cookie,
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "origin": PAN_URL,
        "referer": f"{PAN_URL}/",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "accept-language": "zh-CN,zh;q=0.9",
    }


def _extract_pwd_id(share_url: str) -> str:
    m = _PWD_ID_PATTERN.search(share_url)
    if not m:
        raise ValueError(f"无法从链接中提取 pwd_id: {share_url}")
    return m.group(1)


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, headers: dict, **kwargs) -> dict:
    """带重试的请求封装"""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            logger.warning(f"🔄 [夸克API] 第{attempt}次重试: {method} {url}")
            await asyncio.sleep(RETRY_DELAY * attempt)
        try:
            if method == "GET":
                resp = await client.get(url, headers=headers, **kwargs)
            else:
                resp = await client.post(url, headers=headers, **kwargs)
            data = resp.json()
            if data.get("code") not in (0, None) and data.get("status") != 200:
                logger.warning(f"⚠️ [夸克API] 响应异常: {data.get('code')} {data.get('message')}")
            return data
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            logger.warning(f"⏱️ [夸克API] 连接超时 (第{attempt + 1}次): {method} {url}")
        except Exception as e:
            logger.error(f"❌ [夸克API] 请求失败: {e}")
            return {"status": 500, "code": 1, "message": str(e)}

    logger.error(f"💀 [夸克API] 重试耗尽: {method} {url}")
    return {"status": 500, "code": 1, "message": f"请求超时: {last_error}"}


class QuarkProvider(BaseProvider):
    """夸克网盘 Provider"""

    pan_type = "quark"

    async def check_cookie(self, cookie: str) -> bool:
        headers = _build_headers(cookie)
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                data = await _request_with_retry(client, "GET", f"{PAN_URL}/account/info", headers, params={"fr": "pc", "platform": "pc"})
                valid = bool(data.get("data"))
                logger.info(f"🔑 夸克 Cookie 验证: {'有效' if valid else '无效'}")
                return valid
        except Exception as e:
            logger.warning(f"⚠️ 夸克 Cookie 验证异常: {e}")
            return False

    async def save_share(self, share_url: str, cookie: str, save_folder_id: str = "0") -> SaveResult:
        """转存分享资源（7步流程，对齐 kuakeso 实现）"""
        headers = _build_headers(cookie)

        try:
            pwd_id = _extract_pwd_id(share_url)
            logger.info(f"📎 [转存] 步骤1/6 解析链接: pwdId={pwd_id}")

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                # 2. 获取 stoken
                stoken = await self._get_stoken(client, headers, pwd_id)
                if not stoken:
                    return SaveResult(success=False, error="获取 stoken 失败，资源可能已失效")
                logger.info("🔑 [转存] 步骤2/6 获取stoken成功")

                # 3. 获取分享详情
                detail = await self._get_detail(client, headers, pwd_id, stoken)
                if not detail:
                    return SaveResult(success=False, error="获取分享详情失败，分享内容为空")
                logger.info(f"📂 [转存] 步骤3/6 获取文件列表: {detail['file_name']}")

                fid = detail["fid"]
                fid_token = detail["share_fid_token"]
                file_name = detail["file_name"]

                # 4. 执行转存
                task_id = await self._save_file(client, headers, pwd_id, stoken, fid, fid_token, save_folder_id)
                if not task_id:
                    return SaveResult(success=False, error="创建转存任务失败")
                logger.info(f"💾 [转存] 步骤4/6 转存任务已提交: task_id={task_id}")

                # 5. 等待任务完成（轮询30次，每次500ms，共15秒）
                task_data = await self._query_task(client, headers, task_id, retries=30, interval=0.5)
                if not task_data:
                    return SaveResult(success=False, error="转存任务执行失败或超时")

                save_as = task_data.get("save_as", {})
                top_fids = save_as.get("save_as_top_fids", [])
                if not top_fids:
                    return SaveResult(success=False, error="转存结果中未找到文件ID")

                logger.info(f"✅ [转存] 步骤5/6 转存完成, 文件ID: {top_fids}")
                return SaveResult(success=True, file_id=top_fids[0], file_name=file_name)

        except Exception as e:
            logger.error(f"💥 夸克转存异常: {e}")
            return SaveResult(success=False, error=str(e))

    async def create_share(self, file_id: str, file_name: str, cookie: str) -> ShareResult:
        """创建分享链接（3步流程，对齐 kuakeso 的 createShare）"""
        headers = _build_headers(cookie)

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                # 步骤1: 创建分享任务
                data = await _request_with_retry(
                    client, "POST", f"{BASE_URL}/1/clouddrive/share", headers,
                    params={**_COMMON_PARAMS, "__dt": random.randint(100, 999), "__t": generate_timestamp(13)},
                    json={"fid_list": [file_id], "title": file_name, "url_type": 1, "expired_type": 1},
                )
                task_id = data.get("data", {}).get("task_id")
                if data.get("code") != 0 or not task_id:
                    return ShareResult(success=False, error=f"创建分享任务失败: {data.get('message')}")
                logger.info(f"📤 [分享] 步骤1/3 分享任务已创建: task_id={task_id}")

                # 步骤2: 轮询获取 share_id（分享任务用专门的轮询逻辑）
                share_id = await self._query_share_task(client, headers, task_id)
                if not share_id:
                    return ShareResult(success=False, error="分享任务执行失败，未获取到 share_id")
                logger.info(f"🔗 [分享] 步骤2/3 获取share_id成功: {share_id}")

                # 步骤3: 获取分享链接
                data = await _request_with_retry(
                    client, "POST", f"{BASE_URL}/1/clouddrive/share/password", headers,
                    params=_COMMON_PARAMS, json={"share_id": share_id},
                )
                share_url = data.get("data", {}).get("share_url", "")
                if data.get("code") != 0 or not share_url:
                    return ShareResult(success=False, error="获取分享链接失败")

                logger.info(f"✅ [分享] 步骤3/3 分享链接已生成: {share_url}")
                return ShareResult(success=True, share_url=share_url, share_id=share_id)

        except Exception as e:
            logger.error(f"💥 夸克创建分享异常: {e}")
            return ShareResult(success=False, error=str(e))

    async def delete_resource(self, file_id: str, cookie: str) -> DeleteResult:
        """删除文件"""
        headers = _build_headers(cookie)

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                data = await _request_with_retry(
                    client, "POST", f"{BASE_URL}/1/clouddrive/file/delete", headers,
                    params=_COMMON_PARAMS,
                    json={"action_type": 2, "filelist": [file_id], "exclude_fids": []},
                )
                if data.get("code") != 0:
                    return DeleteResult(success=False, error=data.get("message", "删除请求失败"))

                task_id = data.get("data", {}).get("task_id")
                if task_id:
                    result = await self._query_task(client, headers, task_id, retries=15, interval=0.5)
                    if not result:
                        return DeleteResult(success=False, error="删除任务执行超时")

                logger.info(f"🗑️ 夸克删除成功: {file_id}")
                return DeleteResult(success=True)

        except Exception as e:
            logger.error(f"💥 夸克删除文件异常: {e}")
            return DeleteResult(success=False, error=str(e))

    # ── 内部方法 ──────────────────────────────────────────

    async def _get_stoken(self, client: httpx.AsyncClient, headers: dict, pwd_id: str) -> str:
        data = await _request_with_retry(
            client, "POST", f"{BASE_URL}/1/clouddrive/share/sharepage/token", headers,
            params={**_COMMON_PARAMS}, json={"pwd_id": pwd_id, "passcode": ""},
        )
        if data.get("status") == 200 and data.get("data", {}).get("stoken"):
            return data["data"]["stoken"]
        return ""

    async def _get_detail(self, client: httpx.AsyncClient, headers: dict, pwd_id: str, stoken: str) -> dict | None:
        data = await _request_with_retry(
            client, "GET", f"{BASE_URL}/1/clouddrive/share/sharepage/detail", headers,
            params={
                "pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken,
                "pdir_fid": "0", "force": "0", "_page": 1, "_size": 50,
                "_fetch_banner": "0", "_fetch_share": "0", "_fetch_total": "1",
                "_sort": "file_type:asc,updated_at:desc",
            },
        )
        if data.get("code") != 0:
            return None
        file_list = data.get("data", {}).get("list", [])
        if not file_list:
            return None
        item = file_list[0]
        return {
            "fid": item.get("fid"),
            "share_fid_token": item.get("share_fid_token"),
            "file_name": item.get("file_name", ""),
            "file_type": item.get("file_type"),
        }

    async def _save_file(
        self, client: httpx.AsyncClient, headers: dict,
        pwd_id: str, stoken: str, fid: str, fid_token: str, to_pdir_fid: str,
    ) -> str:
        data = await _request_with_retry(
            client, "POST", "https://drive.quark.cn/1/clouddrive/share/sharepage/save", headers,
            params={**_COMMON_PARAMS, "__dt": random.randint(100, 999), "__t": generate_timestamp(13)},
            json={
                "fid_list": [fid], "fid_token_list": [fid_token],
                "to_pdir_fid": to_pdir_fid, "pwd_id": pwd_id,
                "stoken": stoken, "pdir_fid": "0", "scene": "link",
            },
        )
        if data.get("code") == 0 and data.get("data", {}).get("task_id"):
            return data["data"]["task_id"]
        logger.error(f"❌ [夸克API] 转存失败: {data.get('message')}")
        return ""

    async def _query_task(self, client: httpx.AsyncClient, headers: dict, task_id: str, retries: int = 30, interval: float = 0.5) -> dict | None:
        """轮询转存任务状态"""
        for i in range(retries):
            data = await _request_with_retry(
                client, "GET", f"{BASE_URL}/1/clouddrive/task", headers,
                params={
                    **_COMMON_PARAMS, "task_id": task_id, "retry_index": i,
                    "__dt": random.randint(100, 999), "__t": generate_timestamp(13),
                },
            )
            if data.get("status") != 200:
                return None
            if data.get("data", {}).get("status") == 2:
                return data.get("data", {})
            await asyncio.sleep(interval)
        return None

    async def _query_share_task(self, client: httpx.AsyncClient, headers: dict, task_id: str) -> str | None:
        """轮询分享任务，获取 share_id（分享任务的返回结构与转存不同）"""
        for i in range(10):
            data = await _request_with_retry(
                client, "GET", f"{BASE_URL}/1/clouddrive/task", headers,
                params={
                    **_COMMON_PARAMS, "task_id": task_id, "retry_index": i,
                    "__dt": random.randint(100, 999), "__t": generate_timestamp(13),
                },
            )
            if data.get("status") != 200:
                return None
            # 分享任务: share_id 在 data 顶层
            share_id = data.get("data", {}).get("share_id")
            if share_id:
                return share_id
            await asyncio.sleep(1.0)
        return None
