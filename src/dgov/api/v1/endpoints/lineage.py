from typing import Annotated

from fastapi import APIRouter, Query

from dgov.api.deps import SessionDep
from dgov.services import assets as service

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get("/edges")
async def edges(
    session: SessionDep,
    keyword: str | None = None,
    datasource_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 30,
) -> dict:
    items, total = await service.list_table_edges(
        session,
        keyword=keyword,
        datasource_id=datasource_id,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/graph")
async def graph(
    session: SessionDep,
    table_id: int,
    depth: Annotated[int, Query(ge=1, le=5)] = 2,
    direction: Annotated[str, Query(pattern="^(both|upstream|downstream)$")] = "both",
) -> dict:
    return await service.lineage_graph(
        session, table_id=table_id, depth=depth, direction=direction
    )


@router.get("/fields")
async def fields(
    session: SessionDep,
    table_id: int | None = None,
    keyword: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 30,
) -> dict:
    items, total = await service.list_field_lineage(
        session, table_id=table_id, keyword=keyword, page=page, page_size=page_size
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/impact/{table_id}")
async def impact(
    table_id: int,
    session: SessionDep,
    depth: Annotated[int, Query(ge=1, le=5)] = 4,
) -> dict:
    return await service.impact_analysis(session, table_id, depth)
