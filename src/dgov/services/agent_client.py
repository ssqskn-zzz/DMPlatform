"""HTTP client for registered agent API endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from dgov.core.config import settings
from dgov.core.crypto import decrypt_secret
from dgov.core.exceptions import BadRequestError
from dgov.devtools.mock_agent_api import app as mock_agent_app
from dgov.models.lineage_insight import AgentEndpoint
from dgov.schemas.agent_contract import (
    DiagnoseRequest,
    DiagnoseResponse,
    ParseRequest,
    ParseResponse,
)

logger = logging.getLogger(__name__)

INTERNAL_MOCK = "internal://mock-agent"


class AgentCallError(RuntimeError):
    """Raised after retries are exhausted or the contract response is invalid."""


class AgentApiClient:
    def __init__(
        self,
        endpoint: AgentEndpoint,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        fail_header: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.base_url = endpoint.base_url.rstrip("/")
        self.timeout = endpoint.timeout_ms / 1000.0
        self.max_retries = endpoint.max_retries
        self._explicit_transport = transport
        self.fail_header = fail_header
        token = decrypt_secret(endpoint.auth_token_enc, settings.secret_key)
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            if endpoint.auth_type == "bearer":
                self.headers["Authorization"] = f"Bearer {token}"
            elif endpoint.auth_type == "api_key":
                self.headers["X-API-Key"] = token
        if fail_header:
            self.headers["X-Mock-Fail"] = fail_header

    def _transport(self) -> httpx.AsyncBaseTransport | None:
        if self._explicit_transport is not None:
            return self._explicit_transport
        if self.base_url == INTERNAL_MOCK:
            return httpx.ASGITransport(app=mock_agent_app)
        return None

    def _url(self, path: str) -> str:
        if self.base_url == INTERNAL_MOCK:
            # ASGITransport needs an absolute-looking URL; host is irrelevant.
            return f"http://mock-agent.local{path}"
        return f"{self.base_url}{path}"

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = self._url(path)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, transport=self._transport()
                ) as client:
                    response = await client.post(url, json=body, headers=self.headers)
                    response.raise_for_status()
                    payload = response.json()
                if not isinstance(payload, dict):
                    raise AgentCallError("agent response is not a JSON object")
                return payload
            except Exception as exc:  # retry transport / 5xx failures
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(
                        settings.agent_retry_backoff_ms / 1000.0 * (2**attempt)
                    )
        raise AgentCallError(f"agent call failed after {self.max_retries + 1} attempts: {last_error}")

    async def health(self) -> dict[str, Any]:
        url = self._url("/health")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport()) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def parse(self, payload: ParseRequest) -> ParseResponse:
        raw = await self._post("/v1/lineage/parse", payload.model_dump(mode="json"))
        try:
            return ParseResponse.model_validate(raw)
        except ValidationError as exc:
            raise AgentCallError(f"invalid parse contract: {exc}") from exc

    async def diagnose(self, payload: DiagnoseRequest) -> DiagnoseResponse:
        raw = await self._post("/v1/lineage/diagnose", payload.model_dump(mode="json"))
        try:
            return DiagnoseResponse.model_validate(raw)
        except ValidationError as exc:
            raise AgentCallError(f"invalid diagnose contract: {exc}") from exc

    async def test_contract(
        self, spec: dict[str, Any], edges: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Connectivity + contract smoke test used by the endpoint management UI."""
        from dgov.schemas.agent_contract import LineageEdgePayload, SpecPayload

        health = await self.health()
        spec_payload = SpecPayload(
            version=str(spec.get("version") or "v1"),
            clauses=list(spec.get("clauses") or []),
            full_text=str(spec.get("full_text") or ""),
        )
        sample_edges = [
            LineageEdgePayload.model_validate(edge) for edge in edges[:3]
        ] or [
            LineageEdgePayload(
                source_table_id="sample_src",
                target_table_id="sample_tgt",
                source_table="ods_sample_di",
                target_table="dwd_sample_di",
            )
        ]
        request_id = "contract-test"
        parsed = await self.parse(
            ParseRequest(
                request_id=request_id, spec=spec_payload, edges=sample_edges, scope={"mode": "test"}
            )
        )
        diagnosed = await self.diagnose(
            DiagnoseRequest(
                request_id=request_id,
                spec=spec_payload,
                edges=sample_edges,
                task_graph=parsed.result.task_graph,
            )
        )
        return {
            "health": health,
            "parse_nodes": len(parsed.result.task_graph.nodes),
            "parse_edges": len(parsed.result.task_graph.edges),
            "diagnose_issues": len(diagnosed.result.issues),
            "model": parsed.model,
        }


async def build_client(
    endpoint: AgentEndpoint,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    fail_header: str | None = None,
) -> AgentApiClient:
    if not endpoint.enabled:
        raise BadRequestError("智能体端点已停用")
    return AgentApiClient(endpoint, transport=transport, fail_header=fail_header)
