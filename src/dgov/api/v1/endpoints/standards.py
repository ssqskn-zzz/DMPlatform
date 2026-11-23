from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import FlowWriterDep, SessionDep
from dgov.schemas import Page, StandardCreate, StandardRead, StandardUpdate
from dgov.services import standard as service

router = APIRouter(prefix="/standards", tags=["standards"])


@router.get("", response_model=Page[StandardRead])
async def list_standards(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    keyword: str | None = None,
    category: str | None = None,
    active: bool | None = None,
) -> Page[StandardRead]:
    items, total = await service.list_standards(
        session, page, page_size, keyword, category=category, active=active
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/categories", response_model=list[dict])
async def list_categories(session: SessionDep) -> list[dict]:
    # Make sure the lineage default exists so the standards page always shows
    # at least one active standard.
    from dgov.services import lineage_spec

    await lineage_spec.ensure_default_spec(session)
    await session.commit()
    return await service.list_categories(session)


@router.post("", response_model=StandardRead, status_code=status.HTTP_201_CREATED)
async def create_standard(
    payload: StandardCreate, session: SessionDep, manager: FlowWriterDep
) -> StandardRead:
    standard = await service.create_standard(session, payload, actor=manager.username)
    await session.commit()
    await session.refresh(standard)
    return standard


@router.get("/{standard_id}", response_model=StandardRead)
async def get_standard(standard_id: int, session: SessionDep) -> StandardRead:
    return await service.get_standard(session, standard_id)


@router.patch("/{standard_id}", response_model=StandardRead)
async def update_standard(
    standard_id: int,
    payload: StandardUpdate,
    session: SessionDep,
    manager: FlowWriterDep,
) -> StandardRead:
    standard = await service.update_standard(
        session, standard_id, payload, actor=manager.username
    )
    await session.commit()
    await session.refresh(standard)
    return standard


@router.delete("/{standard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_standard(
    standard_id: int, session: SessionDep, manager: FlowWriterDep
) -> Response:
    await service.delete_standard(session, standard_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
