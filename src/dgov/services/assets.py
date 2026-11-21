"""Read models for the asset inventory and metadata/lineage views."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import NotFoundError
from dgov.models.metadata import (
    ColumnLineage,
    MetadataColumn,
    MetadataLineage,
    MetadataTable,
    QualityCheckResult,
    QualityCheckRule,
    WarehouseModel,
)


def _rate(part: int | float | None, total: int | float | None) -> float:
    if not total:
        return 0.0
    return round(float(part or 0) / float(total) * 100, 1)


def _size_text(value: float | None) -> str:
    if not value:
        return "-"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    number = float(value)
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


async def asset_summary(session: AsyncSession) -> dict[str, Any]:
    active = MetadataTable.status == "active"
    total_tables = (
        await session.execute(select(func.count()).select_from(MetadataTable).where(active))
    ).scalar_one()
    deprecated_tables = (
        await session.execute(
            select(func.count())
            .select_from(MetadataTable)
            .where(MetadataTable.status == "deprecated")
        )
    ).scalar_one()
    total_columns = (
        await session.execute(
            select(func.count())
            .select_from(MetadataColumn)
            .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
            .where(active, MetadataColumn.status == "active")
        )
    ).scalar_one()
    commented = (
        await session.execute(
            select(func.count())
            .select_from(MetadataTable)
            .where(active, func.length(func.trim(func.coalesce(MetadataTable.comment, ""))) > 0)
        )
    ).scalar_one()
    partitioned = (
        await session.execute(
            select(func.count())
            .select_from(MetadataTable)
            .where(active, MetadataTable.partition_newest.is_not(None))
        )
    ).scalar_one()
    lineage_edges = (
        await session.execute(
            select(func.count())
            .select_from(MetadataLineage)
            .where(MetadataLineage.status == "active")
        )
    ).scalar_one()
    field_edges = (
        await session.execute(select(func.count()).select_from(ColumnLineage))
    ).scalar_one()
    avg_quality = (
        await session.execute(select(func.avg(MetadataTable.quality_score)).where(active))
    ).scalar_one()
    total_rows = (
        await session.execute(select(func.sum(MetadataTable.record_count)).where(active))
    ).scalar_one()
    total_size = (
        await session.execute(select(func.sum(MetadataTable.size_bytes)).where(active))
    ).scalar_one()

    layer_rows = (
        await session.execute(
            select(MetadataTable.layer, func.count())
            .where(active)
            .group_by(MetadataTable.layer)
            .order_by(func.count().desc())
        )
    ).all()
    domain_rows = (
        await session.execute(
            select(MetadataTable.domain, func.count())
            .where(active)
            .group_by(MetadataTable.domain)
            .order_by(func.count().desc())
        )
    ).all()
    db_rows = (
        await session.execute(
            select(MetadataTable.db_name, func.count())
            .where(active)
            .group_by(MetadataTable.db_name)
            .order_by(func.count().desc())
        )
    ).all()
    sensitive_rows = (
        await session.execute(
            select(MetadataColumn.sensitive_level, func.count())
            .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
            .where(active, MetadataColumn.status == "active")
            .group_by(MetadataColumn.sensitive_level)
        )
    ).all()
    category_rows = (
        await session.execute(
            select(MetadataColumn.data_category, func.count())
            .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
            .where(active, MetadataColumn.status == "active")
            .group_by(MetadataColumn.data_category)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    type_rows = (
        await session.execute(
            select(MetadataColumn.type_family, func.count())
            .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
            .where(active, MetadataColumn.status == "active")
            .group_by(MetadataColumn.type_family)
            .order_by(func.count().desc())
        )
    ).all()

    return {
        "tables": total_tables,
        "deprecated_tables": deprecated_tables,
        "columns": total_columns,
        "database_count": len([row for row in db_rows if row[0]]),
        "lineage_edges": lineage_edges,
        "field_lineage_edges": field_edges,
        "avg_quality": round(float(avg_quality or 0), 1),
        "comment_coverage": _rate(commented, total_tables),
        "partition_coverage": _rate(partitioned, total_tables),
        "record_count": int(total_rows or 0),
        "size_bytes": float(total_size or 0),
        "size_text": _size_text(total_size),
        "layers": [{"name": name, "count": count} for name, count in layer_rows],
        "domains": [{"name": name, "count": count} for name, count in domain_rows],
        "databases": [{"name": name, "count": count} for name, count in db_rows],
        "sensitive_levels": [{"name": name, "count": count} for name, count in sensitive_rows],
        "data_categories": [{"name": name, "count": count} for name, count in category_rows],
        "type_families": [{"name": name, "count": count} for name, count in type_rows],
    }


async def list_tables(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    layer: str | None = None,
    domain: str | None = None,
    db_name: str | None = None,
    tenant: str | None = None,
    status: str = "active",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    if status != "all":
        conditions.append(MetadataTable.status == status)
    if layer:
        conditions.append(MetadataTable.layer == layer)
    if domain:
        conditions.append(MetadataTable.domain == domain)
    if db_name:
        conditions.append(MetadataTable.db_name == db_name)
    if tenant:
        conditions.append(MetadataTable.tenant == tenant)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(
                MetadataTable.name.ilike(like),
                MetadataTable.comment.ilike(like),
                MetadataTable.external_id.ilike(like),
            )
        )

    stmt = select(MetadataTable)
    count_stmt = select(func.count()).select_from(MetadataTable)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    tables = list(
        (
            await session.execute(
                stmt.order_by(MetadataTable.layer, MetadataTable.name)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    if not tables:
        return [], total

    table_ids = [table.id for table in tables]
    external_ids = [table.external_id for table in tables]

    column_rows = (
        await session.execute(
            select(
                MetadataColumn.table_id,
                func.count(),
                func.sum(
                    case(
                        (
                            func.length(func.trim(func.coalesce(MetadataColumn.comment, ""))) == 0,
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(case((MetadataColumn.sensitive_level != "L1", 1), else_=0)),
            )
            .where(MetadataColumn.status == "active", MetadataColumn.table_id.in_(table_ids))
            .group_by(MetadataColumn.table_id)
        )
    ).all()
    column_stats = {
        table_id: {"count": count, "missing": missing or 0, "sensitive": sensitive or 0}
        for table_id, count, missing, sensitive in column_rows
    }

    upstream: dict[str, int] = defaultdict(int)
    downstream: dict[str, int] = defaultdict(int)
    for source, target in (
        await session.execute(
            select(MetadataLineage.source_table_id, MetadataLineage.target_table_id).where(
                MetadataLineage.status == "active",
                or_(
                    MetadataLineage.source_table_id.in_(external_ids),
                    MetadataLineage.target_table_id.in_(external_ids),
                ),
            )
        )
    ).all():
        downstream[source] += 1
        upstream[target] += 1

    items = []
    for table in tables:
        info = column_stats.get(table.id, {"count": 0, "missing": 0, "sensitive": 0})
        items.append(
            {
                "id": table.id,
                "external_id": table.external_id,
                "name": table.name,
                "comment": table.comment,
                "db_name": table.db_name,
                "tenant": table.tenant,
                "layer": table.layer,
                "domain": table.domain,
                "grain": table.grain,
                "table_type": table.table_type,
                "store_type": table.store_type,
                "status": table.status,
                "record_count": table.record_count,
                "size_text": _size_text(table.size_bytes),
                "partition_newest": table.partition_newest,
                "last_dml_time": table.last_dml_time,
                "quality_score": table.quality_score,
                "column_count": info["count"],
                "sensitive_columns": info["sensitive"],
                "comment_coverage": _rate(info["count"] - info["missing"], info["count"]),
                "upstream": upstream.get(table.external_id, 0),
                "downstream": downstream.get(table.external_id, 0),
            }
        )
    return items, total


async def get_table(session: AsyncSession, table_id: int) -> MetadataTable:
    table = await session.get(MetadataTable, table_id)
    if table is None:
        raise NotFoundError("数据表不存在")
    return table


async def table_detail(session: AsyncSession, table_id: int) -> dict[str, Any]:
    table = await get_table(session, table_id)
    columns = list(
        (
            await session.execute(
                select(MetadataColumn)
                .where(MetadataColumn.table_id == table.id)
                .order_by(MetadataColumn.ordinal, MetadataColumn.id)
            )
        ).scalars()
    )
    model = (
        await session.execute(select(WarehouseModel).where(WarehouseModel.table_id == table.id))
    ).scalar_one_or_none()

    quality_items: list[dict[str, Any]] = []
    latest_run = (
        await session.execute(
            select(QualityCheckResult.run_id)
            .order_by(QualityCheckResult.executed_at.desc(), QualityCheckResult.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_run:
        rows = (
            await session.execute(
                select(QualityCheckResult, QualityCheckRule)
                .join(QualityCheckRule, QualityCheckRule.id == QualityCheckResult.rule_id)
                .where(
                    QualityCheckResult.run_id == latest_run,
                    QualityCheckResult.table_id == table.id,
                )
                .order_by(QualityCheckResult.status.desc())
            )
        ).all()
        quality_items = [
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
        ]

    upstream_edges = list(
        (
            await session.execute(
                select(MetadataLineage)
                .where(
                    MetadataLineage.target_table_id == table.external_id,
                    MetadataLineage.status == "active",
                )
                .order_by(MetadataLineage.id)
            )
        ).scalars()
    )
    downstream_edges = list(
        (
            await session.execute(
                select(MetadataLineage)
                .where(
                    MetadataLineage.source_table_id == table.external_id,
                    MetadataLineage.status == "active",
                )
                .order_by(MetadataLineage.id)
            )
        ).scalars()
    )
    field_in, field_out = await _table_field_lineage(session, table.external_id)

    return {
        "table": {
            "id": table.id,
            "external_id": table.external_id,
            "name": table.name,
            "comment": table.comment,
            "db_name": table.db_name,
            "tenant": table.tenant,
            "layer": table.layer,
            "domain": table.domain,
            "grain": table.grain,
            "table_type": table.table_type,
            "store_type": table.store_type,
            "status": table.status,
            "life_days": table.life_days,
            "record_count": table.record_count,
            "size_text": _size_text(table.size_bytes),
            "file_count": table.file_count,
            "partition_newest": table.partition_newest,
            "last_ddl_time": table.last_ddl_time,
            "last_dml_time": table.last_dml_time,
            "first_seen_at": table.first_seen_at,
            "last_seen_at": table.last_seen_at,
            "quality_score": table.quality_score,
        },
        "columns": [
            {
                "id": column.id,
                "name": column.name,
                "comment": column.comment,
                "data_type": column.data_type,
                "type_family": column.type_family,
                "ordinal": column.ordinal,
                "sensitive_level": column.sensitive_level,
                "data_category": column.data_category,
                "status": column.status,
            }
            for column in columns
        ],
        "quality": {"run_id": latest_run, "items": quality_items},
        "lineage": {
            "upstream": [
                {
                    "source_table_id": edge.source_table_id,
                    "source_table_name": edge.source_table_name,
                    "source_schema": edge.source_schema,
                    "lineage_type": edge.lineage_type,
                }
                for edge in upstream_edges
            ],
            "downstream": [
                {
                    "target_table_id": edge.target_table_id,
                    "target_table_name": edge.target_table_name,
                    "target_schema": edge.target_schema,
                    "lineage_type": edge.lineage_type,
                }
                for edge in downstream_edges
            ],
            "fields_in": field_in,
            "fields_out": field_out,
        },
        "model": (
            {
                "model_name": model.model_name,
                "naming_compliant": model.naming_compliant,
                "type_compliant": model.type_compliant,
                "score": model.score,
                "column_count": model.column_count,
            }
            if model
            else None
        ),
    }


async def list_columns(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    table_id: int | None = None,
    sensitive_level: str | None = None,
    data_category: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int]:
    conditions = [MetadataColumn.status == "active", MetadataTable.status == "active"]
    if table_id:
        conditions.append(MetadataColumn.table_id == table_id)
    if sensitive_level:
        conditions.append(MetadataColumn.sensitive_level == sensitive_level)
    if data_category:
        conditions.append(MetadataColumn.data_category == data_category)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(
                MetadataColumn.name.ilike(like),
                MetadataColumn.comment.ilike(like),
                MetadataTable.name.ilike(like),
            )
        )
    base = (
        select(MetadataColumn, MetadataTable)
        .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
        .where(*conditions)
    )
    count_stmt = (
        select(func.count())
        .select_from(MetadataColumn)
        .join(MetadataTable, MetadataTable.id == MetadataColumn.table_id)
        .where(*conditions)
    )
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            base.order_by(MetadataTable.name, MetadataColumn.ordinal)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return [
        {
            "id": column.id,
            "table_id": table.id,
            "table_name": table.name,
            "table_external_id": table.external_id,
            "layer": table.layer,
            "domain": table.domain,
            "name": column.name,
            "comment": column.comment,
            "data_type": column.data_type,
            "type_family": column.type_family,
            "sensitive_level": column.sensitive_level,
            "data_category": column.data_category,
            "status": column.status,
        }
        for column, table in rows
    ], total


async def _table_field_lineage(
    session: AsyncSession, external_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outgoing = list(
        (
            await session.execute(
                select(ColumnLineage)
                .where(ColumnLineage.source_table_id == external_id)
                .order_by(ColumnLineage.target_table_id, ColumnLineage.source_column)
            )
        ).scalars()
    )
    incoming = list(
        (
            await session.execute(
                select(ColumnLineage)
                .where(ColumnLineage.target_table_id == external_id)
                .order_by(ColumnLineage.source_table_id, ColumnLineage.target_column)
            )
        ).scalars()
    )
    names = await _external_names(
        session,
        {edge.target_table_id for edge in outgoing} | {edge.source_table_id for edge in incoming},
    )
    return (
        [
            {
                "source_column": edge.source_column,
                "source_table_id": edge.source_table_id,
                "source_table_name": names.get(edge.source_table_id),
                "target_column": edge.target_column,
                "target_table_id": edge.target_table_id,
                "target_table_name": names.get(edge.target_table_id),
                "method": edge.method,
                "confidence": edge.confidence,
            }
            for edge in incoming
        ],
        [
            {
                "source_column": edge.source_column,
                "source_table_id": edge.source_table_id,
                "source_table_name": names.get(edge.source_table_id),
                "target_column": edge.target_column,
                "target_table_id": edge.target_table_id,
                "target_table_name": names.get(edge.target_table_id),
                "method": edge.method,
                "confidence": edge.confidence,
            }
            for edge in outgoing
        ],
    )


async def _external_names(
    session: AsyncSession, external_ids: set[str]
) -> dict[str, str]:
    if not external_ids:
        return {}
    rows = (
        await session.execute(
            select(MetadataTable.external_id, MetadataTable.name).where(
                MetadataTable.external_id.in_(external_ids)
            )
        )
    ).all()
    return dict(rows)


async def list_table_edges(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    datasource_id: int | None = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int]:
    conditions = [MetadataLineage.status == "active"]
    if datasource_id:
        conditions.append(MetadataLineage.datasource_id == datasource_id)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(
                MetadataLineage.source_table_name.ilike(like),
                MetadataLineage.target_table_name.ilike(like),
            )
        )
    total = (
        await session.execute(
            select(func.count()).select_from(MetadataLineage).where(*conditions)
        )
    ).scalar_one()
    edges = list(
        (
            await session.execute(
                select(MetadataLineage)
                .where(*conditions)
                .order_by(MetadataLineage.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    field_counts = dict(
        (
            await session.execute(
                select(ColumnLineage.lineage_id, func.count()).group_by(ColumnLineage.lineage_id)
            )
        ).all()
    )
    return [
        {
            "id": edge.id,
            "source_table_id": edge.source_table_id,
            "source_table_name": edge.source_table_name,
            "source_schema": edge.source_schema,
            "target_table_id": edge.target_table_id,
            "target_table_name": edge.target_table_name,
            "target_schema": edge.target_schema,
            "lineage_type": edge.lineage_type,
            "column_edges": field_counts.get(edge.id, 0),
        }
        for edge in edges
    ], total


async def lineage_graph(
    session: AsyncSession,
    *,
    table_id: int,
    depth: int = 2,
    direction: str = "both",
) -> dict[str, Any]:
    root = await get_table(session, table_id)
    edges = list(
        (
            await session.execute(
                select(MetadataLineage).where(
                    MetadataLineage.datasource_id == root.datasource_id,
                    MetadataLineage.status == "active",
                )
            )
        ).scalars()
    )
    nodes_by_external = {
        table.external_id: table
        for table in (
            await session.execute(
                select(MetadataTable).where(
                    MetadataTable.datasource_id == root.datasource_id,
                    MetadataTable.status == "active",
                )
            )
        ).scalars()
    }

    levels: dict[str, int] = {root.external_id: 0}
    visited_edges: set[int] = set()
    frontier = {root.external_id}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in edges:
        outgoing[edge.source_table_id].append(edge)
        incoming[edge.target_table_id].append(edge)

    for level in range(1, max(depth, 0) + 1):
        next_frontier: set[str] = set()
        for external in sorted(frontier):
            if direction in {"both", "upstream"}:
                for edge in incoming.get(external, []):
                    if edge.id not in visited_edges:
                        visited_edges.add(edge.id)
                        if edge.source_table_id not in levels:
                            levels[edge.source_table_id] = level * (
                                -1 if direction == "upstream" else 1
                            )
                            next_frontier.add(edge.source_table_id)
            if direction in {"both", "downstream"}:
                for edge in outgoing.get(external, []):
                    if edge.id not in visited_edges:
                        visited_edges.add(edge.id)
                        if edge.target_table_id not in levels:
                            levels[edge.target_table_id] = level
                            next_frontier.add(edge.target_table_id)
        frontier = next_frontier
        if not frontier:
            break

    # ``both`` keeps a simple concentric layout by distance from the root.
    if direction == "both":
        for external in levels:
            if external == root.external_id:
                levels[external] = 0
            else:
                levels[external] = abs(levels[external])
    elif direction == "upstream":
        levels = {external: -level for external, level in levels.items()}

    included = set(levels)
    node_items = []
    for external, level in sorted(levels.items(), key=lambda item: (item[1], item[0])):
        table = nodes_by_external.get(external)
        node_items.append(
            {
                "external_id": external,
                "table_id": table.id if table else None,
                "name": table.name if table else external,
                "layer": table.layer if table else "OTHER",
                "domain": table.domain if table else "通用",
                "quality_score": table.quality_score if table else None,
                "level": level,
                "is_root": external == root.external_id,
            }
        )
    edge_items = [
        {
            "id": edge.id,
            "source": edge.source_table_id,
            "source_name": edge.source_table_name,
            "target": edge.target_table_id,
            "target_name": edge.target_table_name,
            "lineage_type": edge.lineage_type,
        }
        for edge in edges
        if edge.id in visited_edges
        and edge.source_table_id in included
        and edge.target_table_id in included
    ]
    return {
        "root": root.external_id,
        "root_table_id": root.id,
        "direction": direction,
        "depth": depth,
        "nodes": node_items,
        "edges": edge_items,
        "stats": {
            "nodes": len(node_items),
            "edges": len(edge_items),
            "upstream": sum(1 for level in levels.values() if level < 0),
            "downstream": sum(1 for level in levels.values() if level > 0),
        },
    }


async def impact_analysis(session: AsyncSession, table_id: int, depth: int = 4) -> dict[str, Any]:
    graph = await lineage_graph(session, table_id=table_id, depth=depth, direction="downstream")
    impacted = [node for node in graph["nodes"] if not node["is_root"]]
    direct = [node for node in impacted if node["level"] == 1]
    return {
        "root": graph["nodes"][0] if graph["nodes"] else None,
        "direct": direct,
        "indirect": [node for node in impacted if node["level"] > 1],
        "edges": graph["edges"],
        "stats": {
            "impacted_tables": len(impacted),
            "max_depth": max((node["level"] for node in impacted), default=0),
            "layers": sorted({node["layer"] for node in impacted}),
        },
    }


async def list_field_lineage(
    session: AsyncSession,
    *,
    table_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    if table_id:
        table = await get_table(session, table_id)
        conditions.append(
            or_(
                ColumnLineage.source_table_id == table.external_id,
                ColumnLineage.target_table_id == table.external_id,
            )
        )
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(ColumnLineage.source_column.ilike(like), ColumnLineage.target_column.ilike(like))
        )
    count_stmt = select(func.count()).select_from(ColumnLineage)
    stmt = select(ColumnLineage)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    edges = list(
        (
            await session.execute(
                stmt.order_by(ColumnLineage.source_table_id, ColumnLineage.source_column)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    names = await _external_names(
        session,
        {edge.source_table_id for edge in edges} | {edge.target_table_id for edge in edges},
    )
    return [
        {
            "id": edge.id,
            "source_table_id": edge.source_table_id,
            "source_table_name": names.get(edge.source_table_id),
            "source_column": edge.source_column,
            "target_table_id": edge.target_table_id,
            "target_table_name": names.get(edge.target_table_id),
            "target_column": edge.target_column,
            "method": edge.method,
            "confidence": edge.confidence,
        }
        for edge in edges
    ], total
