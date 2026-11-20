from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import SessionDep
from dgov.schemas import (
    Page,
    QualityResultRead,
    QualityRuleCreate,
    QualityRuleRead,
    QualityRuleUpdate,
)
from dgov.services import quality as service

router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/rules", response_model=Page[QualityRuleRead])
async def list_rules(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    dataset_id: int | None = None,
    enabled: bool | None = None,
) -> Page[QualityRuleRead]:
    items, total = await service.list_rules(session, page, page_size, dataset_id, enabled)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/rules", response_model=QualityRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(payload: QualityRuleCreate, session: SessionDep) -> QualityRuleRead:
    return await service.create_rule(session, payload)


@router.get("/rules/{rule_id}", response_model=QualityRuleRead)
async def get_rule(rule_id: int, session: SessionDep) -> QualityRuleRead:
    return await service.get_rule(session, rule_id)


@router.patch("/rules/{rule_id}", response_model=QualityRuleRead)
async def update_rule(
    rule_id: int, payload: QualityRuleUpdate, session: SessionDep
) -> QualityRuleRead:
    return await service.update_rule(session, rule_id, payload)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, session: SessionDep) -> Response:
    await service.delete_rule(session, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rules/{rule_id}/results", response_model=list[QualityResultRead])
async def list_results(
    rule_id: int, session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> list[QualityResultRead]:
    return await service.list_results(session, rule_id, limit)
