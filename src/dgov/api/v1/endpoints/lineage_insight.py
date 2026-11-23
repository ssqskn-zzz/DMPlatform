"""Lineage insight APIs: specs, flows, runs, issues and alerts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from dgov.api.deps import (
    FlowRunnerDep,
    FlowWriterDep,
    IssueReviewerDep,
    SessionDep,
)
from dgov.core.exceptions import NotFoundError
from dgov.models.lineage_insight import LineageFlowRun, LineageIssue
from dgov.schemas.lineage_insight import (
    FlowRunRequest,
    IssueReview,
    LineageAlertRead,
    LineageFlowCreate,
    LineageFlowRead,
    LineageFlowRunRead,
    LineageFlowUpdate,
    LineageIssueRead,
    LineageSpecRead,
)
from dgov.services import lineage_insight as service
from dgov.services import lineage_spec

router = APIRouter(tags=["lineage-insight"])


class SpecPublish(BaseModel):
    name: str = Field(default="", max_length=128)
    clauses: list[str] = Field(default_factory=list)
    full_text: str = ""


# ---------------- specs ----------------

@router.get("/lineage-specs", response_model=list[LineageSpecRead])
async def list_specs(session: SessionDep) -> list[LineageSpecRead]:
    return [LineageSpecRead(**item) for item in await lineage_spec.list_specs(session)]


@router.get("/lineage-specs/active", response_model=LineageSpecRead)
async def active_spec(session: SessionDep) -> LineageSpecRead:
    return LineageSpecRead(**await lineage_spec.get_active_spec(session))


@router.post("/lineage-specs/publish", response_model=LineageSpecRead, status_code=201)
async def publish_spec(
    payload: SpecPublish, session: SessionDep, manager: FlowWriterDep
) -> LineageSpecRead:
    row = await lineage_spec.publish_new_version(
        session,
        name=payload.name,
        clauses=payload.clauses or list(lineage_spec.DEFAULT_CLAUSES),
        full_text=payload.full_text or lineage_spec.DEFAULT_RULE_TEXT,
        actor=manager.username,
    )
    await session.commit()
    return LineageSpecRead(**row)


# ---------------- flows ----------------

@router.get("/lineage-flows", response_model=list[LineageFlowRead])
async def list_flows(session: SessionDep) -> list[LineageFlowRead]:
    return [LineageFlowRead.model_validate(flow) for flow in await service.list_flows(session)]


@router.post("/lineage-flows", response_model=LineageFlowRead, status_code=201)
async def create_flow(
    payload: LineageFlowCreate, session: SessionDep, manager: FlowWriterDep
) -> LineageFlowRead:
    flow = await service.create_flow(session, payload, manager.username)
    return LineageFlowRead.model_validate(flow)


@router.get("/lineage-flows/{flow_id}", response_model=LineageFlowRead)
async def get_flow(flow_id: int, session: SessionDep) -> LineageFlowRead:
    return LineageFlowRead.model_validate(await service.get_flow(session, flow_id))


@router.patch("/lineage-flows/{flow_id}", response_model=LineageFlowRead)
async def update_flow(
    flow_id: int,
    payload: LineageFlowUpdate,
    session: SessionDep,
    manager: FlowWriterDep,
) -> LineageFlowRead:
    flow = await service.get_flow(session, flow_id)
    return LineageFlowRead.model_validate(await service.update_flow(session, flow, payload))


@router.delete("/lineage-flows/{flow_id}", status_code=204)
async def delete_flow(
    flow_id: int, session: SessionDep, manager: FlowWriterDep
) -> None:
    await service.delete_flow(session, await service.get_flow(session, flow_id))


@router.post("/lineage-flows/{flow_id}/run")
async def run_flow(
    flow_id: int,
    payload: FlowRunRequest,
    session: SessionDep,
    runner: FlowRunnerDep,
) -> dict[str, Any]:
    flow = await service.get_flow(session, flow_id)
    run, summary = await service.run_flow(
        session,
        flow,
        trigger_type="manual",
        actor=runner.username,
        force=payload.force,
        cursor_from=payload.since,
    )
    return {"summary": summary, "run": LineageFlowRunRead.model_validate(run).model_dump(mode="json")}


@router.get("/lineage-flows/{flow_id}/runs", response_model=list[LineageFlowRunRead])
async def flow_runs(
    flow_id: int, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[LineageFlowRunRead]:
    await service.get_flow(session, flow_id)
    rows = list(
        (
            await session.execute(
                select(LineageFlowRun)
                .where(LineageFlowRun.flow_id == flow_id)
                .order_by(LineageFlowRun.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [LineageFlowRunRead.model_validate(row) for row in rows]


# ---------------- runs ----------------

@router.get("/lineage-runs", response_model=list[LineageFlowRunRead])
async def list_runs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[LineageFlowRunRead]:
    rows = list(
        (
            await session.execute(
                select(LineageFlowRun).order_by(LineageFlowRun.id.desc()).limit(limit)
            )
        ).scalars()
    )
    return [LineageFlowRunRead.model_validate(row) for row in rows]


@router.get("/lineage-runs/{run_id}", response_model=LineageFlowRunRead)
async def get_run(run_id: int, session: SessionDep) -> LineageFlowRunRead:
    row = await session.get(LineageFlowRun, run_id)
    if row is None:
        raise NotFoundError("巡检运行记录不存在")
    return LineageFlowRunRead.model_validate(row)


# ---------------- issues ----------------

@router.get("/lineage-issues")
async def list_issues(
    session: SessionDep,
    run_id: int | None = None,
    status: str | None = None,
    code: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    rows, total = await service.list_issues(
        session, run_id=run_id, status=status, code=code, page=page, page_size=page_size
    )
    return {
        "items": [LineageIssueRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/lineage-issues/{issue_id}", response_model=LineageIssueRead)
async def get_issue(issue_id: int, session: SessionDep) -> LineageIssueRead:
    row = await session.get(LineageIssue, issue_id)
    if row is None:
        raise NotFoundError("血缘问题不存在")
    return LineageIssueRead.model_validate(row)


@router.patch("/lineage-issues/{issue_id}", response_model=LineageIssueRead)
async def review_issue(
    issue_id: int,
    payload: IssueReview,
    session: SessionDep,
    reviewer: IssueReviewerDep,
) -> LineageIssueRead:
    return LineageIssueRead.model_validate(
        await service.review_issue(session, issue_id, payload, reviewer.username)
    )


# ---------------- alerts ----------------

@router.get("/lineage-alerts", response_model=list[LineageAlertRead])
async def list_alerts(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[LineageAlertRead]:
    return [LineageAlertRead.model_validate(row) for row in await service.list_alerts(session, limit)]


@router.post("/lineage-alerts/{alert_id}/retry", response_model=LineageAlertRead)
async def retry_alert(
    alert_id: int, session: SessionDep, runner: FlowRunnerDep
) -> LineageAlertRead:
    return LineageAlertRead.model_validate(await service.retry_alert(session, alert_id))


# ---------------- overview ----------------

@router.get("/lineage-insight/summary")
async def insight_summary(session: SessionDep) -> dict[str, Any]:
    return await service.summary(session)
