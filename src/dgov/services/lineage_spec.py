"""Lineage development spec.

The spec is one category (``lineage_dev_spec``) of the unified standards
registry.  Only the full Markdown text is stored; it is passed verbatim to the
agent.  Every text change creates a new immutable version automatically.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import NotFoundError
from dgov.models.catalog import DataStandard
from dgov.services import standard as standard_service

SPEC_CATEGORY = "lineage_dev_spec"

DEFAULT_RULE_TEXT = """# 血缘关系开发规范

## 一、任务与表命名

任务名与表名必须一致，禁止使用无业务含义的随机后缀名。表名遵循分层前缀：
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
    return {
        "id": standard.id,
        "code": standard.code,
        "version": str((standard.extra or {}).get("version") or "v1"),
        "name": standard.name,
        "status": standard.status,
        "category": standard.category or SPEC_CATEGORY,
        "full_text": standard.rule or "",
        "updated_at": standard.updated_at,
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
        row = await ensure_default_spec(session)
        # The lazy default must be persisted, otherwise a read request would
        # roll it back and the next publish would reuse v1.
        await session.commit()
    return spec_to_dict(row)


async def ensure_default_spec(session: AsyncSession) -> DataStandard:
    existing = (
        await session.execute(
            select(DataStandard)
            .where(DataStandard.category == SPEC_CATEGORY, DataStandard.status == "active")
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await standard_service.publish_version(
        session,
        category=SPEC_CATEGORY,
        name="血缘关系开发规范",
        full_text=DEFAULT_RULE_TEXT,
        description="任务名与表名一致；血缘为任务间依赖关系；分层方向与增量采集约束",
        actor="system",
    )


async def publish_new_version(
    session: AsyncSession,
    *,
    name: str,
    full_text: str,
    actor: str | None,
) -> dict[str, Any]:
    """Publish the edited full text as a new version of the lineage spec."""

    row = await standard_service.publish_version(
        session,
        category=SPEC_CATEGORY,
        name=name or "血缘关系开发规范",
        full_text=full_text or DEFAULT_RULE_TEXT,
        actor=actor,
    )
    return spec_to_dict(row)


async def get_spec(session: AsyncSession, spec_id: int) -> DataStandard:
    row = await session.get(DataStandard, spec_id)
    if row is None or row.category != SPEC_CATEGORY:
        raise NotFoundError("血缘开发规范不存在")
    return row
