"""ResourceService - 核心业务逻辑

三层缓存策略:
  L1: 搜索结果 Redis 缓存 - 相同关键词短时间内直接返回
  L2: 转存分布式锁 - 同一资源并发请求只执行一次转存，其余等待
  L3: 已转存资源 Redis 缓存 - 未删除的分享链接毫秒级返回

业务流程:
  1. 检查 L1 搜索缓存 → 命中则秒回
  2. 调用 PanSou 搜索 → 按时间降序排列 → 取前 limit 条
  3. 对每个链接: 检查 L3 资源缓存 → 命中则直接返回
  4. 未命中: 获取 L2 分布式锁 → 转存 → 分享 → 写入 L3 缓存
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.core.config import get_settings
from app.services.pansou_client import PanSouClient
from app.services.scheduler import AccountScheduler
from app.providers import get_provider
from app.repositories.pan_account import PanAccountRepository
from app.repositories.resource import ResourceAssetRepository, ResourceInstanceRepository
from app.repositories.task import DeleteTaskRepository, RequestLogRepository

logger = logging.getLogger(__name__)

# Redis 键模板
_SEARCH_CACHE_KEY = "prds:cache:search:{keyword}:{pan_type}:{limit}"
_RESOURCE_CACHE_KEY = "prds:cache:resource:{resource_key}"
_RESOURCE_LOCK_KEY = "prds:lock:resource:{resource_key}"


def _make_resource_key(keyword: str, url: str) -> str:
    raw = f"{keyword}|{url}"
    return hashlib.md5(raw.encode()).hexdigest()


class ResourceService:

    def __init__(self, db: AsyncSession, redis_client: redis.Redis):
        self._db = db
        self._redis = redis_client
        self._settings = get_settings()
        self._pansou = PanSouClient()
        self._scheduler = AccountScheduler(redis_client)
        self._account_repo = PanAccountRepository(db)
        self._asset_repo = ResourceAssetRepository(db)
        self._instance_repo = ResourceInstanceRepository(db)
        self._delete_repo = DeleteTaskRepository(db)
        self._log_repo = RequestLogRepository(db)

    # ── 主入口 ────────────────────────────────────────

    async def search_and_deliver(
        self, keyword: str, pan_type: str | None = None,
        limit: int | None = None, client_id: str = "default",
    ) -> dict:
        start_ms = time.monotonic()

        if not pan_type:
            pan_type = self._settings.default_pan_type
            logger.info(f"🔍 未指定网盘类型，默认使用: {pan_type}")

        # 确定 limit: 前端传了用前端的，没传用默认值，不超过上限
        effective_limit = min(
            limit or self._settings.default_search_limit,
            self._settings.max_search_limit,
        )

        try:
            # L1: 搜索结果缓存（含 limit 维度）
            cached = await self._get_search_cache(keyword, pan_type, effective_limit)
            if cached is not None:
                logger.info(f"⚡ 搜索缓存命中: keyword={keyword}, limit={effective_limit}")
                duration_ms = int((time.monotonic() - start_ms) * 1000)
                await self._log_repo.create(
                    client_id=client_id, keyword=keyword, pan_type=pan_type,
                    status="success", duration_ms=duration_ms,
                    result_data={"total": len(cached.get("results", [])), "mode": cached.get("mode"), "cache": True},
                )
                return cached

            # PanSou 搜索
            logger.info(f"🔎 开始搜索: keyword={keyword}, pan_type={pan_type}, limit={effective_limit}, client={client_id}")
            search_resp = await self._pansou.search(keyword, pan_type=pan_type)

            if search_resp.total == 0:
                logger.info(f"📭 搜索无结果: keyword={keyword}")
                empty_result = {"mode": "direct", "results": []}
                duration_ms = int((time.monotonic() - start_ms) * 1000)
                await self._log_repo.create(
                    client_id=client_id, keyword=keyword, pan_type=pan_type,
                    status="success", duration_ms=duration_ms,
                    result_data={"total": 0, "mode": "direct"},
                )
                await self._set_search_cache(keyword, pan_type, effective_limit, empty_result, ttl=60)
                return empty_result

            # 处理搜索结果
            results = []
            for ptype in search_resp.available_types:
                # 按时间降序排列，取最新的前 limit 条
                links = search_resp.get_links_by_type(ptype, limit=effective_limit)
                has_account = await self._account_repo.has_accounts_for_type(ptype)

                if has_account:
                    logger.info(f"📦 [{ptype}] 检测到有效账号，进入转存模式 (最新{len(links)}条)")
                    delivered = await self._deliver_links(keyword, ptype, links)
                    results.extend(delivered)
                else:
                    logger.info(f"📎 [{ptype}] 未配置账号，返回原始链接 (最新{len(links)}条)")
                    for link in links:
                        results.append({
                            "title": link.note or keyword, "pan_type": ptype,
                            "url": link.url, "password": link.password, "mode": "direct",
                        })

            mode = "proxy" if any(r.get("mode") == "proxy" for r in results) else "direct"
            logger.info(f"✅ 搜索完成: keyword={keyword}, 模式={mode}, 结果数={len(results)}")

            final = {"mode": mode, "results": results}

            # 写入 L1 搜索缓存
            await self._set_search_cache(keyword, pan_type, effective_limit, final)

            duration_ms = int((time.monotonic() - start_ms) * 1000)
            await self._log_repo.create(
                client_id=client_id, keyword=keyword, pan_type=pan_type,
                status="success", duration_ms=duration_ms,
                result_data={"total": len(results), "mode": mode},
            )
            return final

        except Exception as e:
            duration_ms = int((time.monotonic() - start_ms) * 1000)
            await self._log_repo.create(
                client_id=client_id, keyword=keyword, pan_type=pan_type,
                status="failed", duration_ms=duration_ms, result_data={"error": str(e)},
            )
            raise

    # ── 转存流程 ──────────────────────────────────────

    async def _deliver_links(self, keyword: str, pan_type: str, links: list) -> list[dict]:
        provider = get_provider(pan_type)
        if not provider:
            return [{"title": lnk.note, "pan_type": pan_type, "url": lnk.url, "password": lnk.password, "mode": "direct"} for lnk in links]

        results = []
        for link in links:
            result = await self._deliver_single(keyword, pan_type, link, provider)
            results.append(result)
        return results

    async def _deliver_single(self, keyword: str, pan_type: str, link, provider) -> dict:
        resource_key = _make_resource_key(keyword, link.url)

        # L3: Redis 资源缓存（毫秒级）
        cached = await self._get_resource_cache(resource_key)
        if cached is not None:
            logger.info(f"⚡ 资源缓存命中: {resource_key[:8]}")
            return cached

        # DB 层去重: 检查已有可用实例
        existing_asset = await self._asset_repo.get_by_key(resource_key)
        if existing_asset:
            valid_instance = await self._instance_repo.get_valid_instance(existing_asset.id)
            if valid_instance:
                result = {
                    "title": existing_asset.title, "pan_type": pan_type,
                    "url": valid_instance.share_url, "password": valid_instance.share_password,
                    "mode": "proxy",
                    "expire_at": valid_instance.expire_at.isoformat() if valid_instance.expire_at else None,
                }
                await self._set_resource_cache(resource_key, result)
                logger.info(f"📋 DB缓存命中: {existing_asset.title}")
                return result

        # L2: 分布式锁 - 防止并发转存同一资源
        lock_key = _RESOURCE_LOCK_KEY.format(resource_key=resource_key)
        lock_acquired = await self._redis.set(lock_key, "1", nx=True, ex=120)

        if not lock_acquired:
            logger.info(f"⏳ 等待其他请求完成转存: {resource_key[:8]}")
            result = await self._wait_for_resource(resource_key, pan_type, keyword, link)
            return result

        try:
            return await self._do_transfer(keyword, pan_type, link, provider, resource_key, existing_asset)
        finally:
            await self._redis.delete(lock_key)

    async def _wait_for_resource(self, resource_key: str, pan_type: str, keyword: str, link) -> dict:
        """等待其他请求的转存结果（最多30秒）"""
        for _ in range(30):
            await asyncio.sleep(1)
            cached = await self._get_resource_cache(resource_key)
            if cached is not None:
                logger.info(f"⚡ 等待后缓存命中: {resource_key[:8]}")
                return cached
        logger.warning(f"⏰ 等待转存超时，降级返回原始链接: {resource_key[:8]}")
        return {"title": link.note or keyword, "pan_type": pan_type, "url": link.url, "password": link.password, "mode": "direct"}

    async def _do_transfer(self, keyword, pan_type, link, provider, resource_key, existing_asset) -> dict:
        """执行实际的转存+分享流程"""
        candidates = await self._account_repo.get_active_by_type(pan_type)
        account = await self._scheduler.select_account(candidates)
        if not account:
            return {"title": link.note or keyword, "pan_type": pan_type, "url": link.url, "password": link.password, "mode": "direct"}

        acquired = await self._scheduler.acquire(account)
        if not acquired:
            return {"title": link.note or keyword, "pan_type": pan_type, "url": link.url, "password": link.password, "mode": "direct"}

        try:
            save_result = await provider.save_share(link.url, account.cookie, account.save_folder_id)
            if not save_result.success:
                logger.warning(f"❌ 转存失败: {save_result.error}")
                return {"title": link.note or keyword, "pan_type": pan_type, "url": link.url, "password": link.password, "mode": "direct"}

            logger.info(f"💾 转存成功: {save_result.file_name} -> {save_result.file_id}")

            share_result = await provider.create_share(save_result.file_id, save_result.file_name, account.cookie)
            if not share_result.success:
                logger.warning(f"❌ 分享失败: {share_result.error}")
                await self._register_delete(save_result.file_id, account.id)
                return {"title": link.note or keyword, "pan_type": pan_type, "url": link.url, "password": link.password, "mode": "direct"}

            now = datetime.now(timezone.utc)
            ttl = self._settings.resource_ttl_minutes
            delete_delay = self._settings.delete_delay_minutes

            asset = existing_asset or await self._asset_repo.create(
                resource_key=resource_key, keyword=keyword,
                title=save_result.file_name or link.note or keyword,
                original_url=link.url, pan_type=pan_type,
            )

            instance = await self._instance_repo.create(
                asset_id=asset.id, account_id=account.id, status="shared",
                saved_file_id=save_result.file_id,
                share_url=share_result.share_url, share_password=share_result.share_password,
                expire_at=now + timedelta(minutes=ttl),
                delete_at=now + timedelta(minutes=delete_delay),
            )

            await self._delete_repo.create(
                instance_id=instance.id, account_id=account.id,
                status="pending", due_at=now + timedelta(minutes=delete_delay),
            )

            logger.info(f"🔗 分享成功: {share_result.share_url} (TTL={ttl}分钟, 删除={delete_delay}分钟后)")

            result = {
                "title": asset.title, "pan_type": pan_type,
                "url": share_result.share_url, "password": share_result.share_password,
                "mode": "proxy", "expire_at": instance.expire_at.isoformat(),
            }

            await self._set_resource_cache(resource_key, result)
            return result

        finally:
            await self._scheduler.release(account.id)

    async def _register_delete(self, file_id: str, account_id: int) -> None:
        now = datetime.now(timezone.utc)
        delay = self._settings.delete_delay_minutes
        instance = await self._instance_repo.create(
            asset_id=None, account_id=account_id, status="failed",
            saved_file_id=file_id, delete_at=now + timedelta(minutes=delay),
        )
        await self._delete_repo.create(
            instance_id=instance.id, account_id=account_id,
            status="pending", due_at=now + timedelta(minutes=delay),
        )

    # ── L1: 搜索结果缓存 ─────────────────────────────

    async def _get_search_cache(self, keyword: str, pan_type: str, limit: int) -> dict | None:
        key = _SEARCH_CACHE_KEY.format(keyword=keyword, pan_type=pan_type, limit=limit)
        raw = await self._redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def _set_search_cache(self, keyword: str, pan_type: str, limit: int, data: dict, ttl: int | None = None) -> None:
        key = _SEARCH_CACHE_KEY.format(keyword=keyword, pan_type=pan_type, limit=limit)
        ttl = ttl or self._settings.search_cache_ttl
        await self._redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)

    # ── L3: 已转存资源缓存 ───────────────────────────

    async def _get_resource_cache(self, resource_key: str) -> dict | None:
        key = _RESOURCE_CACHE_KEY.format(resource_key=resource_key)
        raw = await self._redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def _set_resource_cache(self, resource_key: str, data: dict) -> None:
        key = _RESOURCE_CACHE_KEY.format(resource_key=resource_key)
        ttl = self._settings.resource_cache_ttl
        await self._redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
