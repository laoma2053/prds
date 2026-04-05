"""Delete Worker - 定时扫描并执行到期的删除任务

参考: docs/07_Worker任务架构.md
使用 Redis ZSET (prds:delete_due) 作为延迟队列，定期扫描到期任务。
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.redis import redis_client
from app.repositories.task import DeleteTaskRepository
from app.repositories.pan_account import PanAccountRepository
from app.repositories.resource import ResourceInstanceRepository
from app.providers import get_provider

logger = logging.getLogger(__name__)

_RESOURCE_CACHE_KEY = "prds:cache:resource:{resource_key}"


async def run_delete_worker(interval: int = 30):
    """删除 Worker 主循环

    Args:
        interval: 扫描间隔(秒)
    """
    logger.info(f"🗑️ 删除 Worker 启动, 扫描间隔={interval}秒")
    while True:
        try:
            await _process_batch()
        except Exception as e:
            logger.error(f"💥 删除 Worker 异常: {e}")
        await asyncio.sleep(interval)


async def _process_batch():
    """处理一批到期的删除任务"""
    async with async_session_factory() as db:
        repo = DeleteTaskRepository(db)
        account_repo = PanAccountRepository(db)
        instance_repo = ResourceInstanceRepository(db)

        now = datetime.now(timezone.utc)
        tasks = await repo.get_due_tasks(now, limit=20)

        if not tasks:
            return

        logger.info(f"📋 扫描到 {len(tasks)} 个到期删除任务")

        for task in tasks:
            await _execute_delete(db, task, repo, account_repo, instance_repo)

        await db.commit()


async def _execute_delete(db: AsyncSession, task, repo, account_repo, instance_repo):
    """执行单个删除任务"""
    try:
        await repo.update_status(task.id, "processing")

        # 获取实例和账号信息
        instance = await instance_repo.get_by_id(task.instance_id)
        if not instance or not instance.saved_file_id:
            await repo.update_status(task.id, "completed", error_message="实例不存在或无文件ID")
            logger.warning(f"⚠️ 删除跳过: task={task.id}, 实例不存在或无文件ID")
            return

        account = await account_repo.get_by_id(task.account_id)
        if not account:
            await repo.update_status(task.id, "failed", error_message="账号不存在")
            logger.warning(f"⚠️ 删除失败: task={task.id}, 账号不存在")
            return

        # 获取 Provider 并执行删除
        provider = get_provider(account.pan_type)
        if not provider:
            await repo.update_status(task.id, "failed", error_message=f"未知的网盘类型: {account.pan_type}")
            logger.warning(f"⚠️ 删除失败: task={task.id}, 未知网盘类型 {account.pan_type}")
            return

        result = await provider.delete_resource(instance.saved_file_id, account.cookie)

        if result.success:
            now = datetime.now(timezone.utc)
            await repo.update_status(task.id, "completed", executed_at=now)
            await instance_repo.update_status(instance.id, "deleted")
            # 清除 L3 资源缓存，防止返回已失效的分享链接
            resource_key = await instance_repo.get_resource_key_by_instance(instance.id)
            if resource_key:
                cache_key = _RESOURCE_CACHE_KEY.format(resource_key=resource_key)
                await redis_client.delete(cache_key)
            logger.info(f"🗑️ 删除成功: instance={instance.id}, file={instance.saved_file_id}")
        else:
            await repo.update_status(task.id, "failed", error_message=result.error)
            logger.warning(f"❌ 删除失败: instance={instance.id}, 原因={result.error}")

    except Exception as e:
        await repo.update_status(task.id, "failed", error_message=str(e))
        logger.error(f"💥 删除任务异常: task={task.id}, 错误={e}")
