from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from dgov.models.base import Base, IdMixin, TimestampMixin


class LineageEdge(IdMixin, TimestampMixin, Base):
    """Directed lineage edge between any two governance assets.

    ``source_type``/``target_type`` are polymorphic (dataset, column, semantic_asset, job, ...),
    so no foreign keys are declared here on purpose.
    """

    __tablename__ = "lineage_edge"
    __table_args__ = (
        Index("ix_lineage_source", "source_type", "source_id"),
        Index("ix_lineage_target", "target_type", "target_id"),
    )

    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int] = mapped_column(BigInteger)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[int] = mapped_column(BigInteger)
    relation_type: Mapped[str] = mapped_column(String(32), default="derived_from")
    job_name: Mapped[str | None] = mapped_column(String(255), default=None)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
