from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dgov.models.base import Base, IdMixin, TimestampMixin, utcnow


class CollectionJob(IdMixin, TimestampMixin, Base):
    """Scheduled metadata collection configuration.

    ``assets`` holds a subset of ``table_info``, ``column_metadata`` and
    ``table_lineage``.  The scheduler enforces a minimum interval of one minute.
    """

    __tablename__ = "collection_job"
    __table_args__ = (Index("ix_collection_job_due", "enabled", "next_run_at"),)

    datasource_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    assets: Mapped[list[str]] = mapped_column(JSON, default=list)
    interval_unit: Mapped[str] = mapped_column(String(8), default="hour")
    interval_value: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_status: Mapped[str] = mapped_column(String(16), default="never")
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[str | None] = mapped_column(String(32), default=None)


class IngestRun(IdMixin, Base):
    """One execution (manual or scheduled) of a collection job."""

    __tablename__ = "ingest_run"
    __table_args__ = (Index("ix_ingest_run_ds_started", "datasource_id", "started_at"),)

    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_job.id", ondelete="SET NULL"), index=True, default=None
    )
    datasource_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.id", ondelete="CASCADE"), index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    assets: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    tables_new: Mapped[int] = mapped_column(Integer, default=0)
    tables_changed: Mapped[int] = mapped_column(Integer, default=0)
    tables_removed: Mapped[int] = mapped_column(Integer, default=0)
    columns_new: Mapped[int] = mapped_column(Integer, default=0)
    columns_changed: Mapped[int] = mapped_column(Integer, default=0)
    lineage_new: Mapped[int] = mapped_column(Integer, default=0)
    lineage_removed: Mapped[int] = mapped_column(Integer, default=0)
    column_lineage_new: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)


class MetadataTable(IdMixin, TimestampMixin, Base):
    """A table asset harvested from an external metadata API."""

    __tablename__ = "meta_table"
    __table_args__ = (
        UniqueConstraint("datasource_id", "external_id", name="uq_meta_table_source_external"),
        Index("ix_meta_table_layer_domain", "layer", "domain"),
        Index("ix_meta_table_name", "name"),
    )

    datasource_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(128))
    db_name: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    name: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    table_type: Mapped[str | None] = mapped_column(String(64), default=None)
    store_type: Mapped[str | None] = mapped_column(String(32), default=None)
    tenant: Mapped[str | None] = mapped_column(String(64), default=None)
    layer: Mapped[str] = mapped_column(String(16), default="OTHER", index=True)
    domain: Mapped[str] = mapped_column(String(32), default="通用", index=True)
    grain: Mapped[str | None] = mapped_column(String(16), default=None)
    life_days: Mapped[int | None] = mapped_column(Integer, default=None)
    record_count: Mapped[int | None] = mapped_column(BigInteger, default=None)
    size_bytes: Mapped[float | None] = mapped_column(Float, default=None)
    file_count: Mapped[int | None] = mapped_column(Integer, default=None)
    partition_newest: Mapped[str | None] = mapped_column(String(64), default=None)
    last_ddl_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_dml_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    table_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    quality_score: Mapped[float | None] = mapped_column(Float, default=None)

    columns: Mapped[list[MetadataColumn]] = relationship(
        back_populates="table", cascade="all, delete-orphan", passive_deletes=True
    )


class MetadataColumn(IdMixin, TimestampMixin, Base):
    __tablename__ = "meta_column"
    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_meta_column_table_name"),
        Index("ix_meta_column_sensitive", "sensitive_level"),
    )

    table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    data_type: Mapped[str | None] = mapped_column(String(128), default=None)
    type_family: Mapped[str] = mapped_column(String(16), default="string")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    sensitive_level: Mapped[str] = mapped_column(String(4), default="L1")
    data_category: Mapped[str] = mapped_column(String(32), default="其他")
    column_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    table: Mapped[MetadataTable] = relationship(back_populates="columns")


class MetadataLineage(IdMixin, Base):
    """Table-level lineage keyed by the upstream ``table_id`` values."""

    __tablename__ = "meta_lineage"
    __table_args__ = (
        UniqueConstraint(
            "datasource_id",
            "source_table_id",
            "target_table_id",
            "lineage_type",
            name="uq_meta_lineage_edge",
        ),
        Index("ix_meta_lineage_target", "target_table_id"),
    )

    datasource_id: Mapped[int] = mapped_column(
        ForeignKey("data_source.id", ondelete="CASCADE"), index=True
    )
    source_table_id: Mapped[str] = mapped_column(String(128), index=True)
    target_table_id: Mapped[str] = mapped_column(String(128), index=True)
    source_table_name: Mapped[str | None] = mapped_column(String(255), default=None)
    target_table_name: Mapped[str | None] = mapped_column(String(255), default=None)
    source_schema: Mapped[str | None] = mapped_column(String(128), default=None)
    target_schema: Mapped[str | None] = mapped_column(String(128), default=None)
    lineage_type: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ColumnLineage(IdMixin, Base):
    """Derived field-level lineage.

    The fixture only ships table-level lineage, so column edges are inferred by
    matching equal column names across the two tables.  The ``method`` and
    ``confidence`` fields leave room for parser-based lineage later.
    """

    __tablename__ = "column_lineage"
    __table_args__ = (
        UniqueConstraint(
            "source_table_id",
            "source_column",
            "target_table_id",
            "target_column",
            name="uq_column_lineage_edge",
        ),
    )

    lineage_id: Mapped[int | None] = mapped_column(
        ForeignKey("meta_lineage.id", ondelete="CASCADE"), index=True, default=None
    )
    source_table_id: Mapped[str] = mapped_column(String(128), index=True)
    target_table_id: Mapped[str] = mapped_column(String(128), index=True)
    source_column: Mapped[str] = mapped_column(String(128))
    target_column: Mapped[str] = mapped_column(String(128))
    method: Mapped[str] = mapped_column(String(32), default="same_name")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)


class OperationLog(IdMixin, Base):
    """Append-only operational log with retention-based cleanup."""

    __tablename__ = "operation_log"
    __table_args__ = (
        Index("ix_operation_log_category_created", "category", "created_at"),
        Index("ix_operation_log_resource", "resource_type", "resource_id"),
    )

    category: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(8), default="info", index=True)
    actor: Mapped[str | None] = mapped_column(String(64), default=None)
    resource_type: Mapped[str | None] = mapped_column(String(32), default=None)
    resource_id: Mapped[str | None] = mapped_column(String(128), default=None)
    message: Mapped[str] = mapped_column(String(512))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class WarehouseModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "warehouse_model"

    table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id", ondelete="CASCADE"), unique=True, index=True
    )
    model_name: Mapped[str] = mapped_column(String(255))
    layer: Mapped[str] = mapped_column(String(16), index=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    grain: Mapped[str | None] = mapped_column(String(16), default=None)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    naming_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    type_compliant: Mapped[bool] = mapped_column(Boolean, default=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="registered")
    review_note: Mapped[str | None] = mapped_column(Text, default=None)


class OntologyEntity(IdMixin, TimestampMixin, Base):
    __tablename__ = "ontology_entity"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="业务对象")
    description: Mapped[str | None] = mapped_column(Text, default=None)
    table_count: Mapped[int] = mapped_column(Integer, default=0)


class OntologyEntityTable(IdMixin, Base):
    __tablename__ = "ontology_entity_table"
    __table_args__ = (
        UniqueConstraint("entity_id", "table_id", name="uq_ontology_entity_table"),
    )

    entity_id: Mapped[int] = mapped_column(
        ForeignKey("ontology_entity.id", ondelete="CASCADE"), index=True
    )
    table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id", ondelete="CASCADE"), index=True
    )
    match_field: Mapped[str] = mapped_column(String(32), default="table_name")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)


class OntologyRelation(IdMixin, TimestampMixin, Base):
    __tablename__ = "ontology_relation"
    __table_args__ = (
        UniqueConstraint(
            "source_entity_id", "target_entity_id", "relation_type", name="uq_ontology_relation"
        ),
    )

    source_entity_id: Mapped[int] = mapped_column(
        ForeignKey("ontology_entity.id", ondelete="CASCADE"), index=True
    )
    target_entity_id: Mapped[int] = mapped_column(
        ForeignKey("ontology_entity.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), default="data_flow")
    weight: Mapped[int] = mapped_column(Integer, default=1)


class QualityCheckRule(IdMixin, TimestampMixin, Base):
    __tablename__ = "quality_check_rule"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    dimension: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(16), default="table")
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    description: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QualityCheckResult(IdMixin, Base):
    __tablename__ = "quality_check_result"
    __table_args__ = (Index("ix_quality_check_run", "run_id"),)

    run_id: Mapped[str] = mapped_column(String(36), index=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("quality_check_rule.id", ondelete="CASCADE"), index=True
    )
    table_id: Mapped[int | None] = mapped_column(
        ForeignKey("meta_table.id", ondelete="CASCADE"), index=True, default=None
    )
    column_id: Mapped[int | None] = mapped_column(
        ForeignKey("meta_column.id", ondelete="CASCADE"), default=None
    )
    status: Mapped[str] = mapped_column(String(16), default="passed", index=True)
    score: Mapped[float] = mapped_column(Float, default=100.0)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
