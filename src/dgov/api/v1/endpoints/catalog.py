from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import SessionDep
from dgov.schemas import (
    ColumnCreate,
    ColumnRead,
    ColumnUpdate,
    DatasetCreate,
    DatasetRead,
    DatasetUpdate,
    Page,
)
from dgov.services import catalog as service

router = APIRouter(tags=["catalog"])


@router.get("/datasets", response_model=Page[DatasetRead])
async def list_datasets(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    keyword: str | None = None,
    data_source_id: int | None = None,
) -> Page[DatasetRead]:
    items, total = await service.list_datasets(
        session, page, page_size, keyword, data_source_id
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/datasets", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
async def create_dataset(payload: DatasetCreate, session: SessionDep) -> DatasetRead:
    return await service.create_dataset(session, payload)


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
async def get_dataset(dataset_id: int, session: SessionDep) -> DatasetRead:
    return await service.get_dataset(session, dataset_id)


@router.patch("/datasets/{dataset_id}", response_model=DatasetRead)
async def update_dataset(
    dataset_id: int, payload: DatasetUpdate, session: SessionDep
) -> DatasetRead:
    return await service.update_dataset(session, dataset_id, payload)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: int, session: SessionDep) -> Response:
    await service.delete_dataset(session, dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/datasets/{dataset_id}/columns", response_model=list[ColumnRead])
async def list_columns(dataset_id: int, session: SessionDep) -> list[ColumnRead]:
    return await service.list_columns(session, dataset_id)


@router.post(
    "/datasets/{dataset_id}/columns",
    response_model=ColumnRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_column(
    dataset_id: int, payload: ColumnCreate, session: SessionDep
) -> ColumnRead:
    return await service.create_column(session, dataset_id, payload)


@router.patch("/columns/{column_id}", response_model=ColumnRead)
async def update_column(
    column_id: int, payload: ColumnUpdate, session: SessionDep
) -> ColumnRead:
    return await service.update_column(session, column_id, payload)


@router.delete("/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column(column_id: int, session: SessionDep) -> Response:
    await service.delete_column(session, column_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
