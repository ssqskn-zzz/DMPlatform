from __future__ import annotations

import logging
from typing import Any

import httpx

from dgov.core.config import settings
from dgov.core.exceptions import BadRequestError
from dgov.models.datasource import DataSource

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = {
    "table_info": "/metadata/tables",
    "column_metadata": "/metadata/columns",
    "table_lineage": "/metadata/lineage",
}
ASSET_LABELS = {
    "table_info": "表元数据",
    "column_metadata": "字段元数据",
    "table_lineage": "表级血缘",
}
SOURCE_ASSETS = tuple(DEFAULT_ENDPOINTS)


def source_config(datasource: DataSource) -> dict[str, Any]:
    return dict(datasource.config or {})


def resolve_base_url(datasource: DataSource) -> str:
    """Resolve the active endpoint, making test/production switching explicit."""

    config = source_config(datasource)
    mode = str(config.get("mode") or "test").lower()
    if mode not in {"test", "production"}:
        raise BadRequestError("数据源环境必须是 test 或 production")
    key = "test_base_url" if mode == "test" else "production_base_url"
    base_url = str(config.get(key) or "").strip()
    if not base_url:
        raise BadRequestError(
            f"数据源未配置 {key}，请在数据源配置中补全{('测试' if mode == 'test' else '生产')}环境地址"
        )
    if not base_url.startswith(("http://", "https://")):
        raise BadRequestError("数据源地址必须以 http:// 或 https:// 开头")
    return base_url.rstrip("/")


def resolve_endpoints(datasource: DataSource) -> dict[str, str]:
    configured = source_config(datasource).get("endpoints") or {}
    return {
        asset: str(configured.get(asset) or path)
        for asset, path in DEFAULT_ENDPOINTS.items()
    }


def auth_headers(datasource: DataSource) -> dict[str, str]:
    config = source_config(datasource)
    token = str(config.get("auth_token") or "").strip()
    if not token:
        return {}
    auth_type = str(config.get("auth_type") or "none").lower()
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "api_key":
        header = str(config.get("auth_header") or "X-API-Key")
        return {header: token}
    return {}


class MetadataApiClient:
    """Paginated client for a metadata API data source."""

    def __init__(self, datasource: DataSource, transport: httpx.AsyncBaseTransport | None = None):
        self.datasource = datasource
        self.base_url = resolve_base_url(datasource)
        self.endpoints = resolve_endpoints(datasource)
        self.headers = auth_headers(datasource)
        self.page_size = int(source_config(datasource).get("page_size") or 500)
        self.transport = transport

    @property
    def asset_urls(self) -> dict[str, str]:
        return {asset: f"{self.base_url}{path}" for asset, path in self.endpoints.items()}

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=settings.metadata_api_timeout,
            transport=self.transport,
        ) as client:
            response = await client.get(f"{self.base_url}/health", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def fetch(self, asset: str) -> list[dict[str, Any]]:
        if asset not in self.endpoints:
            raise BadRequestError(f"不支持的采集资产: {asset}")
        url = f"{self.base_url}{self.endpoints[asset]}"
        items: list[dict[str, Any]] = []
        offset = 0
        async with httpx.AsyncClient(
            timeout=settings.metadata_api_timeout,
            transport=self.transport,
        ) as client:
            while True:
                response = await client.get(
                    url,
                    params={"limit": self.page_size, "offset": offset},
                    headers=self.headers,
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, list):
                    page, has_more = payload, False
                else:
                    page = payload.get("items") or []
                    has_more = payload.get("has_more")
                    if has_more is None:
                        has_more = len(page) >= self.page_size
                items.extend(page)
                offset += len(page)
                if not page or not has_more:
                    break
                if offset >= 100_000:  # defensive guard against a broken pager
                    raise BadRequestError("采集分页超过安全上限，请检查 API 是否返回 has_more")
        logger.info(
            "metadata api fetched %s rows for %s from %s", len(items), asset, url
        )
        return items

    async def fetch_assets(self, assets: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {asset: await self.fetch(asset) for asset in assets}
