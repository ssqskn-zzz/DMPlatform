"""Agent endpoint registry: CRUD, connectivity/contract test, recent calls."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dgov.api.deps import AgentManagerDep, SessionDep
from dgov.core.config import settings
from dgov.core.crypto import decrypt_secret, encrypt_secret
from dgov.core.exceptions import BadRequestError, ConflictError, NotFoundError
from dgov.models.base import utcnow
from dgov.models.lineage_insight import AgentEndpoint, LineageFlowRun
from dgov.schemas.lineage_insight import (
    AgentEndpointCreate,
    AgentEndpointRead,
    AgentEndpointUpdate,
)
from dgov.services.agent_client import build_client
from dgov.services.lineage_spec import DEFAULT_RULE_TEXT

router = APIRouter(prefix="/agent-endpoints", tags=["agent-endpoints"])


def to_read(endpoint: AgentEndpoint) -> AgentEndpointRead:
    return AgentEndpointRead(
        id=endpoint.id,
        agent_code=endpoint.agent_code,
        name=endpoint.name,
        base_url=endpoint.base_url,
        auth_type=endpoint.auth_type,
        has_token=bool(endpoint.auth_token_enc),
        model=endpoint.model,
        timeout_ms=endpoint.timeout_ms,
        max_retries=endpoint.max_retries,
        enabled=endpoint.enabled,
        health_status=endpoint.health_status,
        last_checked_at=endpoint.last_checked_at,
        last_error=endpoint.last_error,
        extra=endpoint.extra,
        created_at=endpoint.created_at,
    )


async def get_endpoint(session, endpoint_id: int) -> AgentEndpoint:
    endpoint = await session.get(AgentEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFoundError("智能体端点不存在")
    return endpoint


@router.get("", response_model=list[AgentEndpointRead])
async def list_endpoints(session: SessionDep) -> list[AgentEndpointRead]:
    rows = list(
        (await session.execute(select(AgentEndpoint).order_by(AgentEndpoint.id))).scalars()
    )
    return [to_read(row) for row in rows]


@router.post("", response_model=AgentEndpointRead, status_code=201)
async def create_endpoint(
    payload: AgentEndpointCreate, session: SessionDep, manager: AgentManagerDep
) -> AgentEndpointRead:
    existing = (
        await session.execute(
            select(func.count())
            .select_from(AgentEndpoint)
            .where(AgentEndpoint.agent_code == payload.agent_code)
        )
    ).scalar_one()
    if existing:
        raise ConflictError(f"智能体 {payload.agent_code} 已注册端点")
    if payload.auth_type != "none" and not (payload.auth_token or "").strip():
        raise BadRequestError(f"auth_type={payload.auth_type} 时必须配置 auth_token")
    data = payload.model_dump(exclude={"auth_token"})
    endpoint = AgentEndpoint(
        **data,
        auth_token_enc=encrypt_secret(payload.auth_token, settings.secret_key),
    )
    session.add(endpoint)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"智能体 {payload.agent_code} 已注册端点") from exc
    await session.refresh(endpoint)
    return to_read(endpoint)


@router.get("/{endpoint_id}", response_model=AgentEndpointRead)
async def read_endpoint(endpoint_id: int, session: SessionDep) -> AgentEndpointRead:
    return to_read(await get_endpoint(session, endpoint_id))


@router.patch("/{endpoint_id}", response_model=AgentEndpointRead)
async def update_endpoint(
    endpoint_id: int,
    payload: AgentEndpointUpdate,
    session: SessionDep,
    manager: AgentManagerDep,
) -> AgentEndpointRead:
    endpoint = await get_endpoint(session, endpoint_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"auth_token"})
    for field, value in changes.items():
        setattr(endpoint, field, value)
    if "auth_token" in payload.model_dump(exclude_unset=True):
        endpoint.auth_token_enc = encrypt_secret(payload.auth_token, settings.secret_key)
    if endpoint.auth_type != "none" and not decrypt_secret(
        endpoint.auth_token_enc, settings.secret_key
    ):
        raise BadRequestError(f"auth_type={endpoint.auth_type} 时必须配置 auth_token")
    await session.commit()
    await session.refresh(endpoint)
    return to_read(endpoint)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: int, session: SessionDep, manager: AgentManagerDep
) -> None:
    endpoint = await get_endpoint(session, endpoint_id)
    await session.delete(endpoint)
    await session.commit()


@router.post("/{endpoint_id}/test")
async def test_endpoint(
    endpoint_id: int, session: SessionDep, manager: AgentManagerDep
) -> dict:
    """Real connectivity + contract smoke test; never returns the token."""
    endpoint = await get_endpoint(session, endpoint_id)
    try:
        client = await build_client(endpoint)
        result = await client.test_contract(
            {"version": "v1", "full_text": DEFAULT_RULE_TEXT},
            [],
        )
        endpoint.health_status = "ok"
        endpoint.last_error = None
    except Exception as exc:
        endpoint.health_status = "error"
        endpoint.last_error = str(exc)[:500]
        endpoint.last_checked_at = utcnow()
        await session.commit()
        raise BadRequestError(f"智能体连通性测试失败：{str(exc)[:200]}") from exc
    endpoint.last_checked_at = utcnow()
    await session.commit()
    return {"reachable": True, **result}


@router.get("/{endpoint_id}/evaluations")
async def endpoint_evaluations(
    endpoint_id: int, session: SessionDep, limit: int = 10
) -> list[dict]:
    endpoint = await get_endpoint(session, endpoint_id)
    runs = list(
        (
            await session.execute(
                select(LineageFlowRun).order_by(LineageFlowRun.id.desc()).limit(limit)
            )
        ).scalars()
    )
    result = []
    for run in runs:
        for stage in run.stages_result or []:
            if stage.get("detail", {}).get("agent_code") == endpoint.agent_code:
                result.append(
                    {
                        "run_id": run.id,
                        "stage": stage.get("key"),
                        "status": stage.get("status"),
                        "spec_version": run.spec_version,
                        "finished_at": run.finished_at,
                        **{
                            key: stage["detail"].get(key)
                            for key in ("model", "usage", "elapsed_ms")
                        },
                    }
                )
    return result
