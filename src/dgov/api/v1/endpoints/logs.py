from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from dgov.api.deps import ConfigManagerDep, LogReaderDep, SessionDep
from dgov.core.config import settings
from dgov.models.metadata import OperationLog
from dgov.services import logs as service

router = APIRouter(prefix="/logs", tags=["logs"])


def _to_dict(entry: OperationLog) -> dict:
    return {
        "id": entry.id,
        "category": entry.category,
        "category_name": service.LOG_CATEGORIES.get(entry.category, entry.category),
        "action": entry.action,
        "level": entry.level,
        "actor": entry.actor,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "message": entry.message,
        "detail": entry.detail,
        "created_at": entry.created_at,
    }


@router.get("")
async def query(
    session: SessionDep,
    user: LogReaderDep,
    category: str | None = None,
    level: str | None = None,
    keyword: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    entries, total = await service.query_logs(
        session,
        category=category,
        level=level,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_to_dict(entry) for entry in entries],
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": service.LOG_CATEGORIES,
    }


@router.get("/categories")
async def categories(
    session: SessionDep,
    user: LogReaderDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> list[dict]:
    return await service.category_stats(session, days=days)


@router.get("/retention")
async def retention(session: SessionDep, user: LogReaderDep) -> dict:
    total = (
        await session.execute(select(func.count()).select_from(OperationLog))
    ).scalar_one()
    oldest = (
        await session.execute(select(func.min(OperationLog.created_at)))
    ).scalar_one()
    newest = (
        await session.execute(select(func.max(OperationLog.created_at)))
    ).scalar_one()
    return {
        "total": total,
        "oldest": oldest,
        "newest": newest,
        "retention_days": settings.log_retention_days,
        "max_rows": settings.log_max_rows,
        "file_max_bytes": settings.log_file_max_bytes,
        "file_backup_count": settings.log_backup_count,
        "log_dir": settings.log_dir,
        "quality_result_retention_days": settings.quality_result_retention_days,
    }


@router.post("/cleanup")
async def cleanup(session: SessionDep, manager: ConfigManagerDep) -> dict:
    return await service.cleanup_logs(
        session,
        retention_days=settings.log_retention_days,
        max_rows=settings.log_max_rows,
    )
