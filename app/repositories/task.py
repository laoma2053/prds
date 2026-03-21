"""任务 Repository - delete_tasks / request_logs 数据访问层"""

from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import DeleteTask, RequestLog


class DeleteTaskRepository:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, **kwargs) -> DeleteTask:
        task = DeleteTask(**kwargs)
        self._db.add(task)
        await self._db.flush()
        return task

    async def get_due_tasks(self, now: datetime, limit: int = 50) -> list[DeleteTask]:
        """获取到期待执行的删除任务"""
        stmt = (
            select(DeleteTask)
            .where(DeleteTask.status == "pending", DeleteTask.due_at <= now)
            .order_by(DeleteTask.due_at.asc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(self, task_id: int, status: str, **extra) -> None:
        stmt = update(DeleteTask).where(DeleteTask.id == task_id).values(status=status, **extra)
        await self._db.execute(stmt)


class RequestLogRepository:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, **kwargs) -> RequestLog:
        log = RequestLog(**kwargs)
        self._db.add(log)
        await self._db.flush()
        return log
