"""API v1 路由 - 管理后台（登录鉴权 + 账号管理 + 数据统计）

PRD补充需求4: 需要一个后台界面，配置网盘账号，查看API调用相关数据
鉴权方式: 环境变量 ADMIN_PASSWORD，登录后返回 token 存入前端 localStorage
"""

import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.pan_account import PanAccount
from app.models.resource import ResourceAsset, ResourceInstance
from app.models.task import DeleteTask, RequestLog
from app.schemas.response import ok, fail

router = APIRouter(prefix="/admin", tags=["admin"])


# ── 鉴权 ──────────────────────────────────────────────

def _generate_token(password: str) -> str:
    """根据密码生成简单 token (password + 当天日期的 hash)"""
    day = time.strftime("%Y-%m-%d")
    return hashlib.sha256(f"{password}:{day}".encode()).hexdigest()[:48]


def _verify_token(token: str) -> bool:
    settings = get_settings()
    return token == _generate_token(settings.admin_password)


async def require_admin(x_admin_token: str = Header(None)):
    """管理后台鉴权依赖 - 所有管理接口必须携带此 Header"""
    if not x_admin_token or not _verify_token(x_admin_token):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")


@router.post("/login")
async def admin_login(body: dict):
    """管理后台登录 - 验证密码返回 token（此接口无需鉴权）"""
    password = body.get("password", "")
    settings = get_settings()
    if password != settings.admin_password:
        return fail("AUTH_FAILED", "密码错误")
    return ok(data={"token": _generate_token(password)})


# ── 账号管理 CRUD（需鉴权）────────────────────────────

@router.get("/accounts", dependencies=[Depends(require_admin)])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    """获取所有网盘账号列表"""
    stmt = select(PanAccount).order_by(PanAccount.pan_type, PanAccount.id)
    result = await db.execute(stmt)
    accounts = result.scalars().all()
    return ok(data=[
        {
            "id": a.id,
            "pan_type": a.pan_type,
            "name": a.name,
            "is_active": a.is_active,
            "cookie_valid": a.cookie_valid,
            "total_space": a.total_space,
            "used_space": a.used_space,
            "max_concurrency": a.max_concurrency,
            "health_score": a.health_score,
            "weight": a.weight,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ])


@router.post("/accounts", dependencies=[Depends(require_admin)])
async def create_account(body: dict, db: AsyncSession = Depends(get_db)):
    """新增网盘账号"""
    required = ["pan_type", "name", "cookie"]
    for field in required:
        if field not in body:
            return fail("MISSING_FIELD", f"缺少必填字段: {field}")

    account = PanAccount(
        pan_type=body["pan_type"],
        name=body["name"],
        cookie=body["cookie"],
        is_active=body.get("is_active", True),
        total_space=body.get("total_space", 0),
        max_concurrency=body.get("max_concurrency", 3),
        weight=body.get("weight", 1),
        save_folder_id=body.get("save_folder_id", ""),
    )
    db.add(account)
    await db.flush()
    return ok(data={"id": account.id, "message": "账号创建成功"})


@router.put("/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def update_account(account_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    """更新网盘账号"""
    account = await db.get(PanAccount, account_id)
    if not account:
        return fail("NOT_FOUND", "账号不存在")

    allowed_fields = ["name", "cookie", "is_active", "total_space", "max_concurrency", "weight", "save_folder_id"]
    for key, value in body.items():
        if key in allowed_fields:
            setattr(account, key, value)

    return ok(data={"message": "账号更新成功"})


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """删除网盘账号"""
    account = await db.get(PanAccount, account_id)
    if not account:
        return fail("NOT_FOUND", "账号不存在")

    await db.delete(account)
    return ok(data={"message": "账号删除成功"})


# ── 数据统计（需鉴权）────────────────────────────────

@router.get("/stats", dependencies=[Depends(require_admin)])
async def get_stats(db: AsyncSession = Depends(get_db)):
    """获取API调用数据统计"""
    total_requests = (await db.execute(select(func.count(RequestLog.id)))).scalar() or 0
    success_requests = (await db.execute(
        select(func.count(RequestLog.id)).where(RequestLog.status == "success")
    )).scalar() or 0
    failed_requests = total_requests - success_requests

    total_assets = (await db.execute(select(func.count(ResourceAsset.id)))).scalar() or 0

    type_counts_rows = (await db.execute(
        select(ResourceAsset.pan_type, func.count(ResourceAsset.id)).group_by(ResourceAsset.pan_type)
    )).all()
    assets_by_type = {row[0]: row[1] for row in type_counts_rows}

    instance_status_rows = (await db.execute(
        select(ResourceInstance.status, func.count(ResourceInstance.id)).group_by(ResourceInstance.status)
    )).all()
    instances_by_status = {row[0]: row[1] for row in instance_status_rows}

    save_success = sum(instances_by_status.get(s, 0) for s in ["saved", "shared", "deleted"])
    share_success = sum(instances_by_status.get(s, 0) for s in ["shared", "deleted"])

    delete_completed = (await db.execute(
        select(func.count(DeleteTask.id)).where(DeleteTask.status == "completed")
    )).scalar() or 0

    total_accounts = (await db.execute(select(func.count(PanAccount.id)))).scalar() or 0
    active_accounts = (await db.execute(
        select(func.count(PanAccount.id)).where(PanAccount.is_active.is_(True), PanAccount.cookie_valid.is_(True))
    )).scalar() or 0

    return ok(data={
        "requests": {"total": total_requests, "success": success_requests, "failed": failed_requests},
        "assets": {"total": total_assets, "by_type": assets_by_type},
        "instances": {"by_status": instances_by_status, "save_success": save_success, "share_success": share_success},
        "deletes": {"completed": delete_completed},
        "accounts": {"total": total_accounts, "active": active_accounts},
    })


@router.get("/stats/recent", dependencies=[Depends(require_admin)])
async def get_recent_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """获取最近的请求日志"""
    stmt = select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return ok(data=[
        {
            "id": log.id,
            "client_id": log.client_id,
            "keyword": log.keyword,
            "pan_type": log.pan_type,
            "status": log.status,
            "duration_ms": log.duration_ms,
            "result_data": log.result_data,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ])
