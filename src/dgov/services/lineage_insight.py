"""Four-stage lineage insight pipeline: snapshot -> parse -> diagnose -> alert.

Design constraints (from the confirmed v2 plan):
* Input is the *current full graph* from ``meta_lineage`` plus an optional set of
  ids touched by the latest incremental batch (graph-level checks need the full
  view, incremental ids only prioritise / annotate findings).
* Both agent methods receive the active development spec AND the table-level
  lineage edges.
* The agent is read-only: every write happens here, after strict validation.
* Every stage is recorded in ``LineageFlowRun.stages_result``; identical
  (flow_version, spec_version, input_hash) pairs are served from cache.
* Any agent failure degrades to the deterministic rule engine instead of
  aborting the inspection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.base import utcnow
from dgov.models.datasource import DataSource
from dgov.models.governance import GovernanceAgent, RuntimeLog
from dgov.models.lineage_insight import (
    AgentEndpoint,
    LineageAlert,
    LineageFlow,
    LineageFlowRun,
    LineageIssue,
)
from dgov.models.metadata import MetadataLineage, MetadataTable
from dgov.schemas.agent_contract import (
    DiagnosePolicy,
    DiagnoseRequest,
    LineageEdgePayload,
    ParseRequest,
    SpecPayload,
    TaskGraph,
)
from dgov.schemas.lineage_insight import LineageFlowCreate, LineageFlowUpdate
from dgov.services import logs
from dgov.services.agent_client import build_client
from dgov.services.lineage_rules import dedupe_key, infer_layer, run_rules
from dgov.services.lineage_spec import get_active_spec

logger = logging.getLogger(__name__)

DEFAULT_STAGES: list[dict[str, Any]] = [
    {"key": "snapshot", "name": "任务血缘取数快照", "kind": "platform", "enabled": True},
    {
        "key": "parse",
        "name": "解析（规范 + 表级血缘 → 任务依赖图）",
        "kind": "agent",
        "method": "parse",
        "enabled": True,
    },
    {
        "key": "diagnose",
        "name": "问题识别（智能体 + 规则引擎）",
        "kind": "agent",
        "method": "diagnose",
        "enabled": True,
    },
    {"key": "alert", "name": "反馈告警", "kind": "platform", "enabled": True},
]
DEFAULT_SCOPE: dict[str, Any] = {"max_edges": 2000, "tenant": "", "datasource_id": None}
DEFAULT_ALERT_POLICY: dict[str, Any] = {
    "channel": "in_app",
    "min_confidence": 0.8,
    "severities": ["high", "medium"],
    "suppress_reviewed": True,
}
DEFAULT_CATEGORIES = [
    "duplicate_edge",
    "dangling_id",
    "invalid_layer_dependency",
    "temp_table_in_chain",
    "circular_dependency",
    "self_loop",
    "broken_chain",
    "task_name_mismatch",
    "incremental_edge_anomaly",
]


# --------------------------------------------------------------------------
# flow definitions
# --------------------------------------------------------------------------

async def list_flows(session: AsyncSession) -> list[LineageFlow]:
    return list(
        (await session.execute(select(LineageFlow).order_by(LineageFlow.id))).scalars()
    )


async def get_flow(session: AsyncSession, flow_id: int) -> LineageFlow:
    flow = await session.get(LineageFlow, flow_id)
    if flow is None:
        raise NotFoundError("巡检流程不存在")
    return flow


async def create_flow(
    session: AsyncSession, payload: LineageFlowCreate, actor: str | None
) -> LineageFlow:
    existing = (
        await session.execute(
            select(func.count())
            .select_from(LineageFlow)
            .where(LineageFlow.code == payload.code)
        )
    ).scalar_one()
    if existing:
        raise ConflictError(f"流程编码 {payload.code} 已存在")
    version = int(existing) + 1
    data = payload.model_dump()
    flow = LineageFlow(
        **{
            **data,
            "version": version,
            "stages": list(data["stages"]) or [dict(item) for item in DEFAULT_STAGES],
            "scope": dict(data["scope"]) or dict(DEFAULT_SCOPE),
            "alert_policy": dict(data["alert_policy"]) or dict(DEFAULT_ALERT_POLICY),
            "created_by": actor,
        }
    )
    session.add(flow)
    await session.commit()
    await session.refresh(flow)
    return flow


async def update_flow(
    session: AsyncSession, flow: LineageFlow, payload: LineageFlowUpdate
) -> LineageFlow:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(flow, field, value)
    # editing a flow bumps its version so historical runs stay explainable
    flow.version = (flow.version or 1) + 1
    await session.commit()
    await session.refresh(flow)
    return flow


async def delete_flow(session: AsyncSession, flow: LineageFlow) -> None:
    await session.delete(flow)
    await session.commit()


async def ensure_default_flow(session: AsyncSession, actor: str = "system") -> LineageFlow:
    existing = (
        await session.execute(
            select(LineageFlow).where(LineageFlow.code == "lineage_insight_default").limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    flow = LineageFlow(
        code="lineage_insight_default",
        name="任务血缘智能巡检（默认）",
        version=1,
        enabled=True,
        trigger_type="event",
        interval_unit="hour",
        interval_value=1,
        scope=dict(DEFAULT_SCOPE),
        stages=[dict(item) for item in DEFAULT_STAGES],
        alert_policy=dict(DEFAULT_ALERT_POLICY),
        created_by=actor,
    )
    session.add(flow)
    await session.flush()
    return flow


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------

async def build_snapshot(
    session: AsyncSession,
    scope: dict[str, Any] | None,
    *,
    changed_ids: set[str] | None = None,
) -> dict[str, Any]:
    scope = {**DEFAULT_SCOPE, **(scope or {})}
    stmt = select(MetadataLineage).where(MetadataLineage.status == "active")
    if scope.get("datasource_id"):
        stmt = stmt.where(MetadataLineage.datasource_id == int(scope["datasource_id"]))
    rows = list((await session.execute(stmt)).scalars())
    max_edges = int(scope.get("max_edges") or settings.lineage_max_edges)

    edges: list[dict[str, Any]] = []
    for row in rows[:max_edges]:
        src_name = row.source_table_name or ""
        tgt_name = row.target_table_name or ""
        edges.append(
            {
                "id": row.id,
                "source_table_id": row.source_table_id,
                "target_table_id": row.target_table_id,
                "source_table": src_name,
                "target_table": tgt_name,
                "source_schema": row.source_schema or "",
                "target_schema": row.target_schema or "",
                "type": str(row.lineage_type),
                "op": "upsert",
                "source_layer": infer_layer(src_name, row.source_schema or ""),
                "target_layer": infer_layer(tgt_name, row.target_schema or ""),
            }
        )

    known_ids = set(
        (
            await session.execute(
                select(MetadataTable.external_id).where(MetadataTable.status == "active")
            )
        ).scalars()
    )
    # Raw (non-deduplicated) feed + nodes touched by the latest incremental
    # batch are kept on the API datasource config by run_collection.
    raw_edges: list[dict[str, Any]] = []
    config_changed: list[str] = []
    sources = list((await session.execute(select(DataSource))).scalars())
    scoped_source = scope.get("datasource_id")
    for source in sources:
        if scoped_source and source.id != int(scoped_source):
            continue
        config = dict(source.config or {})
        if config.get("lineage_last_raw_edges"):
            raw_edges = list(config["lineage_last_raw_edges"])
            config_changed = list(config.get("lineage_last_changed") or [])
            break
    hash_input = {"edges": edges, "raw_edges": raw_edges}
    payload_hash = hashlib.sha256(
        json.dumps(hash_input, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:40]
    layers: dict[str, int] = {}
    for edge in edges:
        layers[edge["source_layer"]] = layers.get(edge["source_layer"], 0) + 1

    return {
        "edges": edges,
        "raw_edges": raw_edges,
        "input_hash": payload_hash,
        "known_table_ids": sorted(known_ids),
        "changed_ids": sorted(set(changed_ids or set()) | set(config_changed)),
        "scope": scope,
        "stats": {
            "edge_count": len(edges),
            "raw_edge_count": len(raw_edges),
            "total_edges": len(rows),
            "truncated": len(rows) > max_edges,
            "known_table_count": len(known_ids),
            "layer_distribution": layers,
        },
        "sample_edges": edges[:20],
    }


# --------------------------------------------------------------------------
# runtime permission enforcement (four dimensions of gov_service)
# --------------------------------------------------------------------------

async def assert_service_permission(
    session: AsyncSession, agent_code: str, dimension: str, request_id: str
) -> tuple[bool, str]:
    """Return (allowed, reason).  Writes a BLOCK trail when denied."""
    field = {
        "system": "allow_system",
        "retrieval": "allow_retrieval",
        "training": "allow_training",
        "output": "allow_output",
    }.get(dimension)
    if field is None:
        return True, ""
    agent = (
        await session.execute(select(GovernanceAgent).where(GovernanceAgent.code == agent_code))
    ).scalar_one_or_none()
    if agent is None:
        return True, f"智能体 {agent_code} 未登记目录，按默认策略放行"
    services = list(agent.services or [])
    if not services:
        return True, "未绑定服务契约，按默认策略放行"
    allowed = any(getattr(service, field, "✕") == "✓" for service in services)
    if allowed:
        return True, ""
    session.add(
        RuntimeLog(
            level="BLOCK",
            message=f"auth: {agent_code} / {request_id} → {dimension} 未授权，拒绝调用",
        )
    )
    await session.flush()
    return False, f"服务契约未授权 {dimension} 维度"


# --------------------------------------------------------------------------
# main orchestration
# --------------------------------------------------------------------------

async def run_flow(
    session: AsyncSession,
    flow: LineageFlow,
    *,
    trigger_type: str = "manual",
    actor: str | None = None,
    ingest_run_id: int | None = None,
    cursor_from: str | None = None,
    cursor_to: str | None = None,
    changed_ids: set[str] | None = None,
    force: bool = False,
    transport: Any | None = None,
    fail_header: str | None = None,
) -> tuple[LineageFlowRun, dict[str, Any]]:
    started = time.perf_counter()
    request_id = str(uuid.uuid4())
    # Capture identifiers before any commit/rollback: rollback expires ORM
    # instances and touching them afterwards would trigger a lazy load outside
    # the async greenlet.
    flow_id = flow.id
    flow_name = flow.name
    flow_code = flow.code
    flow_version = flow.version
    spec = await get_active_spec(session)
    snapshot = await build_snapshot(session, flow.scope, changed_ids=changed_ids)
    input_hash = hashlib.sha256(
        f"{flow_version}|{spec['version']}|{snapshot['input_hash']}".encode()
    ).hexdigest()[:40]

    if not force:
        cached = (
            await session.execute(
                select(LineageFlowRun)
                .where(
                    LineageFlowRun.flow_id == flow.id,
                    LineageFlowRun.input_hash == input_hash,
                    LineageFlowRun.status.in_(["completed", "partial"]),
                )
                .order_by(LineageFlowRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cached is not None:
            return cached, {"status": "cached", "run_id": cached.id, "input_hash": input_hash}

    run = LineageFlowRun(
        flow_id=flow.id,
        flow_code=flow.code,
        flow_version=flow.version,
        trigger_type=trigger_type,
        actor=actor,
        status="running",
        spec_version=spec["version"],
        ingest_run_id=ingest_run_id,
        cursor_from=cursor_from,
        cursor_to=cursor_to,
        input_hash=input_hash,
        snapshot={
            "scope": snapshot["scope"],
            "stats": snapshot["stats"],
            "sample_edges": snapshot["sample_edges"],
            "changed_ids": snapshot["changed_ids"],
            "spec_version": spec["version"],
            "request_id": request_id,
        },
        started_at=utcnow(),
    )
    session.add(run)
    await session.flush()
    run_id = run.id

    stages = list(flow.stages) or [dict(item) for item in DEFAULT_STAGES]
    stage_results: list[dict[str, Any]] = []
    degraded = False
    edges = snapshot["edges"]
    edge_payloads = [LineageEdgePayload.model_validate(edge) for edge in edges]
    spec_payload = SpecPayload(
        version=spec["version"],
        clauses=spec["clauses"],
        full_text=spec["full_text"],
    )
    parse_graph = TaskGraph()
    all_issues: list[dict[str, Any]] = []
    endpoint_cache: dict[str, AgentEndpoint | None] = {}

    async def resolve_endpoint(stage: dict[str, Any]) -> AgentEndpoint | None:
        code = str(stage.get("agent_code") or "")
        if code not in endpoint_cache:
            stmt = select(AgentEndpoint).where(AgentEndpoint.enabled.is_(True))
            if code:
                stmt = stmt.where(AgentEndpoint.agent_code == code)
            endpoint_cache[code] = (
                await session.execute(stmt.order_by(AgentEndpoint.id).limit(1))
            ).scalar_one_or_none()
        return endpoint_cache[code]

    async def call_agent(stage: dict[str, Any], method: str) -> tuple[Any | None, str, dict[str, Any]]:
        nonlocal degraded
        endpoint = await resolve_endpoint(stage)
        if endpoint is None:
            degraded = True
            return None, "no_endpoint", {"agent_code": "", "model": "", "usage": {}}
        allowed, reason = await assert_service_permission(
            session, endpoint.agent_code, "retrieval", request_id
        )
        if not allowed:
            degraded = True
            return None, f"blocked:{reason}", {"agent_code": endpoint.agent_code, "model": "", "usage": {}}
        try:
            client = await build_client(endpoint, transport=transport, fail_header=fail_header)
            t0 = time.perf_counter()
            if method == "parse":
                response = await client.parse(
                    ParseRequest(
                        request_id=request_id,
                        spec=spec_payload,
                        edges=edge_payloads,
                        scope={"mode": trigger_type, "ingest_run_id": ingest_run_id},
                    )
                )
            else:
                response = await client.diagnose(
                    DiagnoseRequest(
                        request_id=request_id,
                        spec=spec_payload,
                        edges=edge_payloads,
                        task_graph=parse_graph,
                        policy=DiagnosePolicy(
                            categories=list(
                                (flow.alert_policy or {}).get("categories")
                                or DEFAULT_CATEGORIES
                            ),
                            min_confidence=float(
                                (flow.alert_policy or {}).get("min_agent_confidence", 0.6)
                            ),
                        ),
                    )
                )
            elapsed = int((time.perf_counter() - t0) * 1000)
            digest = {
                "agent_code": endpoint.agent_code,
                "model": response.model,
                "usage": response.usage,
                "elapsed_ms": elapsed,
            }
            await logs.write_log(
                session,
                category="agent_call",
                action=f"agent.{method}",
                message=f"智能体 {endpoint.agent_code} {method} 调用成功（{elapsed} ms）",
                actor=actor,
                resource_type="lineage_flow_run",
                resource_id=run.id,
                detail={
                    "request_id": request_id,
                    "spec_version": spec["version"],
                    "edge_count": len(edges),
                    **digest,
                },
                commit=False,
            )
            return response, "ok", digest
        except Exception as exc:  # degrade, never abort the inspection
            degraded = True
            logger.warning("agent %s failed: %s", method, exc)
            await logs.write_log(
                session,
                category="agent_call",
                action=f"agent.{method}.failed",
                message=f"智能体 {method} 调用失败，已降级：{str(exc)[:160]}",
                level="warning",
                actor=actor,
                resource_type="lineage_flow_run",
                resource_id=run.id,
                detail={"request_id": request_id, "error": str(exc)[:400]},
                commit=False,
            )
            return None, f"error:{type(exc).__name__}", {
                "agent_code": endpoint.agent_code if endpoint else "",
                "model": "",
                "usage": {},
            }

    try:
        # ---- stage 1: snapshot -----------------------------------------
        s0 = time.perf_counter()
        stage_results.append(
            {
                "key": "snapshot",
                "name": "任务血缘取数快照",
                "status": "success",
                "elapsed_ms": int((time.perf_counter() - s0) * 1000),
                "detail": {**snapshot["stats"], "input_hash": snapshot["input_hash"]},
            }
        )

        # ---- stage 2: parse ---------------------------------------------
        s0 = time.perf_counter()
        parse_stage = next((s for s in stages if s.get("key") == "parse"), {})
        parse_response, parse_status, parse_digest = await call_agent(parse_stage, "parse")
        if parse_response is not None:
            parse_graph = parse_response.result.task_graph
            violations = list(parse_response.result.violations)
        else:
            # local fallback: map table names to task names following the spec
            nodes: dict[str, dict[str, str]] = {}
            fallback_edges = []
            violations = []
            for edge in edges:
                for name, layer in (
                    (edge["source_table"], edge["source_layer"]),
                    (edge["target_table"], edge["target_layer"]),
                ):
                    key = name or edge["source_table_id"]
                    nodes.setdefault(key, {"task": key, "table": name, "layer": layer})
                fallback_edges.append(
                    {
                        "source_task": edge["source_table"],
                        "target_task": edge["target_table"],
                        "confidence": 0.5,
                    }
                )
            parse_graph = TaskGraph.model_validate({"nodes": list(nodes.values()), "edges": fallback_edges})
        stage_results.append(
            {
                "key": "parse",
                "name": "解析（规范 + 表级血缘 → 任务依赖图）",
                "status": "degraded" if parse_response is None else "success",
                "elapsed_ms": int((time.perf_counter() - s0) * 1000),
                "call_status": parse_status,
                "detail": {
                    **parse_digest,
                    "spec_version": spec["version"],
                    "spec_clauses": len(spec["clauses"]),
                    "edge_count": len(edges),
                    "task_nodes": len(parse_graph.nodes),
                    "task_edges": len(parse_graph.edges),
                    "violations": violations[:20],
                },
            }
        )

        # ---- stage 3: diagnose ------------------------------------------
        s0 = time.perf_counter()
        known_ids = set(snapshot["known_table_ids"])
        rule_issues = run_rules(
            edges,
            known_table_ids=known_ids,
            changed_ids=set(snapshot["changed_ids"]),
            raw_edges=snapshot.get("raw_edges") or None,
        )
        diagnose_stage = next((s for s in stages if s.get("key") == "diagnose"), {})
        diagnose_response, diagnose_status, diagnose_digest = await call_agent(
            diagnose_stage, "diagnose"
        )
        valid_affected = known_ids | {
            str(edge[key])
            for edge in edges
            for key in ("source_table_id", "target_table_id", "source_table", "target_table")
        }
        agent_issue_count = 0
        if diagnose_response is not None:
            for item in diagnose_response.result.issues:
                if any(str(x) not in valid_affected for x in item.affected):
                    continue  # drop hallucinated references
                all_issues.append(
                    {
                        "code": item.code,
                        "category": item.category or item.code,
                        "severity": item.severity,
                        "confidence": float(item.confidence),
                        "source": "agent",
                        "title": f"[智能体] {item.code}",
                        "description": item.evidence,
                        "affected": list(item.affected),
                        "evidence": {"detail": item.evidence, "model": diagnose_digest.get("model")},
                        "suggestion": item.suggestion,
                        "dedupe_key": dedupe_key(item.code, list(item.affected)),
                    }
                )
                agent_issue_count += 1
        all_issues.extend(rule_issues)

        merged: dict[str, dict[str, Any]] = {}
        for issue in all_issues:
            key = issue["dedupe_key"]
            current = merged.get(key)
            if current is None or issue["confidence"] > current["confidence"]:
                if current is not None and current["source"] != issue["source"]:
                    issue = {**issue, "source": "agent+rule"}
                merged[key] = issue
        stage_results.append(
            {
                "key": "diagnose",
                "name": "问题识别（智能体 + 规则引擎）",
                "status": "degraded" if diagnose_response is None else "success",
                "elapsed_ms": int((time.perf_counter() - s0) * 1000),
                "call_status": diagnose_status,
                "detail": {
                    **diagnose_digest,
                    "spec_version": spec["version"],
                    "edge_count": len(edges),
                    "spec_clauses": len(spec["clauses"]),
                    "rule_issues": len(rule_issues),
                    "agent_issues": agent_issue_count,
                    "merged_issues": len(merged),
                },
            }
        )

        # ---- persist issues ---------------------------------------------
        created_issues: list[LineageIssue] = []
        for issue in merged.values():
            row = LineageIssue(run_id=run.id, **issue)
            session.add(row)
            created_issues.append(row)
        await session.flush()

        # ---- stage 4: feedback & alerts ---------------------------------
        s0 = time.perf_counter()
        policy = {**DEFAULT_ALERT_POLICY, **(flow.alert_policy or {})}
        min_confidence = float(policy.get("min_confidence", 0.8))
        allowed_severities = set(policy.get("severities") or ["high", "medium", "low"])
        suppress_reviewed = bool(policy.get("suppress_reviewed", True))
        dedupe_keys = [row.dedupe_key for row in created_issues]
        existing_alerts: set[str] = set()
        if dedupe_keys:
            existing_alerts = set(
                (
                    await session.execute(
                        select(LineageAlert.dedupe_key).where(
                            LineageAlert.dedupe_key.in_(dedupe_keys),
                            LineageAlert.status.in_(["sent", "suppressed"]),
                        )
                    )
                ).scalars()
            )
        reviewed_keys: set[str] = set()
        if suppress_reviewed and dedupe_keys:
            reviewed_keys = set(
                (
                    await session.execute(
                        select(LineageIssue.dedupe_key).where(
                            LineageIssue.dedupe_key.in_(dedupe_keys),
                            LineageIssue.status.in_(["ignored", "resolved"]),
                        )
                    )
                ).scalars()
            )
        alert_count = 0
        suppressed = 0
        for issue in created_issues:
            if issue.confidence < min_confidence or issue.severity not in allowed_severities:
                continue
            if issue.dedupe_key in existing_alerts or issue.dedupe_key in reviewed_keys:
                suppressed += 1
                continue
            allowed, reason = await assert_service_permission(
                session,
                parse_digest.get("agent_code") or diagnose_digest.get("agent_code") or "",
                "output",
                request_id,
            )
            if not allowed and policy.get("channel") != "in_app":
                session.add(
                    LineageAlert(
                        run_id=run.id,
                        issue_id=issue.id,
                        channel=str(policy.get("channel")),
                        level=issue.severity,
                        status="suppressed",
                        payload={"reason": reason},
                        dedupe_key=issue.dedupe_key,
                    )
                )
                suppressed += 1
                continue
            alert = LineageAlert(
                run_id=run.id,
                issue_id=issue.id,
                channel="in_app" if policy.get("channel") == "in_app" else str(policy.get("channel")),
                level=issue.severity,
                status="sent",
                payload={
                    "title": issue.title,
                    "code": issue.code,
                    "confidence": issue.confidence,
                    "affected": issue.affected,
                },
                dedupe_key=issue.dedupe_key,
                sent_at=utcnow(),
            )
            session.add(alert)
            existing_alerts.add(issue.dedupe_key)
            alert_count += 1
        stage_results.append(
            {
                "key": "alert",
                "name": "反馈告警",
                "status": "success",
                "elapsed_ms": int((time.perf_counter() - s0) * 1000),
                "detail": {
                    "alerts": alert_count,
                    "suppressed": suppressed,
                    "channel": policy.get("channel"),
                    "min_confidence": min_confidence,
                },
            }
        )

        run.status = "partial" if degraded else "completed"
        run.degraded = degraded
        run.stages_result = stage_results
        run.issue_count = len(created_issues)
        run.alert_count = alert_count
        run.finished_at = utcnow()
        await logs.write_log(
            session,
            category="lineage_insight",
            action="flow.completed",
            message=(
                f"血缘巡检「{flow.name}」完成（{run.status}）："
                f"{len(edges)} 条边、{len(created_issues)} 个问题、{alert_count} 条告警"
            ),
            level="warning" if degraded else "info",
            actor=actor,
            resource_type="lineage_flow_run",
            resource_id=run.id,
            detail={
                "spec_version": spec["version"],
                "input_hash": input_hash,
                "trigger": trigger_type,
                "degraded": degraded,
            },
            commit=False,
        )
        await session.commit()
        await session.refresh(run)
        summary = {
            "status": run.status,
            "run_id": run.id,
            "input_hash": input_hash,
            "spec_version": spec["version"],
            "edge_count": len(edges),
            "issue_count": run.issue_count,
            "alert_count": run.alert_count,
            "degraded": degraded,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "stages": [item["status"] for item in stage_results],
        }
        return run, summary
    except Exception as exc:
        logger.exception("lineage insight flow %s failed", flow_id)
        await session.rollback()
        run = await session.get(LineageFlowRun, run_id)
        if run is None:
            run = LineageFlowRun(
                flow_id=flow_id,
                flow_code=flow_code,
                flow_version=flow_version,
                trigger_type=trigger_type,
                actor=actor,
                status="failed",
                spec_version=spec["version"],
                input_hash=input_hash,
                started_at=utcnow(),
            )
            session.add(run)
        run.status = "failed"
        run.stages_result = stage_results
        run.error_message = str(exc)[:2000]
        run.finished_at = utcnow()
        await logs.write_log(
            session,
            category="lineage_insight",
            action="flow.failed",
            message=f"血缘巡检「{flow_name}」失败：{str(exc)[:180]}",
            level="error",
            actor=actor,
            resource_type="lineage_flow",
            resource_id=flow_id,
            detail={"error": str(exc)[:600]},
            commit=False,
        )
        await session.commit()
        await session.refresh(run)
        return run, {"status": "failed", "run_id": run.id, "error": str(exc)}


# --------------------------------------------------------------------------
# event / schedule triggers
# --------------------------------------------------------------------------

async def trigger_after_collection(
    session: AsyncSession,
    *,
    datasource_id: int,
    ingest_run_id: int,
    actor: str | None,
    changed_ids: set[str] | None = None,
    cursor_to: str | None = None,
) -> list[int]:
    if not settings.lineage_insight_enabled or not settings.lineage_insight_auto_trigger:
        return []
    flows = list(
        (
            await session.execute(
                select(LineageFlow).where(
                    LineageFlow.enabled.is_(True),
                    LineageFlow.trigger_type == "event",
                )
            )
        ).scalars()
    )
    run_ids: list[int] = []
    for flow in flows:
        scope = dict(flow.scope or {})
        scoped_source = scope.get("datasource_id")
        if scoped_source and int(scoped_source) != datasource_id:
            continue
        flow_id = flow.id
        try:
            run, _ = await run_flow(
                session,
                flow,
                trigger_type="event",
                actor=actor or "scheduler",
                ingest_run_id=ingest_run_id,
                cursor_to=cursor_to,
                changed_ids=changed_ids,
            )
            run_ids.append(run.id)
        except Exception as exc:  # never let insight break collection
            logger.warning("event-triggered insight failed for flow %s: %s", flow_id, exc)
    return run_ids


async def run_due_flows(session: AsyncSession) -> int:
    due = list(
        (
            await session.execute(
                select(LineageFlow).where(
                    LineageFlow.enabled.is_(True),
                    LineageFlow.trigger_type == "schedule",
                    LineageFlow.next_run_at.is_not(None),
                    LineageFlow.next_run_at <= utcnow(),
                )
            )
        ).scalars()
    )
    for flow in due:
        multiplier = {"minute": 60, "hour": 3600, "day": 86400}.get(flow.interval_unit, 3600)
        flow.next_run_at = utcnow() + timedelta(seconds=multiplier * flow.interval_value)
        await session.flush()
        flow_id = flow.id
        try:
            await run_flow(session, flow, trigger_type="schedule", actor="scheduler")
            # Persist the next schedule even when run_flow returns a cached run
            # (the cached branch does not commit on its own).
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("scheduled insight failed for flow %s: %s", flow_id, exc)
    return len(due)


# --------------------------------------------------------------------------
# issues / alerts / summary
# --------------------------------------------------------------------------

async def list_issues(
    session: AsyncSession,
    *,
    run_id: int | None = None,
    status: str | None = None,
    code: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[LineageIssue], int]:
    stmt = select(LineageIssue)
    count_stmt = select(func.count()).select_from(LineageIssue)
    for condition in (
        LineageIssue.run_id == run_id if run_id else None,
        LineageIssue.status == status if status else None,
        LineageIssue.code == code if code else None,
    ):
        if condition is not None:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(LineageIssue.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    return rows, total


async def review_issue(
    session: AsyncSession, issue_id: int, payload: Any, actor: str | None
) -> LineageIssue:
    issue = await session.get(LineageIssue, issue_id)
    if issue is None:
        raise NotFoundError("血缘问题不存在")
    issue.status = payload.status
    issue.assignee = payload.assignee
    issue.review_note = payload.review_note
    issue.reviewed_by = actor
    issue.reviewed_at = utcnow()
    await logs.write_log(
        session,
        category="lineage_insight",
        action="issue.reviewed",
        message=f"血缘问题 #{issue.id} 复核为 {issue.status}",
        actor=actor,
        resource_type="lineage_issue",
        resource_id=issue.id,
        detail={"code": issue.code, "note": payload.review_note},
        commit=False,
    )
    await session.commit()
    await session.refresh(issue)
    return issue


async def list_alerts(session: AsyncSession, limit: int = 50) -> list[LineageAlert]:
    return list(
        (
            await session.execute(
                select(LineageAlert).order_by(LineageAlert.id.desc()).limit(limit)
            )
        ).scalars()
    )


async def retry_alert(session: AsyncSession, alert_id: int) -> LineageAlert:
    alert = await session.get(LineageAlert, alert_id)
    if alert is None:
        raise NotFoundError("告警记录不存在")
    alert.retry_count += 1
    alert.status = "sent"
    alert.sent_at = utcnow()
    alert.error = None
    await session.commit()
    await session.refresh(alert)
    return alert


async def summary(session: AsyncSession) -> dict[str, Any]:
    runs = (await session.execute(select(func.count()).select_from(LineageFlowRun))).scalar_one()
    issues = (await session.execute(select(func.count()).select_from(LineageIssue))).scalar_one()
    alerts = (await session.execute(select(func.count()).select_from(LineageAlert))).scalar_one()
    open_issues = (
        await session.execute(
            select(func.count()).select_from(LineageIssue).where(LineageIssue.status == "open")
        )
    ).scalar_one()
    by_code = (
        await session.execute(
            select(LineageIssue.code, func.count()).group_by(LineageIssue.code)
        )
    ).all()
    latest = (
        await session.execute(
            select(LineageFlowRun).order_by(LineageFlowRun.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    spec = await get_active_spec(session)
    active_flows = (
        await session.execute(
            select(func.count()).select_from(LineageFlow).where(LineageFlow.enabled.is_(True))
        )
    ).scalar_one()
    return {
        "runs": runs,
        "issues": issues,
        "open_issues": open_issues,
        "alerts": alerts,
        "active_flows": active_flows,
        "spec_version": spec["version"],
        "spec_clauses": len(spec["clauses"]),
        "by_code": [{"code": code, "count": count} for code, count in by_code],
        "latest_run": None
        if latest is None
        else {
            "id": latest.id,
            "status": latest.status,
            "spec_version": latest.spec_version,
            "issue_count": latest.issue_count,
            "alert_count": latest.alert_count,
            "degraded": latest.degraded,
            "finished_at": latest.finished_at,
        },
    }
