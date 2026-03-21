"""账号池调度器

调度策略 (参考 docs/06_账号池调度算法.md):
1. 过滤: cookie有效 → 空间足够 → 并发未超限
2. 选择: health_score 降序 → weight 降序 → 轮询
"""

import logging

import redis.asyncio as redis

from app.models.pan_account import PanAccount

logger = logging.getLogger(__name__)

# Redis 并发计数键前缀
_CONCURRENCY_KEY = "prds:pool:concurrency:{account_id}"


class AccountScheduler:

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    async def select_account(self, candidates: list[PanAccount]) -> PanAccount | None:
        """从候选账号中选取最优账号

        候选列表已经按 health_score desc, weight desc 排序（由 Repository 层完成）。
        这里做并发检查，选取第一个未超限的。
        """
        for account in candidates:
            if await self._check_concurrency(account):
                return account

        logger.warning("⚠️ 所有候选账号并发已满，无法分配")
        return None

    async def acquire(self, account: PanAccount) -> bool:
        """占用并发槽位"""
        key = _CONCURRENCY_KEY.format(account_id=account.id)
        current = await self._redis.incr(key)
        if current == 1:
            await self._redis.expire(key, 300)  # 5分钟兜底过期
        if current > account.max_concurrency:
            await self._redis.decr(key)
            return False
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
