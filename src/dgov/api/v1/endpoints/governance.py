from typing import Annotated

from fastapi import APIRouter, Query, status

from dgov.api.deps import SessionDep
from dgov.schemas.governance import (
    AgentRead,
    ApprovalResult,
    ChangeApproval,
    ChangeRequestRead,
    EvaluationRunRead,
    RunResult,
    RuntimeLogRead,
    SemanticTermRead,
    ServiceRead,
    SimulateResult,
    TodoItem,
    WorkOrderRead,
)
from dgov.services import governance as service

router = APIRouter(prefix="/workbench", tags=["workbench"])


@router.get("/overview")
async def overview(session: SessionDep) -> dict:
    """Leadership snapshot: KPIs, domain comparison, health bars and open decisions."""
    return await service.overview(session)


@router.get("/foundation")
async def foundation(session: SessionDep) -> dict:
    """One-page governance foundation snapshot and institution-to-rule mapping."""
    return await service.foundation(session)


@router.get("/todos", response_model=list[TodoItem])
async def list_todos(session: SessionDep) -> list[TodoItem]:
    return await service.list_todos(session)


@router.get("/work-orders", response_model=list[WorkOrderRead])
async def list_work_orders(session: SessionDep) -> list[WorkOrderRead]:
    return await service.list_work_orders(session)


@router.post("/work-orders/{code}/complete", response_model=WorkOrderRead)
async def complete_work_order(code: str, session: SessionDep) -> WorkOrderRead:
    return await service.complete_work_order(session, code)


@router.get("/terms", response_model=list[SemanticTermRead])
async def list_terms(session: SessionDep) -> list[SemanticTermRead]:
    return await service.list_terms(session)


@router.get("/change-requests", response_model=list[ChangeRequestRead])
async def list_change_requests(session: SessionDep) -> list[ChangeRequestRead]:
    return await service.list_change_requests(session)


@router.post("/change-requests/{code}/approve", response_model=ApprovalResult)
async def approve_change_request(
    code: str, session: SessionDep, payload: ChangeApproval | None = None
) -> ApprovalResult:
    approved_by = payload.approved_by if payload else "孙瑜"
    request, term, regression = await service.approve_change_request(session, code, approved_by)
    return ApprovalResult(
        change_request=request,
        term=term,
        affected_agents=request.affected_agents,
        regression_run=regression,
    )


@router.get("/agents", response_model=list[AgentRead])
async def list_agents(session: SessionDep) -> list[AgentRead]:
    return await service.list_agents(session)


@router.get("/agents/{code}", response_model=AgentRead)
async def get_agent(code: str, session: SessionDep) -> AgentRead:
    return await service.get_agent(session, code)


@router.post("/agents/{code}/run", response_model=RunResult, status_code=status.HTTP_201_CREATED)
async def run_agent(code: str, session: SessionDep) -> RunResult:
    run, logs = await service.run_agent(session, code)
    return RunResult(run=run, logs=logs)


@router.get("/services", response_model=list[ServiceRead])
async def list_services(session: SessionDep) -> list[ServiceRead]:
    return await service.list_services(session)


@router.get("/evaluations/latest", response_model=EvaluationRunRead | None)
async def latest_evaluation(session: SessionDep) -> EvaluationRunRead | None:
    return await service.latest_evaluation(session)


@router.get("/runtime/logs", response_model=list[RuntimeLogRead])
async def runtime_logs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> list[RuntimeLogRead]:
    return await service.list_runtime_logs(session, limit)


@router.post("/runtime/simulate", response_model=SimulateResult)
async def simulate_blocked_call(session: SessionDep) -> SimulateResult:
    """Simulate an unauthorized outbound call to prove runtime policy enforcement."""
    blocked, logs = await service.simulate_blocked_call(session)
    return SimulateResult(blocked=blocked, logs=logs)
