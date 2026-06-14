"""账号池调度器

调度策略 (参考 docs/06_账号池调度算法.md):
1. 过滤: cookie有效 → 空间足够 → 并发未超限
2. 选择: health_score 降序 → weight 降序 → last_used 升序（LRU，同分时轮流使用）
"""

import logging
import time

import redis.asyncio as redis

from app.models.pan_account import PanAccount

logger = logging.getLogger(__name__)

_CONCURRENCY_KEY = "prds:pool:concurrency:{account_id}"
_LAST_USED_KEY = "prds:pool:last_used:{account_id}"


class AccountScheduler:

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    async def select_account(self, candidates: list[PanAccount]) -> PanAccount | None:
        """从候选账号中选取最优账号

        优先级: health_score DESC → weight DESC → last_used_at ASC
        相同分值时优先选最久未用的账号，实现均衡轮用。
        """
        available = []
        for account in candidates:
            if await self._check_concurrency(account):
                raw = await self._redis.get(_LAST_USED_KEY.format(account_id=account.id))
                last_used = float(raw) if raw else 0.0
                available.append((account, last_used))

        if not available:
            logger.warning("⚠️ 所有候选账号并发已满，无法分配")
            return None

        available.sort(key=lambda x: (-x[0].health_score, -x[0].weight, x[1]))
        return available[0][0]

    async def acquire(self, account: PanAccount) -> bool:
        """占用并发槽位，并记录最后使用时间（用于 LRU 调度）"""
        key = _CONCURRENCY_KEY.format(account_id=account.id)
        current = await self._redis.incr(key)
        if current == 1:
            await self._redis.expire(key, 300)
        if current > account.max_concurrency:
            await self._redis.decr(key)
            return False
        await self._redis.set(_LAST_USED_KEY.format(account_id=account.id), time.time(), ex=3600)
        return True

    async def release(self, account_id: int) -> None:
        """释放并发槽位"""
        key = _CONCURRENCY_KEY.format(account_id=account_id)
        val = await self._redis.decr(key)
        if val <= 0:
            await self._redis.delete(key)

    async def _check_concurrency(self, account: PanAccount) -> bool:
        """检查账号是否还有并发余量"""
        key = _CONCURRENCY_KEY.format(account_id=account.id)
        current = await self._redis.get(key)
        current = int(current) if current else 0
        return current < account.max_concurrency
