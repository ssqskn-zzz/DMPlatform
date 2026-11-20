from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from dgov.models.base import Base, IdMixin, TimestampMixin


class QualityRule(IdMixin, TimestampMixin, Base):
    """A data quality rule bound to a dataset (and optionally a single column)."""

    __tablename__ = "quality_rule"

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("dataset.id", ondelete="CASCADE"), index=True
    )
    column_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_column.id", ondelete="SET NULL"), default=None, index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    rule_type: Mapped[str] = mapped_column(String(32), index=True)
    expression: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    description: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QualityResult(IdMixin, Base):
    """Execution result of a quality rule (written by the scheduler/executor)."""

    __tablename__ = "quality_result"

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("quality_rule.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float | None] = mapped_column(Float, default=None)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    executed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
