from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.catalog import Dataset, DatasetColumn
from dgov.models.datasource import DataSource
from dgov.schemas.catalog import (
    ColumnCreate,
    ColumnUpdate,
    DatasetCreate,
    DatasetUpdate,
)


async def list_datasets(
    session: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    data_source_id: int | None = None,
) -> tuple[list[Dataset], int]:
    stmt = select(Dataset)
    count_stmt = select(func.count()).select_from(Dataset)
    if keyword:
        condition = or_(
            Dataset.name.ilike(f"%{keyword}%"),
            Dataset.display_name.ilike(f"%{keyword}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if data_source_id is not None:
        stmt = stmt.where(Dataset.data_source_id == data_source_id)
        count_stmt = count_stmt.where(Dataset.data_source_id == data_source_id)
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(Dataset.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def get_dataset(session: AsyncSession, dataset_id: int) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"dataset {dataset_id} not found")
    return dataset


async def create_dataset(session: AsyncSession, payload: DatasetCreate) -> Dataset:
    if await session.get(DataSource, payload.data_source_id) is None:
        raise NotFoundError(f"data source {payload.data_source_id} not found")
    dataset = Dataset(**payload.model_dump())
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("dataset already registered for this data source") from exc
    await session.refresh(dataset)
    return dataset


async def update_dataset(
    session: AsyncSession, dataset_id: int, payload: DatasetUpdate
) -> Dataset:
    dataset = await get_dataset(session, dataset_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(dataset, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("dataset name already registered") from exc
    await session.refresh(dataset)
    return dataset


async def delete_dataset(session: AsyncSession, dataset_id: int) -> None:
    dataset = await get_dataset(session, dataset_id)
    await session.delete(dataset)
    await session.commit()


async def list_columns(session: AsyncSession, dataset_id: int) -> list[DatasetColumn]:
    await get_dataset(session, dataset_id)
    result = await session.execute(
        select(DatasetColumn)
        .where(DatasetColumn.dataset_id == dataset_id)
        .order_by(DatasetColumn.ordinal, DatasetColumn.id)
    )
    return list(result.scalars().all())


async def create_column(
    session: AsyncSession, dataset_id: int, payload: ColumnCreate
) -> DatasetColumn:
    await get_dataset(session, dataset_id)
    column = DatasetColumn(dataset_id=dataset_id, **payload.model_dump())
    session.add(column)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"column '{payload.name}' already exists in dataset {dataset_id}") from exc
    await session.refresh(column)
    return column


async def update_column(
    session: AsyncSession, column_id: int, payload: ColumnUpdate
) -> DatasetColumn:
    column = await session.get(DatasetColumn, column_id)
    if column is None:
        raise NotFoundError(f"column {column_id} not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(column, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("column name already exists in this dataset") from exc
    await session.refresh(column)
    return column


async def delete_column(session: AsyncSession, column_id: int) -> None:
    column = await session.get(DatasetColumn, column_id)
    if column is None:
        raise NotFoundError(f"column {column_id} not found")
    await session.delete(column)
    await session.commit()
