from typing import Annotated

from fastapi import APIRouter, Query

from dgov.api.deps import GovernanceWriterDep, SessionDep
from dgov.services import modeling

router = APIRouter(prefix="/warehouse", tags=["warehouse"])


@router.get("/summary")
async def summary(session: SessionDep) -> dict:
    return await modeling.warehouse_summary(session)


@router.get("/models")
async def models(
    session: SessionDep,
    layer: str | None = None,
    domain: str | None = None,
    keyword: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    items, total = await modeling.list_models(
        session,
        layer=layer,
        domain=domain,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/rebuild")
async def rebuild(session: SessionDep, manager: GovernanceWriterDep) -> dict:
    count = await modeling.rebuild_warehouse_models(session)
    await session.commit()
    return {"models": count}
