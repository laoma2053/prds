"""资源 Repository - resource_assets / resource_instances 数据访问层"""

from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import ResourceAsset, ResourceInstance


class ResourceAssetRepository:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_key(self, resource_key: str) -> ResourceAsset | None:
        stmt = select(ResourceAsset).where(ResourceAsset.resource_key == resource_key)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> ResourceAsset:
        asset = ResourceAsset(**kwargs)
        self._db.add(asset)
        await self._db.flush()
        return asset


class ResourceInstanceRepository:

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, **kwargs) -> ResourceInstance:
        instance = ResourceInstance(**kwargs)
        self._db.add(instance)
        await self._db.flush()
        return instance

    async def get_by_id(self, instance_id: int) -> ResourceInstance | None:
        return await self._db.get(ResourceInstance, instance_id)

    async def update_status(self, instance_id: int, status: str, **extra) -> None:
        stmt = update(ResourceInstance).where(ResourceInstance.id == instance_id).values(status=status, **extra)
        await self._db.execute(stmt)

    async def get_resource_key_by_instance(self, instance_id: int) -> str | None:
        """通过 instance_id 反查关联 asset 的 resource_key"""
        stmt = (
            select(ResourceAsset.resource_key)
            .join(ResourceInstance, ResourceInstance.asset_id == ResourceAsset.id)
            .where(ResourceInstance.id == instance_id)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_valid_instance(self, asset_id: int) -> ResourceInstance | None:
        """获取资源的一个可用实例（已分享且未过期）"""
        now = datetime.now()
        stmt = (
            select(ResourceInstance)
            .where(
                ResourceInstance.asset_id == asset_id,
                ResourceInstance.status == "shared",
                ResourceInstance.expire_at > now,
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()
