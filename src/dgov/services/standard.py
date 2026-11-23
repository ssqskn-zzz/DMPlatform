"""Versioned data standards.

A standard only stores its full Markdown text.  Every time the text changes the
service archives the current row and creates the next version automatically, so
users never maintain clause lists or version numbers by hand.
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.catalog import DataStandard
from dgov.schemas.catalog import StandardCreate, StandardUpdate
from dgov.services import logs

DEFAULT_CATEGORY = "general"

CATEGORY_LABELS: dict[str, str] = {
    "lineage_dev_spec": "血缘开发规范",
    "data_standard_spec": "数据标准规范",
    "data_quality_spec": "数据质量规范",
    "data_security_spec": "数据安全规范",
    "modeling_spec": "数仓建模规范",
    "metadata_spec": "元数据管理规范",
    "general": "通用规范",
}

_VERSION_SUFFIX = re.compile(r"_v(\d+)$")


def _version_number(row: DataStandard) -> int:
    extra_version = str((row.extra or {}).get("version") or "")
    match = re.fullmatch(r"v(\d+)", extra_version)
    if match:
        return int(match.group(1))
    match = _VERSION_SUFFIX.search(row.code or "")
    return int(match.group(1)) if match else 1


def _base_code(code: str) -> str:
    return _VERSION_SUFFIX.sub("", code or "") or "standard"


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or DEFAULT_CATEGORY, category or "通用规范")


async def list_standards(
    session: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    *,
    category: str | None = None,
    active: bool | None = None,
) -> tuple[list[DataStandard], int]:
    stmt = select(DataStandard)
    count_stmt = select(func.count()).select_from(DataStandard)
    if keyword:
        condition = or_(
            DataStandard.code.ilike(f"%{keyword}%"),
            DataStandard.name.ilike(f"%{keyword}%"),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if category:
        stmt = stmt.where(DataStandard.category == category)
        count_stmt = count_stmt.where(DataStandard.category == category)
    if active is not None:
        status = "active" if active else "archived"
        stmt = stmt.where(DataStandard.status == status)
        count_stmt = count_stmt.where(DataStandard.status == status)
    total = (await session.execute(count_stmt)).scalar_one()
    result = await session.execute(
        stmt.order_by(DataStandard.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total


async def list_categories(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(DataStandard.category, DataStandard.status, func.count()).group_by(
                DataStandard.category, DataStandard.status
            )
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for category, status, count in rows:
        bucket = counts.setdefault(category or DEFAULT_CATEGORY, {"total": 0, "active": 0})
        bucket["total"] += int(count or 0)
        if status == "active":
            bucket["active"] += int(count or 0)
    result = []
    for code, label in CATEGORY_LABELS.items():
        bucket = counts.pop(code, {"total": 0, "active": 0})
        result.append(
            {
                "code": code,
                "label": label,
                "versions": bucket["total"],
                "has_active": bucket["active"] > 0,
            }
        )
    for code, bucket in counts.items():
        result.append(
            {
                "code": code,
                "label": category_label(code),
                "versions": bucket["total"],
                "has_active": bucket["active"] > 0,
            }
        )
    return result


async def get_standard(session: AsyncSession, standard_id: int) -> DataStandard:
    standard = await session.get(DataStandard, standard_id)
    if standard is None:
        raise NotFoundError(f"data standard {standard_id} not found")
    return standard


async def _next_version(session: AsyncSession, category: str) -> int:
    rows = list(
        (
            await session.execute(
                select(DataStandard).where(DataStandard.category == category)
            )
        ).scalars()
    )
    return max((_version_number(row) for row in rows), default=0) + 1


async def publish_version(
    session: AsyncSession,
    *,
    category: str,
    name: str,
    full_text: str,
    description: str | None = None,
    actor: str | None = None,
) -> DataStandard:
    """Archive the active standard of ``category`` and create the next version."""

    category = category or DEFAULT_CATEGORY
    active = list(
        (
            await session.execute(
                select(DataStandard).where(
                    DataStandard.category == category,
                    DataStandard.status == "active",
                )
            )
        ).scalars()
    )
    base_code = _base_code(active[0].code) if active else category
    for row in active:
        row.status = "archived"
        base_code = _base_code(row.code)
    version = await _next_version(session, category)
    label = category_label(category)
    row = DataStandard(
        code=f"{base_code}_v{version}",
        name=name or f"{label} v{version}",
        category=category,
        data_type="markdown",
        rule=full_text,
        description=description or f"{label} v{version}，由 {actor or 'system'} 更新",
        status="active",
        extra={"version": f"v{version}"},
    )
    session.add(row)
    await logs.write_log(
        session,
        category="lineage_spec",
        action="standard.published",
        message=f"{label} v{version} 已生效，全文 {len(full_text or '')} 字",
        actor=actor,
        resource_type="data_standard",
        resource_id=row.code,
        detail={"category": category, "version": f"v{version}"},
        commit=False,
    )
    await session.flush()
    return row


async def create_standard(
    session: AsyncSession, payload: StandardCreate, actor: str | None = None
) -> DataStandard:
    category = payload.category or DEFAULT_CATEGORY
    if not payload.code:
        # Only the full text is maintained by users; the code/version are derived.
        return await publish_version(
            session,
            category=category,
            name=payload.name,
            full_text=payload.rule or "",
            description=payload.description,
            actor=actor,
        )
    standard = DataStandard(**payload.model_dump())
    standard.extra = {**(payload.extra or {}), "version": f"v{_version_number(standard)}"}
    session.add(standard)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"data standard '{payload.code}' already exists") from exc
    return standard


async def update_standard(
    session: AsyncSession,
    standard_id: int,
    payload: StandardUpdate,
    actor: str | None = None,
) -> DataStandard:
    standard = await get_standard(session, standard_id)
    changes = payload.model_dump(exclude_unset=True)
    new_text = changes.get("rule")
    if new_text is not None and new_text != (standard.rule or ""):
        # Any full-text change automatically becomes a new version.
        return await publish_version(
            session,
            category=changes.get("category") or standard.category or DEFAULT_CATEGORY,
            name=changes.get("name") or standard.name,
            full_text=new_text,
            description=changes.get("description", standard.description),
            actor=actor,
        )
    for field, value in changes.items():
        setattr(standard, field, value)
    await session.flush()
    return standard


async def delete_standard(session: AsyncSession, standard_id: int) -> None:
    standard = await get_standard(session, standard_id)
    await session.delete(standard)
    await session.flush()
