from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.models.metadata import (
    ColumnLineage,
    MetadataColumn,
    MetadataLineage,
    MetadataTable,
    OntologyEntity,
    OntologyEntityTable,
    OntologyRelation,
    WarehouseModel,
)

ENTITY_SPECS: tuple[dict[str, Any], ...] = (
    {"code": "contract", "name": "合同", "category": "业务对象", "keywords": ("contract", "合同", "协议")},
    {"code": "fund", "name": "基金", "category": "业务对象", "keywords": ("fund", "基金", "资管", "募集")},
    {"code": "customer", "name": "客户", "category": "参与方", "keywords": ("customer", "cust", "客户")},
    {
        "code": "employee",
        "name": "员工",
        "category": "参与方",
        "keywords": ("employee", "staff", "员工", "人员", "hr_"),
    },
    {"code": "project", "name": "项目", "category": "业务对象", "keywords": ("project", "项目", "标的")},
    {
        "code": "account",
        "name": "账户",
        "category": "业务对象",
        "keywords": ("account", "账户", "专户", "科目"),
    },
    {
        "code": "transaction",
        "name": "交易流水",
        "category": "业务事件",
        "keywords": ("transaction", "trade", "流水", "交易", "凭证"),
    },
    {"code": "product", "name": "产品", "category": "业务对象", "keywords": ("product", "产品", "理财")},
    {
        "code": "organization",
        "name": "机构",
        "category": "参与方",
        "keywords": ("org", "机构", "部门", "公司"),
    },
    {"code": "audit", "name": "审计事项", "category": "业务事件", "keywords": ("audit", "审计", "稽核")},
)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


async def rebuild_warehouse_models(session: AsyncSession) -> int:
    tables = list(
        (await session.execute(select(MetadataTable).where(MetadataTable.status == "active"))).scalars()
    )
    if not tables:
        return 0

    column_rows = (
        await session.execute(
            select(
                MetadataColumn.table_id,
                func.count(),
                func.sum(case((MetadataColumn.comment.is_(None), 1), else_=0)),
                func.sum(case((MetadataColumn.type_family == "string", 1), else_=0)),
            )
            .where(MetadataColumn.status == "active")
            .group_by(MetadataColumn.table_id)
        )
    ).all()
    stats = {
        table_id: {"count": count, "missing": missing or 0, "strings": strings or 0}
        for table_id, count, missing, strings in column_rows
    }

    existing = {
        model.table_id: model
        for model in (await session.execute(select(WarehouseModel))).scalars()
    }
    for table in tables:
        info = stats.get(table.id, {"count": 0, "missing": 0, "strings": 0})
        naming_ok = table.layer != "OTHER" and bool(NAME_PATTERN.match(table.name.lower()))
        string_ratio = info["strings"] / info["count"] if info["count"] else 0.0
        type_ok = string_ratio <= 0.85
        comment_ok = bool((table.comment or "").strip())
        column_comment_ratio = (
            (info["count"] - info["missing"]) / info["count"] if info["count"] else 1.0
        )
        score = (
            (35 if naming_ok else 0)
            + (25 if type_ok else 0)
            + (20 if comment_ok else 0)
            + round(column_comment_ratio * 20, 2)
        )
        model = existing.get(table.id)
        if model is None:
            model = WarehouseModel(table_id=table.id, model_name=table.name)
            session.add(model)
        model.model_name = table.name
        model.layer = table.layer
        model.domain = table.domain
        model.grain = table.grain
        model.column_count = info["count"]
        model.naming_compliant = naming_ok
        model.type_compliant = type_ok
        model.score = round(score, 2)
        model.status = "registered"
    await session.flush()
    return len(tables)


async def rebuild_ontology(session: AsyncSession) -> dict[str, int]:
    """Re-derive business entities and entity relations from active metadata."""

    entities = {
        entity.code: entity
        for entity in (await session.execute(select(OntologyEntity))).scalars()
    }
    for spec in ENTITY_SPECS:
        entity = entities.get(spec["code"])
        if entity is None:
            entity = OntologyEntity(code=spec["code"], name=spec["name"], category=spec["category"])
            session.add(entity)
            await session.flush()
            entities[spec["code"]] = entity
        entity.name = spec["name"]
        entity.category = spec["category"]
        entity.description = f"依据表名与注释关键词识别的「{spec['name']}」业务实体"

    await session.execute(delete(OntologyEntityTable))
    await session.execute(delete(OntologyRelation))

    tables = list(
        (await session.execute(select(MetadataTable).where(MetadataTable.status == "active"))).scalars()
    )
    table_entity: dict[str, str] = {}
    counts: dict[str, int] = {}
    for table in tables:
        haystack = f"{table.name} {table.comment or ''} {table.db_name or ''}".lower()
        best_code, best_score = None, 0
        for spec in ENTITY_SPECS:
            score = sum(1 for keyword in spec["keywords"] if keyword.lower() in haystack)
            if score > best_score:
                best_code, best_score = spec["code"], score
        if not best_code:
            continue
        entity = entities[best_code]
        session.add(
            OntologyEntityTable(
                entity_id=entity.id,
                table_id=table.id,
                match_field="table_name/comment",
                confidence=min(0.6 + best_score * 0.1, 0.95),
            )
        )
        table_entity[table.external_id] = best_code
        counts[best_code] = counts.get(best_code, 0) + 1

    for code, entity in entities.items():
        entity.table_count = counts.get(code, 0)

    edges = list(
        (
            await session.execute(
                select(MetadataLineage).where(MetadataLineage.status == "active")
            )
        ).scalars()
    )
    weights: dict[tuple[str, str], int] = {}
    for edge in edges:
        source = table_entity.get(edge.source_table_id)
        target = table_entity.get(edge.target_table_id)
        if source and target and source != target:
            weights[(source, target)] = weights.get((source, target), 0) + 1

    for (source_code, target_code), weight in weights.items():
        session.add(
            OntologyRelation(
                source_entity_id=entities[source_code].id,
                target_entity_id=entities[target_code].id,
                relation_type="data_flow",
                weight=weight,
            )
        )
    await session.flush()
    return {"entities": len(counts), "relations": len(weights)}


async def warehouse_summary(session: AsyncSession) -> dict[str, Any]:
    total = (
        await session.execute(select(func.count()).select_from(WarehouseModel))
    ).scalar_one()
    layers = (
        await session.execute(
            select(WarehouseModel.layer, func.count()).group_by(WarehouseModel.layer)
        )
    ).all()
    domains = (
        await session.execute(
            select(WarehouseModel.domain, func.count()).group_by(WarehouseModel.domain)
        )
    ).all()
    naming = (
        await session.execute(
            select(func.sum(case((WarehouseModel.naming_compliant.is_(True), 1), else_=0)))
        )
    ).scalar_one()
    typed = (
        await session.execute(
            select(func.sum(case((WarehouseModel.type_compliant.is_(True), 1), else_=0)))
        )
    ).scalar_one()
    score = (await session.execute(select(func.avg(WarehouseModel.score)))).scalar_one()
    return {
        "total": total,
        "layers": [{"name": name, "count": count} for name, count in layers],
        "domains": [{"name": name, "count": count} for name, count in domains],
        "naming_rate": round((naming or 0) / total * 100, 1) if total else 0.0,
        "type_rate": round((typed or 0) / total * 100, 1) if total else 0.0,
        "avg_score": round(float(score or 0), 1),
    }


async def list_models(
    session: AsyncSession,
    *,
    layer: str | None = None,
    domain: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    stmt = select(WarehouseModel)
    count_stmt = select(func.count()).select_from(WarehouseModel)
    conditions = []
    if layer:
        conditions.append(WarehouseModel.layer == layer)
    if domain:
        conditions.append(WarehouseModel.domain == domain)
    if keyword:
        conditions.append(WarehouseModel.model_name.ilike(f"%{keyword}%"))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = (await session.execute(count_stmt)).scalar_one()
    models = list(
        (
            await session.execute(
                stmt.order_by(WarehouseModel.layer, WarehouseModel.model_name)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    table_ids = [model.table_id for model in models]
    comments: dict[int, str | None] = {}
    lineage_degree: dict[int, int] = {}
    if table_ids:
        rows = (
            await session.execute(
                select(MetadataTable.id, MetadataTable.comment).where(
                    MetadataTable.id.in_(table_ids)
                )
            )
        ).all()
        comments = dict(rows)
        table_external = {
            external: table_id
            for table_id, external in (
                await session.execute(
                    select(MetadataTable.id, MetadataTable.external_id).where(
                        MetadataTable.id.in_(table_ids)
                    )
                )
            ).all()
        }
        edges = (
            await session.execute(
                select(MetadataLineage.source_table_id, MetadataLineage.target_table_id).where(
                    MetadataLineage.status == "active"
                )
            )
        ).all()
        for source, target in edges:
            if source in table_external:
                lineage_degree[table_external[source]] = lineage_degree.get(table_external[source], 0) + 1
            if target in table_external:
                lineage_degree[table_external[target]] = lineage_degree.get(table_external[target], 0) + 1

    items = [
        {
            "id": model.id,
            "table_id": model.table_id,
            "model_name": model.model_name,
            "layer": model.layer,
            "domain": model.domain,
            "grain": model.grain,
            "column_count": model.column_count,
            "naming_compliant": model.naming_compliant,
            "type_compliant": model.type_compliant,
            "score": model.score,
            "status": model.status,
            "comment": comments.get(model.table_id),
            "lineage_degree": lineage_degree.get(model.table_id, 0),
        }
        for model in models
    ]
    return items, total


async def ontology_view(session: AsyncSession) -> dict[str, Any]:
    entities = list(
        (await session.execute(select(OntologyEntity).order_by(OntologyEntity.id))).scalars()
    )
    relations = list(
        (await session.execute(select(OntologyRelation).order_by(OntologyRelation.weight.desc()))).scalars()
    )
    mappings = (
        await session.execute(
            select(
                OntologyEntityTable.entity_id,
                MetadataTable.name,
                MetadataTable.external_id,
                OntologyEntityTable.confidence,
            ).join(MetadataTable, MetadataTable.id == OntologyEntityTable.table_id)
        )
    ).all()
    samples: dict[int, list[dict[str, Any]]] = {}
    for entity_id, name, external_id, confidence in mappings:
        samples.setdefault(entity_id, []).append(
            {"name": name, "external_id": external_id, "confidence": confidence}
        )
    name_by_id = {entity.id: entity.name for entity in entities}
    return {
        "nodes": [
            {
                "id": entity.id,
                "code": entity.code,
                "name": entity.name,
                "category": entity.category,
                "table_count": entity.table_count,
                "samples": samples.get(entity.id, [])[:5],
            }
            for entity in entities
            if entity.table_count or samples.get(entity.id)
        ],
        "edges": [
            {
                "source": name_by_id.get(relation.source_entity_id),
                "target": name_by_id.get(relation.target_entity_id),
                "relation_type": relation.relation_type,
                "weight": relation.weight,
            }
            for relation in relations
        ],
        "stats": {
            "entity_count": len([entity for entity in entities if entity.table_count]),
            "mapped_tables": len(mappings),
            "relation_count": len(relations),
        },
    }


async def lineage_field_count(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(ColumnLineage))
    ).scalar_one()
