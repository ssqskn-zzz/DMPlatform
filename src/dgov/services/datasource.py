from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.datasource import DataSource
from dgov.schemas.datasource import DataSourceCreate, DataSourceUpdate


async def list_data_sources(
    session: AsyncSession, page: int, page_size: int, keyword: str | None = None
) -> tuple[list[DataSource], int]:
    stmt = select(DataSource)
    count_stmt = select(func.count()).select_from(DataSource)
    if keyword:
        condition = or_(
            DataSource.name.ilike(f"%{keyword}%"),
            DataSource.host.ilike(f"%{keyword}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(DataSource.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_data_source(session: AsyncSession, data_source_id: int) -> DataSource:
    data_source = await session.get(DataSource, data_source_id)
    if data_source is None:
        raise NotFoundError(f"data source {data_source_id} not found")
    return data_source


async def create_data_source(
    session: AsyncSession, payload: DataSourceCreate
) -> DataSource:
    data_source = DataSource(**payload.model_dump())
    session.add(data_source)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"data source '{payload.name}' already exists") from exc
    await session.refresh(data_source)
    return data_source


async def update_data_source(
    session: AsyncSession, data_source_id: int, payload: DataSourceUpdate
) -> DataSource:
    data_source = await get_data_source(session, data_source_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(data_source, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("data source name already exists") from exc
    await session.refresh(data_source)
    return data_source


async def delete_data_source(session: AsyncSession, data_source_id: int) -> None:
    data_source = await get_data_source(session, data_source_id)
    await session.delete(data_source)
    await session.commit()
