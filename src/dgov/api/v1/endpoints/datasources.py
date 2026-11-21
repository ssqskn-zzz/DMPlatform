from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import ConfigManagerDep, SessionDep
from dgov.schemas import DataSourceCreate, DataSourceRead, DataSourceUpdate, Page
from dgov.services import datasource as service

router = APIRouter(prefix="/datasources", tags=["datasources"])


@router.get("", response_model=Page[DataSourceRead])
async def list_data_sources(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    keyword: str | None = None,
) -> Page[DataSourceRead]:
    items, total = await service.list_data_sources(session, page, page_size, keyword)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
async def create_data_source(
    payload: DataSourceCreate, session: SessionDep, manager: ConfigManagerDep
) -> DataSourceRead:
    return await service.create_data_source(session, payload)


@router.get("/{data_source_id}", response_model=DataSourceRead)
async def get_data_source(data_source_id: int, session: SessionDep) -> DataSourceRead:
    return await service.get_data_source(session, data_source_id)


@router.patch("/{data_source_id}", response_model=DataSourceRead)
async def update_data_source(
    data_source_id: int,
    payload: DataSourceUpdate,
    session: SessionDep,
    manager: ConfigManagerDep,
) -> DataSourceRead:
    return await service.update_data_source(session, data_source_id, payload)


@router.delete("/{data_source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_source(
    data_source_id: int, session: SessionDep, manager: ConfigManagerDep
) -> Response:
    await service.delete_data_source(session, data_source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
