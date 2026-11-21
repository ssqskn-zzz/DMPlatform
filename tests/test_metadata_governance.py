from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dgov.core.exceptions import BadRequestError
from dgov.models.base import utcnow
from dgov.models.datasource import DataSource
from dgov.models.metadata import (
    ColumnLineage,
    MetadataColumn,
    MetadataTable,
    OperationLog,
    WarehouseModel,
)
from dgov.services import logs
from dgov.services.ingest import ingest_payload, next_run_at, run_collection
from dgov.services.metadata_client import SOURCE_ASSETS
from tests.mock_metadata_api.app import app as mock_api

API = "/api/v1"

SOURCE_CONFIG = {
    "mode": "test",
    "test_base_url": "http://fixture",
    "production_base_url": "",
    "auth_type": "none",
    "page_size": 500,
}


async def _create_source(
    factory: async_sessionmaker[AsyncSession], name: str = "测试元数据 API"
) -> int:
    async with factory() as session:
        source = DataSource(
            name=name,
            type="api",
            host="http://fixture",
            port=80,
            config=dict(SOURCE_CONFIG),
            description="pytest fixture source",
        )
        session.add(source)
        await session.commit()
        await session.refresh(source)
        return source.id


async def test_fixture_api_pipeline_and_read_modules(
    client: httpx.AsyncClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    source_id = await _create_source(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        run, summary = await run_collection(
            session,
            source,
            list(SOURCE_ASSETS),
            actor="pytest",
            transport=httpx.ASGITransport(app=mock_api),
        )
        assert run.status == "success", run.error_message

    stats = summary["stats"]
    assert stats["tables"]["new"] == 100
    assert stats["columns"]["new"] == 1938
    assert stats["lineage"]["new"] == 11
    assert stats["column_lineage_new"] == 17
    assert summary["quality"]["checks"] == 800

    response = await client.get(f"{API}/assets/summary")
    assert response.status_code == 200, response.text
    summary_payload = response.json()
    assert summary_payload["tables"] == 100
    assert summary_payload["columns"] == 1938
    assert summary_payload["lineage_edges"] == 11
    assert summary_payload["layers"]

    response = await client.get(f"{API}/assets/tables", params={"page_size": 5})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 100
    assert len(page["items"]) == 5
    table = page["items"][0]
    assert table["column_count"] > 0

    response = await client.get(f"{API}/assets/tables/{table['id']}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["columns"]
    assert detail["table"]["external_id"] == table["external_id"]

    response = await client.get(f"{API}/assets/columns", params={"page_size": 5})
    assert response.status_code == 200
    assert response.json()["total"] == 1938

    response = await client.get(f"{API}/lineage/edges", params={"page_size": 5})
    assert response.status_code == 200
    edges = response.json()
    assert edges["total"] == 11

    response = await client.get(
        f"{API}/assets/tables",
        params={"keyword": "dwd_SRC28_SRC52_SRC53_m_df", "page_size": 5},
    )
    source_table = response.json()["items"][0]
    response = await client.get(
        f"{API}/lineage/graph",
        params={"table_id": source_table["id"], "depth": 2, "direction": "downstream"},
    )
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["stats"]["nodes"] >= 2
    assert graph["edges"]

    response = await client.get(f"{API}/lineage/fields", params={"page_size": 5})
    assert response.status_code == 200
    assert response.json()["total"] > 0

    response = await client.get(f"{API}/lineage/impact/{source_table['id']}")
    assert response.status_code == 200
    assert response.json()["stats"]["impacted_tables"] >= 1

    response = await client.get(f"{API}/quality-center/summary")
    assert response.status_code == 200
    quality = response.json()
    assert quality["checks"] == 800
    assert quality["issues"] > 0

    response = await client.get(f"{API}/quality-center/rules")
    assert response.status_code == 200
    assert len(response.json()) == 8

    response = await client.get(f"{API}/quality-center/issues", params={"page_size": 5})
    assert response.status_code == 200
    assert response.json()["total"] > 0

    response = await client.post(f"{API}/quality-center/run")
    assert response.status_code == 200
    assert response.json()["checks"] == 800

    response = await client.get(f"{API}/quality-center/trend")
    assert response.status_code == 200
    assert len(response.json()) >= 2

    response = await client.get(f"{API}/warehouse/summary")
    assert response.status_code == 200
    assert response.json()["total"] == 100

    response = await client.get(f"{API}/warehouse/models", params={"page_size": 5})
    assert response.status_code == 200
    assert response.json()["total"] == 100

    response = await client.get(f"{API}/ontology-model")
    assert response.status_code == 200
    ontology = response.json()
    assert ontology["stats"]["entity_count"] >= 1
    assert ontology["nodes"]

    response = await client.get(f"{API}/logs/categories")
    assert response.status_code == 200
    categories = {item["category"]: item["count"] for item in response.json()}
    assert categories["asset_inventory"] >= 1
    assert categories["quality_check"] >= 1

    response = await client.get(
        f"{API}/logs", params={"category": "asset_inventory", "page_size": 5}
    )
    assert response.status_code == 200
    assert response.json()["total"] >= 1

    response = await client.get(f"{API}/logs/retention")
    assert response.status_code == 200
    assert response.json()["total"] >= 1

    response = await client.get(f"{API}/datasources/{source_id}/api-config")
    assert response.status_code == 200
    config = response.json()
    assert config["mode"] == "test"
    assert config["resolved_base_url"] == "http://fixture"
    assert config["has_auth_token"] is False

    response = await client.put(
        f"{API}/datasources/{source_id}/api-config",
        json={"mode": "production"},
    )
    assert response.status_code == 400

    response = await client.put(
        f"{API}/datasources/{source_id}/api-config",
        json={"mode": "production", "production_base_url": "https://meta.example.com"},
    )
    assert response.status_code == 200
    assert response.json()["resolved_base_url"] == "https://meta.example.com"

    response = await client.post(
        f"{API}/collection-jobs",
        json={
            "name": "测试调度",
            "datasource_id": source_id,
            "assets": list(SOURCE_ASSETS),
            "interval_unit": "minute",
            "interval_value": 1,
            "enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["interval_seconds"] == 60
    assert job["datasource_name"]

    response = await client.patch(
        f"{API}/collection-jobs/{job['id']}",
        json={"interval_unit": "hour", "interval_value": 2, "enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 7200
    assert response.json()["next_run_at"]

    response = await client.get(f"{API}/collection-jobs", params={"datasource_id": source_id})
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = await client.get(f"{API}/collection-jobs/{job['id']}/runs")
    assert response.status_code == 200
    assert response.json() == []


async def test_ingest_detects_changes_and_builds_field_lineage(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    source_id = await _create_source(db_factory, "变更检测 API")
    payload = {
        "table_info": [
            {
                "table_id": "t1",
                "table_name": "ods_src_a",
                "table_comment": "源表 A",
                "db_name": "test_db",
                "tenant_name": "测试租户",
                "table_rec_count": 1000,
                "table_size": 2048,
                "partition_newest": "2026-11-20",
                "table_last_dml_time": "2026-11-20 10:00:00",
            },
            {
                "table_id": "t2",
                "table_name": "dwd_a",
                "table_comment": "明细表 A",
                "db_name": "test_db",
                "table_rec_count": 800,
                "table_size": 1024,
                "partition_newest": "2026-11-20",
                "table_last_dml_time": "2026-11-20 11:00:00",
            },
        ],
        "column_metadata": [
            {
                "table_id": "t1",
                "column_name": "id",
                "column_comment": "主键",
                "column_type": "bigint",
            },
            {
                "table_id": "t1",
                "column_name": "amount",
                "column_comment": "金额",
                "column_type": "decimal(18,2)",
            },
            {
                "table_id": "t2",
                "column_name": "id",
                "column_comment": "主键",
                "column_type": "bigint",
            },
            {
                "table_id": "t2",
                "column_name": "amount",
                "column_comment": "金额",
                "column_type": "decimal(18,2)",
            },
            {
                "table_id": "t2",
                "column_name": "phone",
                "column_comment": "手机号",
                "column_type": "varchar(32)",
            },
        ],
        "table_lineage": [
            {
                "source_table_id": "t1",
                "source_table": "ods_src_a",
                "target_table_id": "t2",
                "target_table": "dwd_a",
                "source_schema": "ods",
                "target_schema": "dwd",
                "lingeage_type": 1,
            }
        ],
    }

    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        first = await ingest_payload(session, source, payload, actor="pytest")
        assert first["initial"] is True
        assert first["tables"]["new"] == 2
        assert first["columns"]["new"] == 5
        assert first["lineage"]["new"] == 1
        assert first["column_lineage_new"] == 2
        assert (
            await session.execute(select(func.count()).select_from(WarehouseModel))
        ).scalar_one() == 2
        assert (
            await session.execute(select(func.count()).select_from(ColumnLineage))
        ).scalar_one() == 2

        second = await ingest_payload(session, source, payload, actor="pytest")
        assert second["tables"]["new"] == 0
        assert second["tables"]["changed"] == 0
        assert second["columns"]["changed"] == 0
        assert second["lineage"]["new"] == 0

        payload["table_info"][0]["table_comment"] = "源表 A（已更新）"
        third = await ingest_payload(session, source, payload, actor="pytest")
        assert third["tables"]["changed"] == 1
        changes = (
            await session.execute(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.category == "metadata_change")
            )
        ).scalar_one()
        assert changes >= 1

        payload["column_metadata"] = [
            row for row in payload["column_metadata"] if row["column_name"] != "amount"
        ]
        fourth = await ingest_payload(session, source, payload, actor="pytest")
        assert fourth["columns"]["removed"] == 2
        assert fourth["column_lineage_new"] == 1

        table = (
            await session.execute(select(MetadataTable).where(MetadataTable.external_id == "t2"))
        ).scalar_one()
        phone = (
            await session.execute(
                select(MetadataColumn).where(
                    MetadataColumn.table_id == table.id, MetadataColumn.name == "phone"
                )
            )
        ).scalar_one()
        assert phone.sensitive_level == "L3"
        assert phone.data_category == "联系信息"


async def test_log_retention_uses_age_and_row_cap(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_factory() as session:
        for index in range(80):
            await logs.write_log(
                session,
                category="datasource_schedule",
                action="test.tick",
                message=f"tick {index}",
                commit=False,
            )
        await logs.write_log(
            session,
            category="datasource_schedule",
            action="test.old",
            message="expired entry",
            commit=False,
        )
        old = (
            await session.execute(
                select(OperationLog).where(OperationLog.action == "test.old")
            )
        ).scalar_one()
        old.created_at = utcnow() - timedelta(days=90)
        await session.commit()

        result = await logs.cleanup_logs(session, retention_days=30, max_rows=50)
        assert result["deleted_expired"] == 1
        assert result["deleted_overflow"] == 30
        remaining = (
            await session.execute(select(func.count()).select_from(OperationLog))
        ).scalar_one()
        assert remaining == 50


def test_next_run_at_supports_minute_hour_day() -> None:
    base = utcnow()
    assert (next_run_at("minute", 1, base) - base) == timedelta(minutes=1)
    assert (next_run_at("hour", 2, base) - base) == timedelta(hours=2)
    assert (next_run_at("day", 1, base) - base) == timedelta(days=1)
    with pytest.raises(BadRequestError):
        next_run_at("week", 1, base)


async def test_role_and_user_permission_configuration(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"{API}/permissions")
    assert response.status_code == 200
    catalog = response.json()
    codes = set(catalog["codes"])
    assert "module:asset-inventory:view" in codes
    assert "logs:read" in codes

    response = await client.post(
        f"{API}/roles",
        json={
            "code": "auditor",
            "name": "审计员",
            "description": "只读审计角色",
            "permissions": ["platform:read", "module:log-query:view"],
        },
    )
    assert response.status_code == 201, response.text
    role = response.json()
    assert set(role["permissions"]) == {"platform:read", "module:log-query:view"}

    response = await client.patch(
        f"{API}/roles/{role['id']}",
        json={"permissions": ["platform:read", "module:log-query:view", "logs:read"]},
    )
    assert response.status_code == 200
    assert "logs:read" in response.json()["permissions"]

    response = await client.post(
        f"{API}/users",
        json={"username": "auditor.one", "display_name": "审计一号", "role": "auditor"},
    )
    assert response.status_code == 201, response.text
    user = response.json()

    response = await client.put(
        f"{API}/users/{user['id']}/permissions",
        json={
            "overrides": [
                {"permission": "module:asset-inventory:view", "effect": "allow"},
                {"permission": "logs:read", "effect": "deny"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    permissions = response.json()["effective_permissions"]
    assert "module:asset-inventory:view" in permissions
    assert "logs:read" not in permissions

    response = await client.post(
        f"{API}/auth/login",
        json={"username": "auditor.one", "password": "abc123456789"},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"{API}/assets/summary", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/logs", headers=headers)).status_code == 403

    response = await client.get(f"{API}/roles", headers=headers)
    assert response.status_code == 403

    response = await client.delete(f"{API}/roles/{role['id']}")
    assert response.status_code == 409
