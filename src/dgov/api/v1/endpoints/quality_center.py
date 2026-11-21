from typing import Annotated

from fastapi import APIRouter, Query

from dgov.api.deps import GovernanceWriterDep, SessionDep
from dgov.services import quality_meta as service

router = APIRouter(prefix="/quality-center", tags=["quality-center"])


@router.get("/summary")
async def summary(session: SessionDep) -> dict:
    return await service.quality_summary(session)


@router.get("/rules")
async def rules(session: SessionDep) -> list[dict]:
    rules = await service.list_rules(session)
    return [
        {
            "id": rule.id,
            "code": rule.code,
            "name": rule.name,
            "dimension": rule.dimension,
            "target": rule.target,
            "severity": rule.severity,
            "description": rule.description,
            "enabled": rule.enabled,
        }
        for rule in rules
    ]


@router.get("/issues")
async def issues(
    session: SessionDep,
    run_id: str | None = None,
    status: Annotated[str | None, Query(pattern="^(passed|warning|failed)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    items, total = await service.list_issues(
        session, run_id=run_id, status=status, page=page, page_size=page_size
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/tables/{table_id}")
async def table_results(table_id: int, session: SessionDep) -> dict:
    return await service.table_results(session, table_id)


@router.get("/trend")
async def trend(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> list[dict]:
    from sqlalchemy import case, func, select

    from dgov.models.metadata import QualityCheckResult

    rows = (
        await session.execute(
            select(
                QualityCheckResult.run_id,
                func.min(QualityCheckResult.executed_at),
                func.count(),
                func.sum(case((QualityCheckResult.status == "passed", 1), else_=0)),
                func.sum(case((QualityCheckResult.status == "failed", 1), else_=0)),
            )
            .group_by(QualityCheckResult.run_id)
            .order_by(func.min(QualityCheckResult.executed_at).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "run_id": run_id,
            "executed_at": executed_at,
            "checks": checks,
            "passed": passed or 0,
            "failed": failed or 0,
            "pass_rate": round((passed or 0) / checks * 100, 2) if checks else 0.0,
        }
        for run_id, executed_at, checks, passed, failed in reversed(rows)
    ]


@router.post("/run")
async def run_checks(session: SessionDep, manager: GovernanceWriterDep) -> dict:
    return await service.run_quality_checks(session, actor=manager.username, trigger="manual")
