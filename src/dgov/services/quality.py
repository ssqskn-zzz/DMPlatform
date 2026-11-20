from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import BadRequestError, NotFoundError
from dgov.models.catalog import Dataset, DatasetColumn
from dgov.models.quality import QualityResult, QualityRule
from dgov.schemas.quality import QualityRuleCreate, QualityRuleUpdate


async def list_rules(
    session: AsyncSession,
    page: int,
    page_size: int,
    dataset_id: int | None = None,
    enabled: bool | None = None,
) -> tuple[list[QualityRule], int]:
    stmt = select(QualityRule)
    count_stmt = select(func.count()).select_from(QualityRule)
    if dataset_id is not None:
        stmt = stmt.where(QualityRule.dataset_id == dataset_id)
        count_stmt = count_stmt.where(QualityRule.dataset_id == dataset_id)
    if enabled is not None:
        stmt = stmt.where(QualityRule.enabled.is_(enabled))
        count_stmt = count_stmt.where(QualityRule.enabled.is_(enabled))
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(QualityRule.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_rule(session: AsyncSession, rule_id: int) -> QualityRule:
    rule = await session.get(QualityRule, rule_id)
    if rule is None:
        raise NotFoundError(f"quality rule {rule_id} not found")
    return rule


async def create_rule(session: AsyncSession, payload: QualityRuleCreate) -> QualityRule:
    if await session.get(Dataset, payload.dataset_id) is None:
        raise NotFoundError(f"dataset {payload.dataset_id} not found")
    if payload.column_id is not None:
        column = await session.get(DatasetColumn, payload.column_id)
        if column is None:
            raise NotFoundError(f"column {payload.column_id} not found")
        if column.dataset_id != payload.dataset_id:
            raise BadRequestError("column does not belong to the given dataset")
    rule = QualityRule(**payload.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def update_rule(
    session: AsyncSession, rule_id: int, payload: QualityRuleUpdate
) -> QualityRule:
    rule = await get_rule(session, rule_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return rule


async def delete_rule(session: AsyncSession, rule_id: int) -> None:
    rule = await get_rule(session, rule_id)
    await session.delete(rule)
    await session.commit()


async def list_results(
    session: AsyncSession, rule_id: int, limit: int = 20
) -> list[QualityResult]:
    await get_rule(session, rule_id)
    result = await session.execute(
        select(QualityResult)
        .where(QualityResult.rule_id == rule_id)
        .order_by(QualityResult.executed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
