from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dgov.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from dgov.models.datasource import DataSource


class Dataset(IdMixin, TimestampMixin, Base):
    """A table/view registered in the asset catalog."""

    __tablename__ = "dataset"
    __table_args__ = (
        UniqueConstraint("data_source_id", "schema_name", "name", name="uk_dataset_physical"),
    )

    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.id", ondelete="CASCADE"), index=True
    )
    schema_name: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    dataset_type: Mapped[str] = mapped_column(String(16), default="table")
    description: Mapped[str | None] = mapped_column(Text, default=None)
    owner: Mapped[str | None] = mapped_column(String(64), default=None)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    row_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")

    data_source: Mapped[DataSource] = relationship(back_populates="datasets")
    columns: Mapped[list[DatasetColumn]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )


class DatasetColumn(IdMixin, TimestampMixin, Base):
    """Column-level metadata, optionally bound to a data standard."""

    __tablename__ = "dataset_column"
    __table_args__ = (UniqueConstraint("dataset_id", "name", name="uk_column_of_dataset"),)

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("dataset.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    data_type: Mapped[str] = mapped_column(String(64), default="string")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    standard_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_standard.id", ondelete="SET NULL"), default=None, index=True
    )

    dataset: Mapped[Dataset] = relationship(back_populates="columns")


class DataStandard(IdMixin, TimestampMixin, Base):
    """Reusable naming/typing/validation rule applied to columns."""

    __tablename__ = "data_standard"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    data_type: Mapped[str] = mapped_column(String(64), default="string")
    rule: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="active")
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
