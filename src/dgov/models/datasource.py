from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dgov.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from dgov.models.catalog import Dataset


class DataSource(IdMixin, TimestampMixin, Base):
    """A physical system that datasets are extracted from (MySQL, Hive, ClickHouse, ...)."""

    __tablename__ = "data_source"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    host: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int] = mapped_column(Integer, default=3306)
    database: Mapped[str | None] = mapped_column(String(128), default=None)
    username: Mapped[str | None] = mapped_column(String(128), default=None)
    # TODO: encrypt at rest with a KMS/master key before production use.
    password: Mapped[str | None] = mapped_column(String(512), default=None)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # API sources: {"mode": "test", "test_base_url": ..., "production_base_url": ...,
    # "endpoints": {...}, "auth_type": "none|api_key|bearer", "auth_token": ...}
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    datasets: Mapped[list[Dataset]] = relationship(
        back_populates="data_source", cascade="all, delete-orphan", passive_deletes=True
    )
