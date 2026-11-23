from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.config import settings
from dgov.core.exceptions import BadRequestError
from dgov.models.base import utcnow
from dgov.models.datasource import DataSource
from dgov.models.metadata import (
    ColumnLineage,
    IngestRun,
    MetadataColumn,
    MetadataLineage,
    MetadataTable,
)
from dgov.services import modeling
from dgov.services.logs import write_log
from dgov.services.metadata_client import ASSET_LABELS, SOURCE_ASSETS, MetadataApiClient
from dgov.services.metadata_rules import (
    classify_column,
    domain_for,
    grain_for,
    layer_for,
    parse_datetime,
    stable_hash,
    type_family,
)
from dgov.services.quality_meta import run_quality_checks

logger = logging.getLogger(__name__)

TABLE_FIELDS = (
    "name",
    "comment",
    "table_type",
    "store_type",
    "tenant",
    "layer",
    "domain",
    "grain",
    "life_days",
    "record_count",
    "size_bytes",
    "file_count",
    "partition_newest",
    "last_ddl_time",
    "last_dml_time",
)


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def normalize_table(row: dict[str, Any]) -> dict[str, Any] | None:
    external_id = _text(row, "table_id")
    name = _text(row, "table_name")
    if not external_id or not name:
        return None
    db_name = _text(row, "db_name")
    comment = _text(row, "table_comment", "table_desc_text", "db_comment")
    return {
        "external_id": external_id,
        "name": name,
        "comment": comment,
        "db_name": db_name,
        "table_type": _text(row, "table_type"),
        "store_type": _text(row, "table_store_type", "store_type"),
        "tenant": _text(row, "tenant_name"),
        "layer": layer_for(name),
        "domain": domain_for(name, comment, db_name, _text(row, "tenant_name")),
        "grain": grain_for(name),
        "life_days": _integer(row.get("table_life_day")),
        "record_count": _integer(row.get("table_rec_count")),
        "size_bytes": _number(row.get("table_size")),
        "file_count": _integer(row.get("table_file_count")),
        "partition_newest": _text(row, "partition_newest"),
        "last_ddl_time": parse_datetime(row.get("table_last_ddl_time")),
        "last_dml_time": parse_datetime(row.get("table_last_dml_time")),
    }


def normalize_column(row: dict[str, Any]) -> dict[str, Any] | None:
    table_external = _text(row, "table_id")
    name = _text(row, "column_name")
    if not table_external or not name:
        return None
    comment = _text(row, "column_comment")
    data_type = _text(row, "column_type")
    level, category = classify_column(name, comment)
    return {
        "table_external_id": table_external,
        "name": name,
        "comment": comment,
        "data_type": data_type,
        "type_family": type_family(data_type),
        "sensitive_level": level,
        "data_category": category,
    }


def normalize_lineage(row: dict[str, Any]) -> dict[str, Any] | None:
    source = _text(row, "source_table_id")
    target = _text(row, "target_table_id")
    if not source or not target:
        return None
    return {
        "source_table_id": source,
        "target_table_id": target,
        "source_table_name": _text(row, "source_table"),
        "target_table_name": _text(row, "target_table"),
        "source_schema": _text(row, "source_schema"),
        "target_schema": _text(row, "target_schema"),
        "lineage_type": _integer(row.get("lingeage_type")) or 0,
        "op": str(row.get("op") or "upsert").lower(),
    }


def lineage_raw_edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the API's original (possibly duplicated) rows for insight checks.

    ``_upsert_lineage`` de-duplicates before writing ``meta_lineage``, so the
    graph itself cannot reveal duplicate-edge problems.  The raw feed is kept
    (bounded) in ``DataSource.config`` and consumed by the rule engine.
    """
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = normalize_lineage(row)
        if payload is None:
            continue
        result.append(
            {
                "source_table_id": payload["source_table_id"],
                "target_table_id": payload["target_table_id"],
                "source_table": payload["source_table_name"] or "",
                "target_table": payload["target_table_name"] or "",
                "source_schema": payload["source_schema"] or "",
                "target_schema": payload["target_schema"] or "",
                "type": str(payload["lineage_type"]),
                "op": payload["op"],
            }
        )
    return result


def _table_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {field: payload.get(field) for field in TABLE_FIELDS}


def _table_hash(payload: dict[str, Any]) -> str:
    return stable_hash(_table_values(payload))


def _column_hash(payload: dict[str, Any]) -> str:
    return stable_hash(
        {
            key: payload.get(key)
            for key in ("name", "comment", "data_type", "type_family", "sensitive_level", "data_category")
        }
    )


async def _upsert_tables(
    session: AsyncSession,
    datasource: DataSource,
    rows: list[dict[str, Any]],
    *,
    initial: bool,
    actor: str | None,
) -> tuple[dict[str, MetadataTable], dict[str, int], set[str]]:
    existing = {
        table.external_id: table
        for table in (
            await session.execute(
                select(MetadataTable).where(MetadataTable.datasource_id == datasource.id)
            )
        ).scalars()
    }
    stats = {"new": 0, "changed": 0, "removed": 0, "skipped": 0}
    seen: set[str] = set()
    now = utcnow()
    changed_tables: list[dict[str, Any]] = []

    for row in rows:
        payload = normalize_table(row)
        if payload is None:
            stats["skipped"] += 1
            continue
        external_id = payload["external_id"]
        if external_id in seen:
            stats["skipped"] += 1
            continue
        seen.add(external_id)
        digest = _table_hash(payload)
        table = existing.get(external_id)
        if table is None:
            table = MetadataTable(
                datasource_id=datasource.id,
                external_id=external_id,
                table_hash=digest,
                first_seen_at=now,
                last_seen_at=now,
                status="active",
                **{key: value for key, value in payload.items() if key != "external_id"},
            )
            session.add(table)
            existing[external_id] = table
            stats["new"] += 1
            continue

        previous = _table_values(
            {
                "name": table.name,
                "comment": table.comment,
                "table_type": table.table_type,
                "store_type": table.store_type,
                "tenant": table.tenant,
                "layer": table.layer,
                "domain": table.domain,
                "grain": table.grain,
                "life_days": table.life_days,
                "record_count": table.record_count,
                "size_bytes": table.size_bytes,
                "file_count": table.file_count,
                "partition_newest": table.partition_newest,
                "last_ddl_time": table.last_ddl_time,
                "last_dml_time": table.last_dml_time,
            }
        )
        if table.table_hash and table.table_hash != digest:
            diffs = {
                field: {"old": previous.get(field), "new": payload.get(field)}
                for field in TABLE_FIELDS
                if previous.get(field) != payload.get(field)
            }
            stats["changed"] += 1
            if not initial:
                changed_tables.append({"table": table.name, "changes": diffs})
        for field, value in payload.items():
            if field != "external_id":
                setattr(table, field, value)
        table.table_hash = digest
        table.last_seen_at = now
        table.status = "active"

    for external_id, table in existing.items():
        if table.status == "active" and external_id not in seen:
            table.status = "deprecated"
            stats["removed"] += 1

    await session.flush()
    if changed_tables:
        for item in changed_tables[:50]:
            await write_log(
                session,
                category="metadata_change",
                action="table.updated",
                message=f"表 {item['table']} 元数据发生变更",
                actor=actor,
                resource_type="meta_table",
                resource_id=item["table"],
                detail=item,
                commit=False,
            )
    return existing, stats, seen


async def _upsert_columns(
    session: AsyncSession,
    tables: dict[str, MetadataTable],
    rows: list[dict[str, Any]],
    *,
    initial: bool,
    actor: str | None,
) -> dict[str, int]:
    existing = {
        (column.table_id, column.name): column
        for column in (
            await session.execute(select(MetadataColumn).join(MetadataTable))
        ).scalars()
        if column.table_id in {table.id for table in tables.values()}
    }
    stats = {"new": 0, "changed": 0, "removed": 0, "skipped": 0}
    now = utcnow()
    seen_per_table: dict[int, set[str]] = {}
    ordinals: dict[int, int] = {}
    changed_by_table: dict[int, list[str]] = {}

    for row in rows:
        payload = normalize_column(row)
        if payload is None:
            stats["skipped"] += 1
            continue
        table = tables.get(payload["table_external_id"])
        if table is None:
            stats["skipped"] += 1
            continue
        key = (table.id, payload["name"])
        if key in existing and existing[key].name in seen_per_table.get(table.id, set()):
            stats["skipped"] += 1
            continue
        seen_per_table.setdefault(table.id, set()).add(payload["name"])
        ordinals[table.id] = ordinals.get(table.id, 0) + 1
        digest = _column_hash(payload)
        column = existing.get(key)
        if column is None:
            column = MetadataColumn(
                table_id=table.id,
                ordinal=ordinals[table.id],
                column_hash=digest,
                first_seen_at=now,
                last_seen_at=now,
                status="active",
                **{k: v for k, v in payload.items() if k != "table_external_id"},
            )
            session.add(column)
            existing[key] = column
            stats["new"] += 1
            continue

        if column.column_hash and column.column_hash != digest:
            stats["changed"] += 1
            if not initial:
                changed_by_table.setdefault(table.id, []).append(column.name)
        column.comment = payload["comment"]
        column.data_type = payload["data_type"]
        column.type_family = payload["type_family"]
        column.sensitive_level = payload["sensitive_level"]
        column.data_category = payload["data_category"]
        column.column_hash = digest
        column.last_seen_at = now
        column.status = "active"

    for (table_id, name), column in existing.items():
        if column.status == "active" and name not in seen_per_table.get(table_id, set()):
            column.status = "deprecated"
            stats["removed"] += 1

    await session.flush()
    name_by_id = {table.id: table.name for table in tables.values()}
    for table_id, names in list(changed_by_table.items())[:30]:
        await write_log(
            session,
            category="metadata_change",
            action="columns.updated",
            message=f"表 {name_by_id.get(table_id)} 的 {len(names)} 个字段发生变更",
            actor=actor,
            resource_type="meta_table",
            resource_id=name_by_id.get(table_id),
            detail={"columns": names[:20]},
            commit=False,
        )
    return stats


async def _upsert_lineage(
    session: AsyncSession,
    datasource: DataSource,
    rows: list[dict[str, Any]],
    *,
    initial: bool,
    actor: str | None,
    incremental: bool = False,
) -> dict[str, int]:
    existing = {
        (edge.source_table_id, edge.target_table_id, edge.lineage_type): edge
        for edge in (
            await session.execute(
                select(MetadataLineage).where(MetadataLineage.datasource_id == datasource.id)
            )
        ).scalars()
    }
    stats = {"new": 0, "removed": 0, "skipped": 0}
    seen: set[tuple[str, str, int]] = set()
    now = utcnow()
    for row in rows:
        payload = normalize_lineage(row)
        if payload is None:
            stats["skipped"] += 1
            continue
        op = payload.pop("op", "upsert")
        key = (
            payload["source_table_id"],
            payload["target_table_id"],
            payload["lineage_type"],
        )
        if key in seen:
            stats["skipped"] += 1
            continue
        seen.add(key)
        edge = existing.get(key)
        if op == "delete":
            if edge is not None and edge.status == "active":
                edge.status = "deprecated"
                edge.last_seen_at = now
                stats["removed"] += 1
                if not initial:
                    await write_log(
                        session,
                        category="lineage_change",
                        action="lineage.removed",
                        message=f"增量删除血缘 {key[0]} → {key[1]}",
                        actor=actor,
                        level="warning",
                        resource_type="meta_lineage",
                        resource_id=f"{key[0]}->{key[1]}",
                        commit=False,
                    )
            continue
        if edge is None:
            edge = MetadataLineage(
                datasource_id=datasource.id,
                first_seen_at=now,
                last_seen_at=now,
                status="active",
                **payload,
            )
            session.add(edge)
            existing[key] = edge
            stats["new"] += 1
            if not initial:
                await write_log(
                    session,
                    category="lineage_change",
                    action="lineage.added",
                    message=(
                        f"新增血缘 {payload['source_table_name'] or key[0]} → "
                        f"{payload['target_table_name'] or key[1]}"
                    ),
                    actor=actor,
                    resource_type="meta_lineage",
                    resource_id=f"{key[0]}->{key[1]}",
                    detail=payload,
                    commit=False,
                )
            continue
        edge.source_table_name = payload["source_table_name"]
        edge.target_table_name = payload["target_table_name"]
        edge.source_schema = payload["source_schema"]
        edge.target_schema = payload["target_schema"]
        edge.last_seen_at = now
        edge.status = "active"

    # Only a FULL feed may deprecate edges that did not appear.  In incremental
    # mode the API explicitly marks deletions with op=delete; treating an absent
    # edge as deleted would wipe every unchanged edge in the graph.
    if not incremental:
        for key, edge in existing.items():
            if edge.status == "active" and key not in seen:
                edge.status = "deprecated"
                stats["removed"] += 1
                if not initial:
                    await write_log(
                        session,
                        category="lineage_change",
                        action="lineage.removed",
                        message=(
                            f"血缘失效 {edge.source_table_name or key[0]} → "
                            f"{edge.target_table_name or key[1]}"
                        ),
                        actor=actor,
                        level="warning",
                        resource_type="meta_lineage",
                        resource_id=f"{key[0]}->{key[1]}",
                        commit=False,
                    )
    await session.flush()
    return stats


async def rebuild_column_lineage(session: AsyncSession, datasource_id: int) -> int:
    lineage_ids = select(MetadataLineage.id).where(
        MetadataLineage.datasource_id == datasource_id
    )
    await session.execute(delete(ColumnLineage).where(ColumnLineage.lineage_id.in_(lineage_ids)))

    edges = list(
        (
            await session.execute(
                select(MetadataLineage).where(
                    MetadataLineage.datasource_id == datasource_id,
                    MetadataLineage.status == "active",
                )
            )
        ).scalars()
    )
    if not edges:
        await session.flush()
        return 0

    columns = (
        await session.execute(
            select(MetadataTable.external_id, MetadataColumn.name)
            .join(MetadataColumn, MetadataColumn.table_id == MetadataTable.id)
            .where(
                MetadataTable.datasource_id == datasource_id,
                MetadataColumn.status == "active",
                MetadataTable.status == "active",
            )
        )
    ).all()
    columns_by_table: dict[str, set[str]] = {}
    for external_id, name in columns:
        columns_by_table.setdefault(external_id, set()).add(name)

    created = 0
    for edge in edges:
        source_columns = columns_by_table.get(edge.source_table_id, set())
        target_columns = columns_by_table.get(edge.target_table_id, set())
        for name in sorted(source_columns & target_columns):
            session.add(
                ColumnLineage(
                    lineage_id=edge.id,
                    source_table_id=edge.source_table_id,
                    target_table_id=edge.target_table_id,
                    source_column=name,
                    target_column=name,
                    method="same_name",
                    confidence=0.8,
                )
            )
            created += 1
    await session.flush()
    return created


async def ingest_payload(
    session: AsyncSession,
    datasource: DataSource,
    payload: dict[str, list[dict[str, Any]]],
    *,
    actor: str | None = None,
    incremental: bool = False,
) -> dict[str, Any]:
    existing_tables = (
        await session.execute(
            select(func.count())
            .select_from(MetadataTable)
            .where(MetadataTable.datasource_id == datasource.id)
        )
    ).scalar_one()
    existing_columns = (
        await session.execute(
            select(func.count())
            .select_from(MetadataColumn)
            .join(MetadataTable)
            .where(MetadataTable.datasource_id == datasource.id)
        )
    ).scalar_one()
    initial = existing_tables == 0 and existing_columns == 0

    tables = {
        table.external_id: table
        for table in (
            await session.execute(
                select(MetadataTable).where(MetadataTable.datasource_id == datasource.id)
            )
        ).scalars()
    }
    stats: dict[str, Any] = {
        "initial": initial,
        "tables": {"new": 0, "changed": 0, "removed": 0, "skipped": 0},
        "columns": {"new": 0, "changed": 0, "removed": 0, "skipped": 0},
        "lineage": {"new": 0, "removed": 0, "skipped": 0},
        "column_lineage_new": 0,
        "rows_read": sum(len(items) for items in payload.values()),
    }
    if "table_info" in payload:
        tables, table_stats, _ = await _upsert_tables(
            session, datasource, payload["table_info"], initial=initial, actor=actor
        )
        stats["tables"] = table_stats
    if "column_metadata" in payload:
        stats["columns"] = await _upsert_columns(
            session, tables, payload["column_metadata"], initial=initial, actor=actor
        )
    if "table_lineage" in payload:
        stats["lineage"] = await _upsert_lineage(
            session,
            datasource,
            payload["table_lineage"],
            initial=initial,
            actor=actor,
            incremental=incremental,
        )
        raw_edges = lineage_raw_edges(payload["table_lineage"])
        unique_edges = {
            (edge["source_table_id"], edge["target_table_id"], edge["type"])
            for edge in raw_edges
        }
        stats["lineage_raw_rows"] = len(raw_edges)
        stats["lineage_duplicate_rows"] = len(raw_edges) - len(unique_edges)
        timestamps = [
            str(row.get("updatetime") or row.get("createtime") or "")
            for row in payload["table_lineage"]
        ]
        stats["lineage_cursor"] = max((value for value in timestamps if value), default=None)
    stats["column_lineage_new"] = await rebuild_column_lineage(session, datasource.id)
    await modeling.rebuild_warehouse_models(session)
    await modeling.rebuild_ontology(session)
    stats["rows_written"] = (
        stats["tables"]["new"]
        + stats["tables"]["changed"]
        + stats["columns"]["new"]
        + stats["columns"]["changed"]
        + stats["lineage"]["new"]
        + stats["column_lineage_new"]
    )
    await session.commit()
    return stats


async def run_collection(
    session: AsyncSession,
    datasource: DataSource,
    assets: list[str],
    *,
    trigger_type: str = "manual",
    job_id: int | None = None,
    actor: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    run_quality: bool = True,
    since: str | None = None,
    incremental: bool | None = None,
    run_insight: bool = True,
) -> tuple[IngestRun, dict[str, Any]]:
    invalid = sorted(set(assets) - set(SOURCE_ASSETS))
    if invalid:
        raise BadRequestError(f"不支持的采集资产: {', '.join(invalid)}")
    selected = [asset for asset in SOURCE_ASSETS if asset in assets] or list(SOURCE_ASSETS)
    if datasource.type != "api":
        raise BadRequestError("当前仅支持 API 类型数据源的元数据采集")

    source_id = datasource.id
    source_name = datasource.name
    run = IngestRun(
        job_id=job_id,
        datasource_id=source_id,
        trigger_type=trigger_type,
        status="running",
        assets=selected,
        started_at=utcnow(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    run_id = run.id

    try:
        client = MetadataApiClient(datasource, transport=transport)
        # A scheduled lineage-only job reuses the persisted cursor, which is
        # how "periodically fetch incremental lineage from the API" is realised.
        if (
            since is None
            and incremental is None
            and trigger_type == "schedule"
            and selected == ["table_lineage"]
        ):
            since = dict(datasource.config or {}).get("lineage_cursor") or None
        is_incremental = bool(since) if incremental is None else incremental
        params_by_asset: dict[str, dict[str, Any]] = {}
        if is_incremental and since:
            params_by_asset["table_lineage"] = {"since": since}
        payload = await client.fetch_assets(selected, params_by_asset=params_by_asset)
        stats = await ingest_payload(
            session, datasource, payload, actor=actor, incremental=is_incremental
        )
        cursor_to = stats.get("lineage_cursor")
        raw_edges = lineage_raw_edges(payload.get("table_lineage") or [])
        changed_ids = {
            value
            for edge in raw_edges
            for value in (edge["source_table_id"], edge["target_table_id"])
        }
        # Persist the cursor, raw feed and changed ids on the datasource config
        # (no new table needed; the JSON column already exists).
        config = dict(datasource.config or {})
        if cursor_to:
            config["lineage_cursor"] = cursor_to
        # Keep the full raw feed for duplicate-edge checks.  An incremental
        # batch only carries changed edges, so merge it into the previous feed
        # instead of replacing it (otherwise duplicate detection silently
        # degrades to the size of the latest delta).
        if is_incremental:
            grouped: dict[tuple[str, str, str], list[dict]] = {}
            for edge in config.get("lineage_last_raw_edges") or []:
                key = (edge["source_table_id"], edge["target_table_id"], edge["type"])
                grouped.setdefault(key, []).append(edge)
            touched: set[tuple[str, str, str]] = set()
            for edge in raw_edges:
                key = (edge["source_table_id"], edge["target_table_id"], edge["type"])
                if key not in touched:
                    # A batch re-states all rows of an edge it touches, so
                    # replace the previous rows for that edge (this also
                    # handles deletes without losing other duplicate rows).
                    grouped[key] = []
                    touched.add(key)
                if edge.get("op") != "delete":
                    grouped[key].append(edge)
            raw_feed = [edge for rows in grouped.values() for edge in rows]
        else:
            raw_feed = raw_edges
        config["lineage_last_raw_edges"] = raw_feed[-settings.lineage_max_edges :]
        config["lineage_last_changed"] = sorted(changed_ids)
        datasource.config = config
        quality_summary = None
        if run_quality and "table_info" in selected:
            quality_summary = await run_quality_checks(session, actor=actor, trigger="collection")
        summary = {
            "run_id": run.id,
            "status": "success",
            "assets": selected,
            "stats": stats,
            "quality": quality_summary,
            "incremental": is_incremental,
            "cursor_from": since,
            "cursor_to": cursor_to,
            "changed_ids": sorted(changed_ids),
        }
        run.status = "success"
        run.finished_at = utcnow()
        run.rows_read = stats["rows_read"]
        run.rows_written = stats["rows_written"]
        run.tables_new = stats["tables"]["new"]
        run.tables_changed = stats["tables"]["changed"]
        run.tables_removed = stats["tables"]["removed"]
        run.columns_new = stats["columns"]["new"]
        run.columns_changed = stats["columns"]["changed"]
        run.lineage_new = stats["lineage"]["new"]
        run.lineage_removed = stats["lineage"]["removed"]
        run.column_lineage_new = stats["column_lineage_new"]
        await write_log(
            session,
            category="asset_inventory",
            action="collection.completed",
            message=(
                f"资产盘点完成：表 +{stats['tables']['new']}/~{stats['tables']['changed']}，"
                f"字段 +{stats['columns']['new']}/~{stats['columns']['changed']}，"
                f"血缘 +{stats['lineage']['new']}/-{stats['lineage']['removed']}，"
                f"字段血缘 +{stats['column_lineage_new']}"
            ),
            actor=actor,
            resource_type="data_source",
            resource_id=source_id,
            detail=summary,
            commit=False,
        )
        await write_log(
            session,
            category="datasource_schedule",
            action=f"collection.{trigger_type}",
            message=f"数据源「{source_name}」采集成功（{trigger_type}）",
            actor=actor,
            resource_type="ingest_run",
            resource_id=run.id,
            detail={"assets": selected, "duration_rows": stats["rows_read"]},
            commit=False,
        )
        await session.commit()
        await session.refresh(run)
        if run_insight:
            try:
                from dgov.services.lineage_insight import trigger_after_collection

                summary["insight_run_ids"] = await trigger_after_collection(
                    session,
                    datasource_id=datasource.id,
                    ingest_run_id=run.id,
                    actor=actor,
                    changed_ids=changed_ids,
                    cursor_to=cursor_to,
                )
            except Exception as exc:  # insight must never break collection
                logger.warning("post-collection insight trigger skipped: %s", exc)
                summary["insight_run_ids"] = []
        return run, summary
    except Exception as exc:  # surface failure through run history + operation log
        logger.exception("metadata collection failed for source %s", source_id)
        await session.rollback()
        run = await session.get(IngestRun, run_id)
        if run is None:
            run = IngestRun(
                id=run_id,
                job_id=job_id,
                datasource_id=source_id,
                trigger_type=trigger_type,
                status="failed",
                assets=selected,
                started_at=utcnow(),
            )
            session.add(run)
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = str(exc)[:2000]
        await write_log(
            session,
            category="datasource_schedule",
            action="collection.failed",
            message=f"数据源「{source_name}」采集失败：{str(exc)[:180]}",
            actor=actor,
            level="error",
            resource_type="ingest_run",
            resource_id=run_id,
            detail={"assets": selected},
            commit=False,
        )
        await session.commit()
        return run, {
            "run_id": run_id,
            "status": "failed",
            "error": str(exc),
            "assets": selected,
        }


def next_run_at(unit: str, value: int, base=None):
    base = base or utcnow()
    if unit == "minute":
        return base + timedelta(minutes=max(value, 1))
    if unit == "hour":
        return base + timedelta(hours=max(value, 1))
    if unit == "day":
        return base + timedelta(days=max(value, 1))
    raise BadRequestError("调度单位仅支持 minute、hour、day")


SOURCE_ASSET_LABELS = ASSET_LABELS
