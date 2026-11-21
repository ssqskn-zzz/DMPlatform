from __future__ import annotations

import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.models.base import utcnow
from dgov.models.metadata import (
    MetadataColumn,
    MetadataLineage,
    MetadataTable,
    QualityCheckResult,
    QualityCheckRule,
)
from dgov.services.logs import write_log

logger = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

RULE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "code": "table_comment_missing",
        "name": "表注释完整性",
        "dimension": "完整性",
        "target": "table",
        "severity": "high",
        "description": "表必须填写业务含义清晰的注释，便于资产检索与理解。",
    },
    {
        "code": "column_comment_missing",
        "name": "字段注释完整性",
        "dimension": "完整性",
        "target": "column",
        "severity": "high",
        "description": "字段注释覆盖率应达到 100%，低于 80% 记为告警。",
    },
    {
        "code": "record_count_missing",
        "name": "表行数可采集性",
        "dimension": "完整性",
        "target": "table",
        "severity": "medium",
        "description": "元数据 API 应返回表行数，缺失时无法评估数据量与空表风险。",
    },
    {
        "code": "table_freshness",
        "name": "数据时效性",
        "dimension": "时效性",
        "target": "table",
        "severity": "high",
        "description": "最近 DML 超过 90 天告警，超过 180 天判定为失效风险。",
    },
    {
        "code": "naming_standard",
        "name": "分层命名规范",
        "dimension": "规范性",
        "target": "table",
        "severity": "medium",
        "description": "表名应使用小写字母、数字与下划线，并以 ods/dwd/dws/ads/dim 分层前缀开头。",
    },
    {
        "code": "lineage_connectivity",
        "name": "血缘连通性",
        "dimension": "关联性",
        "target": "table",
        "severity": "medium",
        "description": "表应至少存在一条上游或下游血缘，孤立表需人工确认。",
    },
    {
        "code": "type_standard",
        "name": "字段类型合理性",
        "dimension": "规范性",
        "target": "table",
        "severity": "low",
        "description": "字符串字段占比超过 85% 时提示类型建模风险。",
    },
    {
        "code": "partition_readiness",
        "name": "分区就绪度",
        "dimension": "可用性",
        "target": "table",
        "severity": "low",
        "description": "ODS/DWD 明细表应返回最新分区，便于增量调度与 freshness 监控。",
    },
)

Evaluator = Callable[[dict[str, Any]], tuple[str, float, dict[str, Any]]]


def _rule_table_comment(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    if (table.comment or "").strip():
        return "passed", 100.0, {}
    return "failed", 0.0, {"reason": "表注释为空"}


def _rule_column_comment(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    columns = ctx["columns"]
    if not columns:
        return "warning", 70.0, {"reason": "未采集到字段"}
    missing = [column.name for column in columns if not (column.comment or "").strip()]
    coverage = round((len(columns) - len(missing)) / len(columns) * 100, 1)
    status = "passed" if not missing else ("warning" if coverage >= 80 else "failed")
    return status, coverage, {"missing_count": len(missing), "missing_columns": missing[:10]}


def _rule_record_count(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    if table.record_count is None:
        return "warning", 50.0, {"reason": "未返回行数"}
    if table.record_count == 0:
        return "failed", 0.0, {"record_count": 0}
    return "passed", 100.0, {"record_count": table.record_count}


def _rule_freshness(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    if table.last_dml_time is None:
        return "warning", 60.0, {"reason": "缺少最近 DML 时间"}
    age = (utcnow() - table.last_dml_time).days
    if age > 180:
        return "failed", 30.0, {"age_days": age}
    if age > 90:
        return "warning", 70.0, {"age_days": age}
    return "passed", 100.0, {"age_days": age}


def _rule_naming(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    valid = table.layer != "OTHER" and bool(NAME_PATTERN.match(table.name.lower()))
    if valid:
        return "passed", 100.0, {"layer": table.layer}
    return "failed", 30.0, {"layer": table.layer, "reason": "未识别分层前缀或命名不规范"}


def _rule_lineage(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    degree = ctx["lineage_degree"].get(table.external_id, 0)
    if degree:
        return "passed", 100.0, {"degree": degree}
    return "warning", 60.0, {"degree": 0, "reason": "缺失上下游血缘"}


def _rule_type(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    columns = ctx["columns"]
    if not columns:
        return "warning", 70.0, {}
    strings = sum(1 for column in columns if column.type_family == "string")
    ratio = round(strings / len(columns) * 100, 1)
    if ratio > 85:
        return "warning", 70.0, {"string_ratio": ratio}
    return "passed", 100.0, {"string_ratio": ratio}


def _rule_partition(ctx: dict[str, Any]) -> tuple[str, float, dict[str, Any]]:
    table = ctx["table"]
    if table.layer in {"ODS", "DWD"}:
        if table.partition_newest:
            return "passed", 100.0, {"partition_newest": table.partition_newest}
        return "warning", 65.0, {"reason": "明细层未返回最新分区"}
    return "passed", 100.0, {}


EVALUATORS: dict[str, Evaluator] = {
    "table_comment_missing": _rule_table_comment,
    "column_comment_missing": _rule_column_comment,
    "record_count_missing": _rule_record_count,
    "table_freshness": _rule_freshness,
    "naming_standard": _rule_naming,
    "lineage_connectivity": _rule_lineage,
    "type_standard": _rule_type,
    "partition_readiness": _rule_partition,
}


async def seed_quality_rules(session: AsyncSession) -> None:
    existing = {
        rule.code for rule in (await session.execute(select(QualityCheckRule))).scalars()
    }
    for spec in RULE_SPECS:
        if spec["code"] in existing:
            continue
        session.add(QualityCheckRule(**spec))
    await session.commit()


async def run_quality_checks(
    session: AsyncSession,
    *,
    actor: str | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    started = time.perf_counter()
    await seed_quality_rules(session)
    rules = {
        rule.code: rule
        for rule in (await session.execute(select(QualityCheckRule))).scalars()
        if rule.enabled
    }
    tables = list(
        (await session.execute(select(MetadataTable).where(MetadataTable.status == "active"))).scalars()
    )
    columns_by_table: dict[int, list[MetadataColumn]] = defaultdict(list)
    for column in (
        await session.execute(
            select(MetadataColumn).where(MetadataColumn.status == "active")
        )
    ).scalars():
        columns_by_table[column.table_id].append(column)

    lineage_degree: dict[str, int] = defaultdict(int)
    for source, target in (
        await session.execute(
            select(MetadataLineage.source_table_id, MetadataLineage.target_table_id).where(
                MetadataLineage.status == "active"
            )
        )
    ).all():
        lineage_degree[source] += 1
        lineage_degree[target] += 1

    run_id = str(uuid.uuid4())
    counters = {"passed": 0, "warning": 0, "failed": 0}
    issues: list[dict[str, Any]] = []
    table_scores: dict[int, list[float]] = defaultdict(list)
    for table in tables:
        ctx = {
            "table": table,
            "columns": columns_by_table.get(table.id, []),
            "lineage_degree": lineage_degree,
        }
        for code, rule in rules.items():
            evaluator = EVALUATORS.get(code)
            if evaluator is None:
                continue
            status, score, detail = evaluator(ctx)
            counters[status] += 1
            table_scores[table.id].append(score)
            session.add(
                QualityCheckResult(
                    run_id=run_id,
                    rule_id=rule.id,
                    table_id=table.id,
                    status=status,
                    score=score,
                    detail=detail,
                )
            )
            if status in {"warning", "failed"} and len(issues) < 200:
                issues.append(
                    {
                        "table_id": table.id,
                        "table_name": table.name,
                        "rule_code": code,
                        "rule_name": rule.name,
                        "severity": rule.severity,
                        "status": status,
                        "score": score,
                        "detail": detail,
                    }
                )
        if table_scores[table.id]:
            table.quality_score = round(
                sum(table_scores[table.id]) / len(table_scores[table.id]), 2
            )

    total_checks = sum(counters.values())
    summary = {
        "run_id": run_id,
        "trigger": trigger,
        "tables": len(tables),
        "checks": total_checks,
        "passed": counters["passed"],
        "warning": counters["warning"],
        "failed": counters["failed"],
        "issue_count": counters["warning"] + counters["failed"],
        "pass_rate": round(counters["passed"] / total_checks * 100, 2) if total_checks else 100.0,
        "avg_score": round(
            sum(score for scores in table_scores.values() for score in scores)
            / max(total_checks, 1),
            2,
        ),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "executed_at": utcnow().isoformat(),
    }
    await write_log(
        session,
        category="quality_check",
        action="quality.run",
        message=(
            f"质量巡检完成：{len(tables)} 张表、{total_checks} 项检查，"
            f"通过率 {summary['pass_rate']}%，问题 {summary['issue_count']} 项"
        ),
        actor=actor,
        resource_type="quality_run",
        resource_id=run_id,
        detail=summary,
        commit=False,
    )
    await session.commit()
    logger.info("quality run %s finished: %s", run_id, summary)
    return summary


async def latest_run(session: AsyncSession) -> QualityCheckResult | None:
    return (
        await session.execute(
            select(QualityCheckResult)
            .order_by(QualityCheckResult.executed_at.desc(), QualityCheckResult.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def quality_summary(session: AsyncSession) -> dict[str, Any]:
    latest = await latest_run(session)
    if latest is None:
        return {"run_id": None, "tables": 0, "checks": 0, "pass_rate": 0.0, "issues": 0}
    rows = (
        await session.execute(
            select(QualityCheckResult.status, func.count())
            .where(QualityCheckResult.run_id == latest.run_id)
            .group_by(QualityCheckResult.status)
        )
    ).all()
    counts = dict(rows)
    total = sum(counts.values())
    return {
        "run_id": latest.run_id,
        "executed_at": latest.executed_at,
        "tables": (
            await session.execute(
                select(func.count(func.distinct(QualityCheckResult.table_id))).where(
                    QualityCheckResult.run_id == latest.run_id
                )
            )
        ).scalar_one(),
        "checks": total,
        "passed": counts.get("passed", 0),
        "warning": counts.get("warning", 0),
        "failed": counts.get("failed", 0),
        "issues": counts.get("warning", 0) + counts.get("failed", 0),
        "pass_rate": round(counts.get("passed", 0) / total * 100, 2) if total else 0.0,
    }


async def list_issues(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    if run_id is None:
        latest = await latest_run(session)
        run_id = latest.run_id if latest else None
    if run_id is None:
        return [], 0
    stmt = (
        select(QualityCheckResult, QualityCheckRule, MetadataTable)
        .join(QualityCheckRule, QualityCheckRule.id == QualityCheckResult.rule_id)
        .join(MetadataTable, MetadataTable.id == QualityCheckResult.table_id)
        .where(QualityCheckResult.run_id == run_id)
    )
    count_stmt = (
        select(func.count())
        .select_from(QualityCheckResult)
        .where(QualityCheckResult.run_id == run_id)
    )
    if status:
        stmt = stmt.where(QualityCheckResult.status == status)
        count_stmt = count_stmt.where(QualityCheckResult.status == status)
    else:
        stmt = stmt.where(QualityCheckResult.status.in_(["warning", "failed"]))
        count_stmt = count_stmt.where(QualityCheckResult.status.in_(["warning", "failed"]))
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(
                QualityCheckResult.status.desc(), QualityCheckResult.score.asc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return [
        {
            "id": result.id,
            "table_id": table.id,
            "table_name": table.name,
            "layer": table.layer,
            "rule_code": rule.code,
            "rule_name": rule.name,
            "dimension": rule.dimension,
            "severity": rule.severity,
            "status": result.status,
            "score": result.score,
            "detail": result.detail,
        }
        for result, rule, table in rows
    ], total


async def list_rules(session: AsyncSession) -> list[QualityCheckRule]:
    await seed_quality_rules(session)
    return list(
        (await session.execute(select(QualityCheckRule).order_by(QualityCheckRule.id))).scalars()
    )


async def table_results(session: AsyncSession, table_id: int) -> dict[str, Any]:
    latest = await latest_run(session)
    if latest is None:
        return {"run_id": None, "items": [], "quality_score": None}
    rows = (
        await session.execute(
            select(QualityCheckResult, QualityCheckRule)
            .join(QualityCheckRule, QualityCheckRule.id == QualityCheckResult.rule_id)
            .where(
                QualityCheckResult.run_id == latest.run_id,
                QualityCheckResult.table_id == table_id,
            )
        )
    ).all()
    table = await session.get(MetadataTable, table_id)
    return {
        "run_id": latest.run_id,
        "quality_score": table.quality_score if table else None,
        "items": [
            {
                "rule_code": rule.code,
                "rule_name": rule.name,
                "dimension": rule.dimension,
                "severity": rule.severity,
                "status": result.status,
                "score": result.score,
                "detail": result.detail,
            }
            for result, rule in rows
        ],
    }


async def prune_quality_results(session: AsyncSession, retention_days: int) -> int:
    cutoff = utcnow() - timedelta(days=max(retention_days, 1))
    deleted = await session.execute(
        delete(QualityCheckResult).where(QualityCheckResult.executed_at < cutoff)
    )
    await session.commit()
    return deleted.rowcount or 0
