from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import SessionDep
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


@router.post("", response_model=StandardRead, status_code=status.HTTP_201_CREATED)
async def create_standard(payload: StandardCreate, session: SessionDep) -> StandardRead:
    return await service.create_standard(session, payload)


@router.get("/{standard_id}", response_model=StandardRead)
async def get_standard(standard_id: int, session: SessionDep) -> StandardRead:
    return await service.get_standard(session, standard_id)


@router.patch("/{standard_id}", response_model=StandardRead)
async def update_standard(
    standard_id: int, payload: StandardUpdate, session: SessionDep
) -> StandardRead:
    return await service.update_standard(session, standard_id, payload)


@router.delete("/{standard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_standard(standard_id: int, session: SessionDep) -> Response:
    await service.delete_standard(session, standard_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
