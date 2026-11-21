from typing import Annotated

from fastapi import APIRouter, Query

from dgov.api.deps import SessionDep
from dgov.services import assets as service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/summary")
async def summary(session: SessionDep) -> dict:
    return await service.asset_summary(session)


@router.get("/tables")
async def tables(
    session: SessionDep,
    keyword: str | None = None,
    layer: str | None = None,
    domain: str | None = None,
    db_name: str | None = None,
    tenant: str | None = None,
    status: str = "active",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    items, total = await service.list_tables(
        session,
        keyword=keyword,
        layer=layer,
        domain=domain,
        db_name=db_name,
        tenant=tenant,
        status=status,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/tables/{table_id}")
async def table_detail(table_id: int, session: SessionDep) -> dict:
    return await service.table_detail(session, table_id)


@router.get("/columns")
async def columns(
    session: SessionDep,
    keyword: str | None = None,
    table_id: int | None = None,
    sensitive_level: str | None = None,
    data_category: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 30,
) -> dict:
    items, total = await service.list_columns(
        session,
        keyword=keyword,
        table_id=table_id,
        sensitive_level=sensitive_level,
        data_category=data_category,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
