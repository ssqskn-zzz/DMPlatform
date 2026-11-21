"""Fixture metadata API used by the workbench in test environments.

Run standalone with::

    uvicorn tests.mock_metadata_api.app:app --port 8100 --app-dir /workspace

The payload is intentionally read-only and hard-coded from the CSV fixtures in
``data/`` so the workbench can exercise its metadata ingestion pipeline without a
real data warehouse.  A production deployment only needs to point the data
source's ``production_base_url`` at a compatible endpoint.
"""

from __future__ import annotations

import csv
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(__file__).parent / "data"

FILES = {
    "tables": "table_info.csv",
    "columns": "column_metadata.csv",
    "lineage": "table_lineage.csv",
}

# The uploaded lineage fixture references external systems whose column metadata
# is not part of the snapshot.  These extra catalog-internal edges make the
# field-level lineage capability demonstrable end to end.
SUPPLEMENTS = {
    "lineage": ("supplemental_lineage.csv",),
}

INT_FIELDS = {
    "table_life_day",
    "table_rec_count",
    "table_file_count",
    "lingeage_type",
}
FLOAT_FIELDS = {"table_size"}

app = FastAPI(
    title="Metadata Fixture API",
    version="0.1.0",
    description="Read-only metadata API backed by the test CSV fixtures.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalise(key: str, value: str) -> Any:
    value = value.strip()
    if value == "":
        return None
    if key in INT_FIELDS:
        try:
            return int(float(value))
        except ValueError:
            return None
    if key in FLOAT_FIELDS:
        try:
            return float(value)
        except ValueError:
            return None
    return value


@lru_cache
def load_rows(dataset: str) -> tuple[dict[str, Any], ...]:
    paths = [DATA_DIR / FILES[dataset]]
    paths.extend(DATA_DIR / name for name in SUPPLEMENTS.get(dataset, ()))
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows.extend(
                {key: _normalise(key, value or "") for key, value in row.items()}
                for row in reader
            )
    return tuple(rows)


def _check_token(token: str | None) -> None:
    expected = os.getenv("MOCK_METADATA_API_KEY")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


def _filter(
    rows: tuple[dict[str, Any], ...],
    params: dict[str, str | None],
) -> list[dict[str, Any]]:
    result = list(rows)
    for field, expected in params.items():
        if expected:
            result = [row for row in result if str(row.get(field)) == expected]
    return result


def _page(
    rows: list[dict[str, Any]],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(rows),
    }


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, int]:
    return {
        "tables": len(load_rows("tables")),
        "columns": len(load_rows("columns")),
        "lineage": len(load_rows("lineage")),
    }


@app.get("/metadata/tables")
async def tables(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    table_id: str | None = None,
    table_name: str | None = None,
    db_name: str | None = None,
    tenant_name: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(x_api_key)
    rows = _filter(
        load_rows("tables"),
        {
            "table_id": table_id,
            "table_name": table_name,
            "db_name": db_name,
            "tenant_name": tenant_name,
        },
    )
    return _page(rows, limit, offset)


@app.get("/metadata/columns")
async def columns(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    table_id: str | None = None,
    table_name: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(x_api_key)
    rows = _filter(load_rows("columns"), {"table_id": table_id, "table_name": table_name})
    return _page(rows, limit, offset)


@app.get("/metadata/lineage")
async def lineage(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    source_table_id: str | None = None,
    target_table_id: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(x_api_key)
    rows = _filter(
        load_rows("lineage"),
        {"source_table_id": source_table_id, "target_table_id": target_table_id},
    )
    return _page(rows, limit, offset)


@app.get("/metadata/tables/{table_id}/columns")
async def table_columns(
    table_id: str,
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(x_api_key)
    return _page(_filter(load_rows("columns"), {"table_id": table_id}), limit, offset)
