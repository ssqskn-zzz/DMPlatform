from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.catalog import DataStandard
from dgov.schemas.catalog import StandardCreate, StandardUpdate


async def list_standards(
    session: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    *,
    category: str | None = None,
    active: bool | None = None,
) -> tuple[list[DataStandard], int]:
    stmt = select(DataStandard)
    count_stmt = select(func.count()).select_from(DataStandard)
    if keyword:
        condition = or_(
            DataStandard.code.ilike(f"%{keyword}%"),
            DataStandard.name.ilike(f"%{keyword}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if category:
        stmt = stmt.where(DataStandard.category == category)
        count_stmt = count_stmt.where(DataStandard.category == category)
    if active is not None:
        status = "active" if active else "archived"
        stmt = stmt.where(DataStandard.status == status)
        count_stmt = count_stmt.where(DataStandard.status == status)
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(DataStandard.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_standard(session: AsyncSession, standard_id: int) -> DataStandard:
    standard = await session.get(DataStandard, standard_id)
    if standard is None:
        raise NotFoundError(f"data standard {standard_id} not found")
    return standard


async def create_standard(session: AsyncSession, payload: StandardCreate) -> DataStandard:
    standard = DataStandard(**payload.model_dump())
    session.add(standard)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"data standard '{payload.code}' already exists") from exc
    await session.refresh(standard)
    return standard


async def update_standard(
    session: AsyncSession, standard_id: int, payload: StandardUpdate
) -> DataStandard:
    standard = await get_standard(session, standard_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(standard, field, value)
    await session.commit()
    await session.refresh(standard)
    return standard


async def delete_standard(session: AsyncSession, standard_id: int) -> None:
    standard = await get_standard(session, standard_id)
    await session.delete(standard)
    await session.commit()
