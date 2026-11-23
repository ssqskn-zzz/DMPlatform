"""Lineage development specs, stored in the existing ``DataStandard`` table.

``rule`` holds the human-readable Markdown, ``extra`` holds structured clauses
that are sent to the agent.  ``code`` carries the version so publishing a new
version never mutates historical runs (each run records ``spec_version``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.models.catalog import DataStandard
from dgov.services import logs

SPEC_CATEGORY = "lineage_dev_spec"
SPEC_CODE_PREFIX = "lineage_dev_spec_v"

DEFAULT_CLAUSES = [
    "任务名与表名一致：任务名称必须与其产出表名保持一致",
    "血缘描述任务间依赖关系：一条边表示一个任务消费上游任务产出的表并产出下游表",
    "分层依赖方向为 ods → dwd → dws → ads，禁止逆向或跨层直连",
    "正式链路禁止 tmp_*、dq_monitor_* 等临时/监控表作为上下游节点",
    "增量血缘必须携带 updatetime，边按 (源表, 目标表, 血缘类型) 唯一",
]

DEFAULT_RULE_TEXT = """# 血缘关系开发规范

## 一、任务与表命名

任务名与产出表名必须一致，禁止使用无业务含义的随机后缀名。表名遵循分层前缀：
`ods_` / `dwd_` / `dws_` / `dim_` / `ads_`。

## 二、血缘语义

**数据平台的血缘逻辑是任务间依赖关系**，不是单纯的表到表引用。
一条血缘边 (A → B) 表示：产出表 B 的任务依赖产出表 A 的任务。
因此检查血缘时，应先把表级血缘映射为任务依赖图，再做链路、分层与命名合规判断。

## 三、分层约束

允许方向：ods → dwd → dws → ads；dim 可被各层引用。
逆向依赖（如 ads → ods）与跨层直连（ods → ads）需标记为由人工复核的问题。

## 四、临时表

`tmp_*`、`dq_monitor_*` 等临时表/质量监控表不得进入正式任务依赖链。

## 五、增量采集

血缘接口按 `updatetime` 增量返回，必须携带 `op=upsert|delete`；
平台侧按标记增量更新，禁止把未出现在增量批次中的边误判为失效。
"""


def spec_to_dict(standard: DataStandard) -> dict[str, Any]:
    extra = dict(standard.extra or {})
    return {
        "id": standard.id,
        "code": standard.code,
        "version": str(extra.get("version") or standard.code.rsplit("_v", 1)[-1] or "v1"),
        "name": standard.name,
        "status": standard.status,
        "category": standard.category or SPEC_CATEGORY,
        "clauses": list(extra.get("clauses") or []),
        "full_text": standard.rule or "",
    }


async def list_specs(session: AsyncSession) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(DataStandard)
                .where(DataStandard.category == SPEC_CATEGORY)
                .order_by(DataStandard.id.desc())
            )
        ).scalars()
    )
    return [spec_to_dict(row) for row in rows]


async def get_active_spec(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            select(DataStandard)
            .where(
                DataStandard.category == SPEC_CATEGORY,
                DataStandard.status == "active",
            )
            .order_by(DataStandard.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        await ensure_default_spec(session)
        return await get_active_spec(session)
    return spec_to_dict(row)


async def get_spec(session: AsyncSession, spec_id: int) -> DataStandard:
    row = await session.get(DataStandard, spec_id)
    if row is None or row.category != SPEC_CATEGORY:
        from dgov.core.exceptions import NotFoundError

        raise NotFoundError("血缘开发规范不存在")
    return row


async def ensure_default_spec(session: AsyncSession) -> DataStandard:
    existing = (
        await session.execute(
            select(DataStandard).where(DataStandard.category == SPEC_CATEGORY).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = DataStandard(
        code=f"{SPEC_CODE_PREFIX}1",
        name="血缘关系开发规范 v1",
        category=SPEC_CATEGORY,
        data_type="json",
        rule=DEFAULT_RULE_TEXT,
        description="任务名与表名一致；血缘为任务间依赖关系；分层方向与增量采集约束",
        status="active",
        extra={"version": "v1", "clauses": list(DEFAULT_CLAUSES)},
    )
    session.add(row)
    await session.flush()
    return row


async def publish_new_version(
    session: AsyncSession,
    *,
    name: str,
    clauses: list[str],
    full_text: str,
    actor: str | None,
) -> dict[str, Any]:
    """Archive the current active spec and publish a new immutable version."""
    await ensure_default_spec(session)
    active = list(
        (
            await session.execute(
                select(DataStandard).where(
                    DataStandard.category == SPEC_CATEGORY,
                    DataStandard.status == "active",
                )
            )
        ).scalars()
    )
    for row in active:
        row.status = "archived"
    existing_versions = [
        int(row.code.rsplit("_v", 1)[-1])
        for row in (
            await session.execute(
                select(DataStandard).where(DataStandard.category == SPEC_CATEGORY)
            )
        ).scalars()
        if row.code.rsplit("_v", 1)[-1].isdigit()
    ]
    version = (max(existing_versions) if existing_versions else 0) + 1
    row = DataStandard(
        code=f"{SPEC_CODE_PREFIX}{version}",
        name=name or f"血缘关系开发规范 v{version}",
        category=SPEC_CATEGORY,
        data_type="json",
        rule=full_text or DEFAULT_RULE_TEXT,
        description=f"血缘开发规范 v{version}，由 {actor or 'system'} 发布",
        status="active",
        extra={"version": f"v{version}", "clauses": list(clauses)},
    )
    session.add(row)
    await logs.write_log(
        session,
        category="lineage_spec",
        action="spec.published",
        message=f"血缘开发规范 v{version} 已发布，共 {len(clauses)} 条条款",
        actor=actor,
        resource_type="data_standard",
        resource_id=row.code,
        detail={"version": f"v{version}", "clauses": clauses},
        commit=False,
    )
    await session.flush()
    return spec_to_dict(row)
