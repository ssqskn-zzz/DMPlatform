"""In-process mock lineage agent API.

Mounted at ``/mock/agent`` in development and also reachable through the
``internal://mock-agent`` pseudo base URL, so the whole pipeline works without
any external service.  It implements the platform contract in
``schemas/agent_contract.py`` and can be made to fail on demand for fallback
tests (``X-Mock-Fail: 1`` header).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from dgov.schemas.agent_contract import (
    DiagnoseRequest,
    DiagnoseResponse,
    ParseRequest,
    ParseResponse,
)

app = FastAPI(title="Mock Lineage Agent API", version="0.1.0", include_in_schema=False)

LAYER_ORDER = {"ODS": 0, "STG": 1, "DWD": 2, "DWS": 3, "DIM": 3, "ADS": 4, "TMP": -1, "OTHER": -1}


def _check_auth(x_api_key: str | None, authorization: str | None) -> None:
    expected = os.getenv("MOCK_AGENT_API_KEY")
    if not expected:
        return
    if x_api_key == expected or authorization == f"Bearer {expected}":
        return
    raise HTTPException(status_code=401, detail="invalid agent api key")


def _check_fail(x_mock_fail: str | None) -> None:
    if x_mock_fail:
        raise HTTPException(status_code=503, detail="mock agent unavailable")


def _layer_of(name: str, schema: str, hint: str) -> str:
    if hint:
        return hint.upper()
    value = (schema or "").upper()
    if value in LAYER_ORDER:
        return value
    for prefix in ("ods_", "stg_", "dwd_", "dws_", "ads_", "dim_", "tmp_"):
        if (name or "").lower().startswith(prefix):
            return prefix[:3].upper()
    return "OTHER"


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model": "mock-lineage-agent", "contract": "v1"}


@app.post("/v1/lineage/parse")
async def parse(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_mock_fail: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, authorization)
    _check_fail(x_mock_fail)
    try:
        payload = ParseRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    nodes: dict[str, dict[str, str]] = {}
    edges = []
    violations: list[str] = []
    for edge in payload.edges:
        src_layer = _layer_of(edge.source_table, edge.source_schema, "")
        tgt_layer = _layer_of(edge.target_table, edge.target_schema, "")
        for table, layer in (
            (edge.source_table, src_layer),
            (edge.target_table, tgt_layer),
        ):
            key = table or edge.source_table_id
            nodes.setdefault(key, {"task": key, "table": table, "layer": layer})
        edges.append(
            {
                "source_task": edge.source_table or edge.source_table_id,
                "target_task": edge.target_table or edge.target_table_id,
                "confidence": 0.9,
            }
        )
        for name in (edge.source_table, edge.target_table):
            low = (name or "").lower()
            if low.startswith("tmp_") or low.startswith("dq_monitor_"):
                violations.append(f"临时/监控表进入正式依赖链: {name}")
                break
        s_order, t_order = LAYER_ORDER.get(src_layer, -1), LAYER_ORDER.get(tgt_layer, -1)
        if s_order >= 0 and t_order >= 0 and t_order < s_order:
            violations.append(f"分层依赖逆向: {src_layer} → {tgt_layer}")

    response = ParseResponse(
        request_id=payload.request_id,
        result={
            "task_graph": {"nodes": list(nodes.values()), "edges": edges},
            "violations": sorted(set(violations)),
            "observations": [
                f"按规范 {payload.spec.version} 将表级血缘映射为任务依赖图",
                f"收到规范全文 {len(payload.spec.full_text or '')} 字",
                f"共 {len(payload.edges)} 条边、{len(nodes)} 个任务节点",
            ],
        },
        model="mock-lineage-agent",
        usage={"tokens_in": len(payload.edges) * 12, "tokens_out": len(edges) * 6},
    )
    return response.model_dump(mode="json")


@app.post("/v1/lineage/diagnose")
async def diagnose(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_mock_fail: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, authorization)
    _check_fail(x_mock_fail)
    try:
        payload = DiagnoseRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    allowed = set(payload.policy.categories)
    issues: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        if kwargs["code"] in allowed and kwargs["confidence"] >= payload.policy.min_confidence:
            issues.append(kwargs)

    # Duplicate edges: semantic agent confirms what the local engine also detects.
    counts: dict[tuple[str, str, str], int] = {}
    for edge in payload.edges:
        key = (edge.source_table_id, edge.target_table_id, edge.type)
        counts[key] = counts.get(key, 0) + 1
    for (src, tgt, _), count in counts.items():
        if count > 1:
            add(
                code="duplicate_edge",
                category="duplicate_edge",
                severity="medium",
                confidence=0.93,
                affected=[src, tgt],
                evidence=f"同一条边出现 {count} 次",
                suggestion="按 (源, 目标, 类型) 去重",
            )

    # Semantic-only findings the deterministic engine is not asked to produce.
    for edge in payload.edges:
        for name in (edge.source_table, edge.target_table):
            low = (name or "").lower()
            if low.startswith("tmp_") or low.startswith("dq_monitor_"):
                add(
                    code="temp_table_in_chain",
                    category="temp_table_in_chain",
                    severity="medium",
                    confidence=0.78,
                    affected=[edge.source_table_id, edge.target_table_id],
                    evidence=f"任务/表名 {name} 不符合开发规范命名",
                    suggestion="改用 ods_/dwd_/dws_/ads_ 前缀的正式表名",
                )
                break

    if not (payload.spec.full_text or "").strip():
        add(
            code="missing_spec",
            category="missing_spec",
            severity="low",
            confidence=0.6,
            affected=[],
            evidence="本次调用未携带生效的开发规范",
            suggestion="在标准规范中发布血缘开发规范",
        )

    response = DiagnoseResponse(
        request_id=payload.request_id,
        result={"issues": issues},
        model="mock-lineage-agent",
        usage={"tokens_in": len(payload.edges) * 8, "tokens_out": len(issues) * 20},
    )
    return response.model_dump(mode="json")
