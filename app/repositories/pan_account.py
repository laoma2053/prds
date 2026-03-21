"""账号 Repository - pan_accounts 数据访问层"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pan_account import PanAccount


class PanAccountRepository:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_active_by_type(self, pan_type: str) -> list[PanAccount]:
        """获取指定网盘类型的所有启用且cookie有效的账号"""
        stmt = (
            select(PanAccount)
            .where(
                PanAccount.pan_type == pan_type,
                PanAccount.is_active.is_(True),
                PanAccount.cookie_valid.is_(True),
            )
            .order_by(PanAccount.health_score.desc(), PanAccount.weight.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, account_id: int) -> PanAccount | None:
        return await self._db.get(PanAccount, account_id)

    async def mark_cookie_invalid(self, account_id: int) -> None:
        stmt = update(PanAccount).where(PanAccount.id == account_id).values(cookie_valid=False)
        await self._db.execute(stmt)

    async def update_health_score(self, account_id: int, score: float) -> None:
        stmt = update(PanAccount).where(PanAccount.id == account_id).values(health_score=score)
        await self._db.execute(stmt)

    async def update_used_space(self, account_id: int, used_mb: int) -> None:
        stmt = update(PanAccount).where(PanAccount.id == account_id).values(used_space=used_mb)
        await self._db.execute(stmt)

    async def has_accounts_for_type(self, pan_type: str) -> bool:
        """判断系统是否配置了指定类型的有效账号"""
        stmt = (
            select(PanAccount.id)
            .where(
                PanAccount.pan_type == pan_type,
                PanAccount.is_active.is_(True),
                PanAccount.cookie_valid.is_(True),
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none() is not None
