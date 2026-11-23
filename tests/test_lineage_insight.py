"""Lineage insight pipeline: agent contract, spec registry, rules, orchestration,
incremental ingestion, feedback loop and API surface.

All tests run against in-memory SQLite + ASGITransport; no external agent or
middleware is required (the built-in mock agent implements the same contract).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dgov.core.crypto import decrypt_secret, encrypt_secret
from dgov.core.exceptions import ConflictError
from dgov.devtools.mock_agent_api import app as mock_agent_app
from dgov.models.catalog import DataStandard
from dgov.models.datasource import DataSource
from dgov.models.governance import GovernanceAgent, GovernanceService, RuntimeLog
from dgov.models.lineage_insight import (
    AgentEndpoint,
    LineageAlert,
    LineageFlow,
    LineageFlowRun,
    LineageIssue,
)
from dgov.models.metadata import IngestRun, MetadataLineage
from dgov.schemas.agent_contract import (
    AgentIssue,
    DiagnoseRequest,
    DiagnoseResponse,
    ParseRequest,
    ParseResponse,
    SpecPayload,
)
from dgov.schemas.lineage_insight import LineageFlowCreate
from dgov.services import lineage_insight as insight
from dgov.services import lineage_spec
from dgov.services.agent_client import INTERNAL_MOCK, AgentApiClient, AgentCallError
from dgov.services.ingest import ingest_payload, run_collection
from dgov.services.lineage_rules import dedupe_key, run_rules
from dgov.services.metadata_client import INTERNAL_MOCK_BASE, SOURCE_ASSETS
from tests.mock_metadata_api.app import app as mock_api

API = "/api/v1"
SOURCE_CONFIG = {
    "mode": "test",
    "test_base_url": INTERNAL_MOCK_BASE,
    "production_base_url": "",
    "auth_type": "none",
    "page_size": 500,
}
AGENT_CODE = "AG-LINEAGE-TEST"
SERVICE_CODE = "SVC-LINEAGE-TEST"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

async def _create_source(
    factory: async_sessionmaker[AsyncSession], name: str = "测试血缘 API"
) -> int:
    async with factory() as session:
        source = DataSource(
            name=name,
            type="api",
            host="http://fixture",
            port=80,
            config=dict(SOURCE_CONFIG),
            description="pytest lineage source",
        )
        session.add(source)
        await session.commit()
        await session.refresh(source)
        return source.id


async def _seed_agent(
    factory: async_sessionmaker[AsyncSession],
    *,
    base_url: str = INTERNAL_MOCK,
    retrieval: str = "✓",
    output: str = "✕",
    auth_type: str = "none",
    token: str | None = None,
) -> int:
    async with factory() as session:
        service = GovernanceService(
            code=SERVICE_CODE,
            name="血缘解析服务",
            engine="Agent 就绪引擎",
            input_desc="规范 + 表级血缘",
            output_desc="任务依赖图 + 问题清单",
            evidence="运行档案",
            allow_system="✓",
            allow_retrieval=retrieval,
            allow_training="✕",
            allow_output=output,
        )
        agent = GovernanceAgent(
            code=AGENT_CODE,
            name="测试血缘智能体",
            kind="治理",
            owner="pytest",
            version="1.0",
            status="on",
            boundary="复核",
            description="测试用",
        )
        agent.services = [service]
        session.add(agent)
        endpoint = AgentEndpoint(
            agent_code=AGENT_CODE,
            name="测试端点",
            base_url=base_url,
            auth_type=auth_type,
            auth_token_enc=encrypt_secret(token, "dgov-dev-secret-change-me") if token else "",
            model="mock-lineage-agent",
            timeout_ms=2000,
            max_retries=0,
            enabled=True,
        )
        session.add(endpoint)
        await insight.ensure_default_flow(session)
        await session.commit()
        return endpoint.id


def _edge(
    src: str = "A",
    tgt: str = "B",
    *,
    src_name: str = "dwd_a_di",
    tgt_name: str = "dws_b_di",
    edge_type: str = "0",
    op: str = "upsert",
) -> dict[str, Any]:
    return {
        "source_table_id": src,
        "target_table_id": tgt,
        "source_table": src_name,
        "target_table": tgt_name,
        "source_schema": "",
        "target_schema": "",
        "type": edge_type,
        "op": op,
    }


# ==========================================================================
# 1. credential encryption
# ==========================================================================

def test_encrypt_secret_round_trip_and_tamper_detection() -> None:
    secret = "unit-test-secret"
    encoded = encrypt_secret("sk-abc-123", secret)
    assert encoded.startswith("dgov1$")
    assert "sk-abc-123" not in encoded
    assert decrypt_secret(encoded, secret) == "sk-abc-123"
    assert decrypt_secret(encoded + "x", secret) == ""
    assert decrypt_secret(encoded, "wrong-secret") == ""
    assert decrypt_secret(None, secret) == ""
    assert encrypt_secret(None, secret) == ""


def test_encrypt_secret_random_salt() -> None:
    a = encrypt_secret("same-token", "k")
    b = encrypt_secret("same-token", "k")
    assert a != b
    assert decrypt_secret(a, "k") == decrypt_secret(b, "k") == "same-token"


# ==========================================================================
# 2. agent API contract validation
# ==========================================================================

def test_parse_request_contract_requires_spec_and_edges() -> None:
    payload = ParseRequest(
        request_id="r1",
        spec=SpecPayload(version="v1", full_text="任务名与表名一致"),
        edges=[{"source_table_id": "A", "target_table_id": "B"}],
    )
    assert "任务名与表名一致" in payload.spec.full_text
    assert payload.edges[0].op == "upsert"


def test_contract_rejects_invalid_confidence_and_severity() -> None:
    with pytest.raises(ValidationError):
        AgentIssue(code="x", confidence=1.5)
    with pytest.raises(ValidationError):
        AgentIssue(code="x", severity="critical")
    with pytest.raises(ValidationError):
        DiagnoseResponse.model_validate(
            {"request_id": "r", "result": {"issues": [{"code": "x", "confidence": 2.0}]}}
        )
    ok = DiagnoseResponse.model_validate(
        {"request_id": "r", "result": {"issues": [{"code": "x", "confidence": 0.9}]}}
    )
    assert ok.result.issues[0].severity == "medium"


# ==========================================================================
# 3. built-in mock agent API
# ==========================================================================

@pytest.fixture
def agent_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=mock_agent_app)


async def test_mock_agent_health(agent_transport: httpx.ASGITransport) -> None:
    async with httpx.AsyncClient(transport=agent_transport, base_url="http://mock") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["contract"] == "v1"


async def test_mock_agent_parse_maps_tables_to_tasks(
    agent_transport: httpx.ASGITransport,
) -> None:
    request = ParseRequest(
        request_id="r-parse",
        spec=SpecPayload(version="v1", full_text="任务名与表名一致；血缘为任务间依赖"),
        edges=[
            {
                "source_table_id": "1", "target_table_id": "2",
                "source_table": "ods_order_di", "target_table": "dwd_order_di", "type": "0",
            },
            {
                "source_table_id": "2", "target_table_id": "3",
                "source_table": "dwd_order_di", "target_table": "ads_order_df", "type": "0",
            },
        ],
    )
    async with httpx.AsyncClient(transport=agent_transport, base_url="http://mock") as client:
        response = await client.post("/v1/lineage/parse", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    parsed = ParseResponse.model_validate(response.json())
    assert len(parsed.result.task_graph.nodes) == 3
    assert len(parsed.result.task_graph.edges) == 2
    assert "收到规范全文" in "".join(parsed.result.observations)
    assert any("v1" in item for item in parsed.result.observations)


async def test_mock_agent_parse_flags_temp_and_reverse_layer(
    agent_transport: httpx.ASGITransport,
) -> None:
    request = ParseRequest(
        request_id="r-parse2",
        spec=SpecPayload(version="v1", full_text=""),
        edges=[
            {
                "source_table_id": "1", "target_table_id": "2",
                "source_table": "tmp_x", "target_table": "dq_monitor_y",
            },
            {
                "source_table_id": "3", "target_table_id": "4",
                "source_table": "ads_x", "target_table": "ods_y",
            },
        ],
    )
    async with httpx.AsyncClient(transport=agent_transport, base_url="http://mock") as client:
        response = await client.post("/v1/lineage/parse", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    violations = ParseResponse.model_validate(response.json()).result.violations
    assert any("临时" in item for item in violations)
    assert any("逆向" in item for item in violations)


async def test_mock_agent_diagnose_reports_duplicates(
    agent_transport: httpx.ASGITransport,
) -> None:
    request = DiagnoseRequest(
        request_id="r-diag",
        spec=SpecPayload(version="v1", clauses=["任务名与表名一致"]),
        edges=[{"source_table_id": "A", "target_table_id": "B", "type": "0"}] * 3,
        policy={"categories": ["duplicate_edge"], "min_confidence": 0.6},
    )
    async with httpx.AsyncClient(transport=agent_transport, base_url="http://mock") as client:
        response = await client.post("/v1/lineage/diagnose", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    result = DiagnoseResponse.model_validate(response.json()).result
    assert len(result.issues) == 1
    assert result.issues[0].code == "duplicate_edge"
    assert result.issues[0].confidence >= 0.9


async def test_mock_agent_auth_and_failure_switch(
    agent_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOCK_AGENT_API_KEY", "secret-key")
    request = ParseRequest(request_id="r", spec=SpecPayload(version="v1"), edges=[])
    async with httpx.AsyncClient(transport=agent_transport, base_url="http://mock") as client:
        denied = await client.post("/v1/lineage/parse", json=request.model_dump(mode="json"))
        allowed = await client.post(
            "/v1/lineage/parse",
            json=request.model_dump(mode="json"),
            headers={"X-API-Key": "secret-key"},
        )
        failed = await client.post(
            "/v1/lineage/parse",
            json=request.model_dump(mode="json"),
            headers={"X-API-Key": "secret-key", "X-Mock-Fail": "1"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert failed.status_code == 503


# ==========================================================================
# 4. lineage development spec registry
# ==========================================================================

async def test_default_spec_is_auto_created(db_factory) -> None:
    async with db_factory() as session:
        spec = await lineage_spec.get_active_spec(session)
        assert spec["version"] == "v1"
        assert spec["status"] == "active"
        assert "任务名与表名" in spec["full_text"]
        assert "任务间依赖" in spec["full_text"]
        assert "血缘语义" in spec["full_text"]
        await session.commit()
    async with db_factory() as session:
        again = await lineage_spec.get_active_spec(session)
        assert again["version"] == "v1"
        count = (
            await session.execute(
                select(func.count())
                .select_from(DataStandard)
                .where(DataStandard.category == lineage_spec.SPEC_CATEGORY)
            )
        ).scalar_one()
        assert count == 1, "default spec must be idempotent"


async def test_publish_new_spec_version_archives_previous(db_factory) -> None:
    async with db_factory() as session:
        await lineage_spec.get_active_spec(session)
        v2 = await lineage_spec.publish_new_version(
            session,
            name="血缘规范 v2",
            full_text="# v2",
            actor="pytest",
        )
        await session.commit()
        assert v2["version"] == "v2"
    async with db_factory() as session:
        active = await lineage_spec.get_active_spec(session)
        assert active["version"] == "v2"
        assert active["full_text"] == "# v2"
        rows = list(
            (
                await session.execute(
                    select(DataStandard).where(
                        DataStandard.category == lineage_spec.SPEC_CATEGORY
                    )
                )
            ).scalars()
        )
        assert {row.status for row in rows} == {"active", "archived"}
        history = await lineage_spec.list_specs(session)
        assert len(history) == 2


# ==========================================================================
# 5. deterministic rule engine
# ==========================================================================

def test_rules_detect_duplicate_edges_from_raw_feed() -> None:
    graph = [_edge("A", "B"), _edge("C", "D")]
    raw = graph + [_edge("A", "B"), _edge("A", "B")]
    issues = run_rules(graph, raw_edges=raw)
    duplicates = [item for item in issues if item["code"] == "duplicate_edge"]
    assert len(duplicates) == 1
    assert duplicates[0]["evidence"]["count"] == 3
    assert duplicates[0]["confidence"] == 1.0
    assert duplicates[0]["source"] == "rule"


def test_rules_detect_cycle_self_loop_and_reverse_layer() -> None:
    issues = run_rules(
        [
            _edge("A", "B", src_name="dwd_a", tgt_name="dws_b"),
            _edge("B", "C", src_name="dws_b", tgt_name="dwd_c"),
            _edge("C", "A", src_name="dwd_c", tgt_name="dwd_a"),
            _edge("X", "X"),
        ]
    )
    codes = {item["code"] for item in issues}
    assert "circular_dependency" in codes
    assert "self_loop" in codes
    assert "invalid_layer_dependency" in codes


def test_rules_detect_dangling_ids_and_temp_tables() -> None:
    issues = run_rules(
        [
            _edge("A", "B", src_name="tmp_scratch", tgt_name="dwd_b"),
            _edge("C", "D", src_name="dwd_c", tgt_name="dq_monitor_4nps"),
        ],
        known_table_ids={"A", "B", "C", "D"},
    )
    codes = {item["code"] for item in issues}
    assert "temp_table_in_chain" in codes
    assert "dangling_id" not in codes

    dangling = run_rules([_edge("A", "B")], known_table_ids={"A"})
    assert any(item["code"] == "dangling_id" for item in dangling)


def test_dedupe_key_is_order_independent() -> None:
    assert dedupe_key("duplicate_edge", ["B", "A"]) == dedupe_key("duplicate_edge", ["A", "B"])


# ==========================================================================
# 6. agent client: retry, contract enforcement, token transport
# ==========================================================================

def _endpoint(**kwargs: Any) -> AgentEndpoint:
    defaults: dict[str, Any] = dict(
        agent_code=AGENT_CODE,
        name="test",
        base_url="http://agent.local",
        auth_type="none",
        auth_token_enc="",
        timeout_ms=1000,
        max_retries=0,
        enabled=True,
    )
    defaults.update(kwargs)
    return AgentEndpoint(**defaults)


async def test_agent_client_contract_rejects_bad_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/parse"):
            return httpx.Response(
                200,
                json={
                    "request_id": "r",
                    "result": {"task_graph": {"nodes": [{"task": 123}], "edges": []}},
                },
            )
        return httpx.Response(200, json={})

    client = AgentApiClient(_endpoint(), transport=httpx.MockTransport(handler))
    with pytest.raises(AgentCallError):
        await client.parse(ParseRequest(request_id="r", spec=SpecPayload(version="v1")))


async def test_agent_client_retries_then_raises() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"detail": "busy"})

    client = AgentApiClient(
        _endpoint(max_retries=2), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AgentCallError):
        await client.parse(ParseRequest(request_id="r", spec=SpecPayload(version="v1")))
    assert calls["n"] == 3


async def test_agent_client_sends_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_AGENT_API_KEY", "k-123")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("X-API-Key", "")
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"status": "ok"})

    ep = _endpoint(
        base_url="http://agent.local",
        auth_type="api_key",
        auth_token_enc=encrypt_secret("k-123", "dgov-dev-secret-change-me"),
    )
    client = AgentApiClient(ep, transport=httpx.MockTransport(handler))
    result = await client.health()
    assert result["status"] == "ok"
    assert seen["api_key"] == "k-123"

    ep2 = _endpoint(
        base_url="http://agent.local",
        auth_type="bearer",
        auth_token_enc=encrypt_secret("k-123", "dgov-dev-secret-change-me"),
    )
    client2 = AgentApiClient(ep2, transport=httpx.MockTransport(handler))
    await client2.health()
    assert seen["auth"] == "Bearer k-123"


async def test_agent_client_test_contract_with_internal_mock() -> None:
    ep = _endpoint(base_url=INTERNAL_MOCK)
    client = AgentApiClient(ep)
    result = await client.test_contract(
        {"version": "v1", "full_text": "任务名与表名一致"}, [_edge()]
    )
    assert result["health"]["status"] == "ok"
    assert result["parse_nodes"] == 2
    assert result["diagnose_issues"] == 0


# ==========================================================================
# 7. flow orchestration (service layer)
# ==========================================================================

async def test_run_flow_full_success_with_agent(db_factory) -> None:
    await _seed_agent(db_factory)
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, summary = await insight.run_flow(session, flow, actor="pytest")
        assert run.status == "completed"
        assert run.degraded is False
        assert summary["status"] == "completed"
        assert [stage["key"] for stage in run.stages_result] == [
            "snapshot",
            "parse",
            "diagnose",
            "alert",
        ]
        parse_detail = run.stages_result[1]["detail"]
        diag_detail = run.stages_result[2]["detail"]
        assert parse_detail["spec_version"] == "v1"
        assert parse_detail["spec_chars"] > 0
        assert parse_detail["edge_count"] == diag_detail["edge_count"] == 0
        assert parse_detail["agent_code"] == AGENT_CODE


async def test_run_flow_is_idempotent_by_input_hash(db_factory) -> None:
    await _seed_agent(db_factory)
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        first, _ = await insight.run_flow(session, flow, actor="pytest")
        second, cached = await insight.run_flow(session, flow, actor="pytest")
        assert cached["status"] == "cached"
        assert first.id == second.id
        forced, summary = await insight.run_flow(session, flow, actor="pytest", force=True)
        assert summary["status"] == "completed"
        assert forced.id != first.id


async def test_run_flow_uses_new_spec_version(db_factory) -> None:
    await _seed_agent(db_factory)
    async with db_factory() as session:
        await lineage_spec.get_active_spec(session)
        await lineage_spec.publish_new_version(
            session, name="v2", full_text="# v2", actor="pytest"
        )
        await session.commit()
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, _ = await insight.run_flow(session, flow, actor="pytest")
        assert run.spec_version == "v2"
        assert run.stages_result[1]["detail"]["spec_chars"] == len("# v2")


async def test_run_flow_degrades_when_agent_unavailable(db_factory) -> None:
    await _seed_agent(db_factory)
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, summary = await insight.run_flow(
            session, flow, actor="pytest", fail_header="1", force=True
        )
        assert run.status == "partial"
        assert run.degraded is True
        assert summary["degraded"] is True
        statuses = {stage["key"]: stage["status"] for stage in run.stages_result}
        assert statuses["snapshot"] == "success"
        assert statuses["parse"] == "degraded"
        assert statuses["diagnose"] == "degraded"
        assert statuses["alert"] == "success"
        assert run.stages_result[2]["detail"]["rule_issues"] >= 0
        assert run.finished_at is not None


async def test_run_flow_blocks_unauthorized_retrieval(db_factory) -> None:
    await _seed_agent(db_factory, retrieval="✕")
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, _ = await insight.run_flow(session, flow, actor="pytest")
        assert run.status == "partial"
        logs = list(
            (await session.execute(select(RuntimeLog).where(RuntimeLog.level == "BLOCK"))).scalars()
        )
        assert any("retrieval" in log.message for log in logs)


async def test_run_flow_drops_hallucinated_agent_findings(db_factory) -> None:
    await _seed_agent(db_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/parse"):
            return httpx.Response(
                200,
                json={"request_id": "r", "result": {"task_graph": {"nodes": [], "edges": []}}},
            )
        return httpx.Response(
            200,
            json={
                "request_id": "r",
                "result": {"issues": [
                    {"code": "fake_problem", "confidence": 0.99, "affected": ["NOT_IN_DB"]}
                ]},
            },
        )

    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, _ = await insight.run_flow(
            session, flow, actor="pytest", transport=httpx.MockTransport(handler)
        )
        assert run.status == "completed"
        assert run.stages_result[2]["detail"]["agent_issues"] == 0
        issues = list(
            (await session.execute(select(LineageIssue).where(LineageIssue.run_id == run.id))).scalars()
        )
        assert not any(issue.code == "fake_problem" for issue in issues)


async def test_full_pipeline_detects_real_fixture_problems_and_creates_alerts(db_factory) -> None:
    source_id = await _create_source(db_factory)
    await _seed_agent(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        flow = await insight.ensure_default_flow(session)
        run, summary = await insight.run_flow(
            session,
            flow,
            actor="pytest",
            changed_ids={"12900", "2077"},
        )
        # No collection yet -> empty graph; run collection first below
        assert summary["edge_count"] == 0
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        await run_collection(
            session,
            source,
            list(SOURCE_ASSETS),
            actor="pytest",
            transport=httpx.ASGITransport(app=mock_api),
            run_insight=False,
        )
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        run, summary = await insight.run_flow(session, flow, actor="pytest", force=True)
        assert run.status in {"completed", "partial"}
        assert summary["edge_count"] == 11
        codes = set(
            (await session.execute(select(LineageIssue.code).where(LineageIssue.run_id == run.id))).scalars()
        )
        assert "duplicate_edge" in codes, "3 duplicate groups from the fixture must be found"
        assert "temp_table_in_chain" in codes
        assert "dangling_id" in codes
        assert run.issue_count >= 20
        assert run.alert_count > 0


async def test_review_suppresses_future_alerts(db_factory) -> None:
    source_id = await _create_source(db_factory)
    await _seed_agent(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        await run_collection(
            session,
            source,
            list(SOURCE_ASSETS),
            actor="pytest",
            transport=httpx.ASGITransport(app=mock_api),
            run_insight=False,
        )
    async with db_factory() as session:
        flow = await insight.ensure_default_flow(session)
        first, _ = await insight.run_flow(session, flow, actor="pytest")
        issue = (
            await session.execute(
                select(LineageIssue)
                .where(LineageIssue.run_id == first.id, LineageIssue.code == "duplicate_edge")
                .limit(1)
            )
        ).scalar_one()
        key = issue.dedupe_key
        reviewed = await insight.review_issue(
            session,
            issue.id,
            type("Review", (), {"status": "ignored", "assignee": None, "review_note": "误报"})(),
            "pytest",
        )
        assert reviewed.status == "ignored"
        assert reviewed.reviewed_by == "pytest"

        second, _ = await insight.run_flow(session, flow, actor="pytest", force=True)
        new_alerts = list(
            (
                await session.execute(
                    select(LineageAlert).where(
                        LineageAlert.run_id == second.id, LineageAlert.dedupe_key == key
                    )
                )
            ).scalars()
        )
        assert new_alerts == [], "ignored issues must suppress subsequent alerts"


async def test_flow_crud_version_and_summary(db_factory) -> None:
    await _seed_agent(db_factory)
    async with db_factory() as session:
        flow = await insight.create_flow(
            session,
            LineageFlowCreate(code="custom_flow", name="自定义流程", trigger_type="manual"),
            "pytest",
        )
        assert flow.version == 1
        assert len(flow.stages) == 4
        updated = await insight.update_flow(
            session, flow, type("Upd", (), {"model_dump": lambda self, **kw: {"name": "改名"}})()
        )
        assert updated.version == 2
        assert updated.name == "改名"
        with pytest.raises(ConflictError):
            await insight.create_flow(
                session, LineageFlowCreate(code="custom_flow", name="重复"), "pytest"
            )
    async with db_factory() as session:
        flow = (
            await session.execute(select(LineageFlow).where(LineageFlow.code == "custom_flow"))
        ).scalar_one()
        await insight.run_flow(session, flow, actor="pytest")
        data = await insight.summary(session)
        assert data["runs"] == 1
        assert data["spec_version"] == "v1"
        assert data["active_flows"] >= 2


# ==========================================================================
# 8. incremental ingestion
# ==========================================================================

async def test_incremental_fetch_filters_by_since_and_keeps_edges(db_factory) -> None:
    source_id = await _create_source(db_factory)
    transport = httpx.ASGITransport(app=mock_api)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        full, sm1 = await run_collection(
            session, source, list(SOURCE_ASSETS), actor="pytest", transport=transport,
            run_insight=False,
        )
        assert full.status == "success"
        assert sm1["stats"]["lineage"]["new"] == 11
        assert sm1["stats"]["lineage_raw_rows"] == 18
        assert sm1["stats"]["lineage_duplicate_rows"] == 7
        assert sm1["cursor_to"] == "2026-11-20 00:00:00"
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        before = (
            await session.execute(
                select(func.count())
                .select_from(MetadataLineage)
                .where(MetadataLineage.status == "active")
            )
        ).scalar_one()
        incremental, sm2 = await run_collection(
            session,
            source,
            ["table_lineage"],
            actor="pytest",
            transport=transport,
            since="2026-11-20 00:00:00",
            run_insight=False,
        )
        after = (
            await session.execute(
                select(func.count())
                .select_from(MetadataLineage)
                .where(MetadataLineage.status == "active")
            )
        ).scalar_one()
        assert incremental.status == "success"
        assert sm2["incremental"] is True
        assert sm2["stats"]["rows_read"] == 2
        assert sm2["stats"]["lineage"]["removed"] == 0
        assert before == after == 11, "incremental mode must NOT deprecate unchanged edges"
        assert (source.config or {}).get("lineage_cursor") == "2026-11-20 00:00:00"
        assert len((source.config or {}).get("lineage_last_raw_edges") or []) == 18, (
            "incremental batches must merge into the full raw feed"
        )


async def test_collection_failure_is_recorded(db_factory) -> None:
    """A transport failure must return a failed run instead of raising (and the
    failure log must survive the rollback)."""
    source_id = await _create_source(db_factory)

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        run, summary = await run_collection(
            session,
            source,
            list(SOURCE_ASSETS),
            actor="pytest",
            transport=httpx.MockTransport(_boom),
            run_insight=False,
        )
        assert summary["status"] == "failed"
        assert run.status == "failed"
        assert "boom" in (run.error_message or "")
        persisted = await session.get(IngestRun, run.id)
        assert persisted is not None and persisted.status == "failed"


async def test_op_delete_deprecates_only_marked_edge(db_factory) -> None:
    source_id = await _create_source(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        await run_collection(
            session, source, list(SOURCE_ASSETS), actor="pytest",
            transport=httpx.ASGITransport(app=mock_api), run_insight=False,
        )
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        rows = [
            {
                "source_table_id": "12900",
                "target_table_id": "2077",
                "source_table": "ods_a",
                "target_table": "dwd_b",
                "lingeage_type": "1",
                "op": "delete",
                "updatetime": "2026-11-21 00:00:00",
            }
        ]
        stats = await ingest_payload(
            session, source, {"table_lineage": rows}, actor="pytest", incremental=True
        )
        assert stats["lineage"]["removed"] == 1
        active = (
            await session.execute(
                select(func.count())
                .select_from(MetadataLineage)
                .where(MetadataLineage.status == "active")
            )
        ).scalar_one()
        assert active == 10


async def test_full_feed_still_deprecates_missing_edges(db_factory) -> None:
    """Regression guard: full collection keeps its original deprecate behaviour."""
    source_id = await _create_source(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        rows = [
            {
                "source_table_id": "A", "target_table_id": "B",
                "source_table": "a", "target_table": "b", "lingeage_type": "0",
            },
            {
                "source_table_id": "C", "target_table_id": "D",
                "source_table": "c", "target_table": "d", "lingeage_type": "0",
            },
        ]
        await ingest_payload(session, source, {"table_lineage": rows}, actor="pytest")
        smaller = [rows[0]]
        stats = await ingest_payload(
            session, source, {"table_lineage": smaller}, actor="pytest"
        )
        assert stats["lineage"]["removed"] == 1


async def test_collection_event_triggers_insight_run(db_factory) -> None:
    source_id = await _create_source(db_factory)
    await _seed_agent(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        run, summary = await run_collection(
            session, source, list(SOURCE_ASSETS), actor="pytest",
            transport=httpx.ASGITransport(app=mock_api),
        )
        assert run.status == "success"
        assert summary["insight_run_ids"], "collection must trigger the event-driven flow"
    async with db_factory() as session:
        runs = list((await session.execute(select(LineageFlowRun))).scalars())
        assert len(runs) == 1
        assert runs[0].trigger_type == "event"
        assert runs[0].status == "completed"
        assert runs[0].issue_count >= 20


async def test_scheduled_lineage_job_reuses_cursor(db_factory, monkeypatch) -> None:
    source_id = await _create_source(db_factory)
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        config = dict(source.config or {})
        config["lineage_cursor"] = "2026-11-20 00:00:00"
        source.config = config
        await session.commit()
    async with db_factory() as session:
        source = await session.get(DataSource, source_id)
        run, summary = await run_collection(
            session,
            source,
            ["table_lineage"],
            trigger_type="schedule",
            actor="scheduler",
            transport=httpx.ASGITransport(app=mock_api),
            run_insight=False,
        )
        assert run.status == "success"
        assert summary["incremental"] is True
        assert summary["cursor_from"] == "2026-11-20 00:00:00"
        assert summary["stats"]["rows_read"] == 2


# ==========================================================================
# 9. API surface
# ==========================================================================

async def test_standard_registry_auto_versions_full_text(client) -> None:
    categories = (await client.get(f"{API}/standards/categories")).json()
    lineage = next(item for item in categories if item["code"] == "lineage_dev_spec")
    assert lineage["has_active"] is True

    created = await client.post(
        f"{API}/standards",
        json={
            "name": "数据质量规范",
            "category": "data_quality_spec",
            "rule": "# 质量规范 v1",
        },
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["version"] == "v1" and first["status"] == "active"

    updated = await client.patch(
        f"{API}/standards/{first['id']}", json={"rule": "# 质量规范 v2"}
    )
    assert updated.status_code == 200, updated.text
    second = updated.json()
    assert second["version"] == "v2" and second["id"] != first["id"]
    assert second["rule"] == "# 质量规范 v2"

    history = (
        await client.get(f"{API}/standards?category=data_quality_spec&page_size=50")
    ).json()["items"]
    assert {item["version"] for item in history} == {"v1", "v2"}
    assert {item["status"] for item in history} == {"active", "archived"}
    # Only the lineage category is consumed by the lineage insight pipeline.
    lineage_active = (await client.get(f"{API}/lineage-specs/active")).json()
    assert lineage_active["category"] == "lineage_dev_spec"


async def test_api_specs_and_publish(client: httpx.AsyncClient) -> None:
    active = await client.get(f"{API}/lineage-specs/active")
    assert active.status_code == 200, active.text
    assert active.json()["version"] == "v1"

    published = await client.post(
        f"{API}/lineage-specs/publish",
        json={"name": "血缘规范 v2", "full_text": "# v2"},
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == "v2"

    history = await client.get(f"{API}/lineage-specs")
    assert history.status_code == 200
    assert len(history.json()) == 2

    filtered = await client.get(
        f"{API}/standards", params={"category": "lineage_dev_spec", "active": "true"}
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["code"] == "lineage_dev_spec_v2"


async def test_api_agent_endpoint_crud_and_contract_test(client: httpx.AsyncClient) -> None:
    created = await client.post(
        f"{API}/agent-endpoints",
        json={
            "agent_code": AGENT_CODE,
            "name": "内置 Mock",
            "base_url": INTERNAL_MOCK,
            "auth_type": "bearer",
            "auth_token": "sk-secret",
            "model": "mock-lineage-agent",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_token"] is True
    assert "auth_token" not in body and "auth_token_enc" not in body
    endpoint_id = body["id"]

    tested = await client.post(f"{API}/agent-endpoints/{endpoint_id}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["reachable"] is True
    assert tested.json()["parse_nodes"] == 2

    listed = await client.get(f"{API}/agent-endpoints")
    assert listed.json()[0]["health_status"] == "ok"

    patched = await client.patch(
        f"{API}/agent-endpoints/{endpoint_id}", json={"name": "改名", "enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "改名"
    assert patched.json()["enabled"] is False

    duplicate = await client.post(
        f"{API}/agent-endpoints",
        json={"agent_code": AGENT_CODE, "name": "dup", "base_url": INTERNAL_MOCK},
    )
    assert duplicate.status_code == 409

    deleted = await client.delete(f"{API}/agent-endpoints/{endpoint_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"{API}/agent-endpoints")).json() == []


async def test_api_flow_and_run_lifecycle(client: httpx.AsyncClient, db_factory) -> None:
    await _seed_agent(db_factory)
    created = await client.post(
        f"{API}/lineage-flows",
        json={"code": "api_flow", "name": "API 流程", "trigger_type": "manual"},
    )
    assert created.status_code == 201, created.text
    flow_id = created.json()["id"]

    run = await client.post(
        f"{API}/lineage-flows/{flow_id}/run", json={"force": False}
    )
    assert run.status_code == 200, run.text
    assert run.json()["summary"]["status"] == "completed"
    run_id = run.json()["run"]["id"]

    detail = await client.get(f"{API}/lineage-runs/{run_id}")
    assert detail.status_code == 200
    assert len(detail.json()["stages_result"]) == 4

    issues = await client.get(f"{API}/lineage-issues", params={"run_id": run_id})
    assert issues.status_code == 200
    assert issues.json()["total"] == 0

    alerts = await client.get(f"{API}/lineage-alerts")
    assert alerts.status_code == 200

    summary = await client.get(f"{API}/lineage-insight/summary")
    assert summary.json()["runs"] >= 1

    patched = await client.patch(f"{API}/lineage-flows/{flow_id}", json={"name": "改名"})
    assert patched.json()["version"] == 2
    assert (await client.delete(f"{API}/lineage-flows/{flow_id}")).status_code == 204


async def test_api_full_insight_and_review_flow(client: httpx.AsyncClient, db_factory) -> None:
    source_id = await _create_source(db_factory)
    await _seed_agent(db_factory)
    collected = await client.post(
        f"{API}/datasources/{source_id}/collect", json={"assets": list(SOURCE_ASSETS)}
    )
    assert collected.status_code == 200, collected.text
    assert collected.json()["summary"]["insight_run_ids"]

    issues = await client.get(f"{API}/lineage-issues", params={"page_size": 100})
    assert issues.status_code == 200
    payload = issues.json()
    assert payload["total"] >= 20
    codes = {item["code"] for item in payload["items"]}
    assert "duplicate_edge" in codes

    duplicate = next(item for item in payload["items"] if item["code"] == "duplicate_edge")
    reviewed = await client.patch(
        f"{API}/lineage-issues/{duplicate['id']}",
        json={"status": "confirmed", "review_note": "确认重复上报"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
    assert reviewed.json()["reviewed_by"] == "admin"

    filtered = await client.get(
        f"{API}/lineage-issues", params={"status": "confirmed", "page_size": 100}
    )
    assert filtered.json()["total"] >= 1

    alerts = await client.get(f"{API}/lineage-alerts")
    assert alerts.status_code == 200
    assert alerts.json()
    retried = await client.post(f"{API}/lineage-alerts/{alerts.json()[0]['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["retry_count"] == 1


async def test_api_incremental_collect_param(client: httpx.AsyncClient, db_factory) -> None:
    source_id = await _create_source(db_factory)
    # full first
    full = await client.post(f"{API}/datasources/{source_id}/collect")
    assert full.status_code == 200, full.text
    # explicit incremental
    incremental = await client.post(
        f"{API}/datasources/{source_id}/collect",
        json={"assets": ["table_lineage"], "since": "2026-11-20 00:00:00"},
    )
    assert incremental.status_code == 200, incremental.text
    body = incremental.json()["summary"]
    assert body["incremental"] is True
    assert body["cursor_from"] == "2026-11-20 00:00:00"
    assert body["stats"]["rows_read"] == 2


async def test_api_permissions_are_enforced(client: httpx.AsyncClient) -> None:
    # governance can configure flows but cannot manage agent credentials
    gov = await client.post(
        f"{API}/auth/login",
        json={"username": "governance", "password": "abc123456789"},
    )
    assert gov.status_code == 200
    gov_token = gov.json()["token"]
    headers = {"Authorization": f"Bearer {gov_token}"}

    denied = await client.post(
        f"{API}/agent-endpoints",
        json={"agent_code": "X", "name": "x", "base_url": INTERNAL_MOCK},
        headers=headers,
    )
    assert denied.status_code == 403

    allowed = await client.post(
        f"{API}/lineage-flows",
        json={"code": "gov_flow", "name": "治理流程", "trigger_type": "manual"},
        headers=headers,
    )
    assert allowed.status_code == 201

    viewer = await client.post(
        f"{API}/auth/login", json={"username": "viewer", "password": "abc123456789"}
    )
    viewer_headers = {"Authorization": f"Bearer {viewer.json()['token']}"}
    read_ok = await client.get(f"{API}/lineage-specs/active", headers=viewer_headers)
    assert read_ok.status_code == 200
    write_denied = await client.post(
        f"{API}/lineage-specs/publish",
        json={"name": "x", "full_text": "x"},
        headers=viewer_headers,
    )
    assert write_denied.status_code == 403
    # restore admin token so the shared fixture stays usable
    admin_login = await client.post(
        f"{API}/auth/login",
        json={"username": "admin", "password": "abc123456789"},
    )
    client.headers.update({"Authorization": f"Bearer {admin_login.json()['token']}"})


async def test_api_contract_rejects_invalid_flow_code(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{API}/lineage-flows",
        json={"code": "INVALID CODE!", "name": "x", "trigger_type": "manual"},
    )
    assert response.status_code == 422
