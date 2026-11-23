"""Lineage insight pipeline and agent endpoint registry.

Five new tables.  The existing 34 tables are intentionally left untouched:
lineage specs live in ``DataStandard`` and the incremental cursor lives in
``DataSource.config`` (both are JSON/Text columns already present).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column

from dgov.models.base import Base, IdMixin, TimestampMixin, utcnow


class AgentEndpoint(IdMixin, TimestampMixin, Base):
    """A real agent API endpoint registered against a ``gov_agent`` catalogue entry."""

    __tablename__ = "agent_endpoint"
    __table_args__ = (UniqueConstraint("agent_code", name="uq_agent_endpoint_code"),)

    agent_code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    auth_type: Mapped[str] = mapped_column(String(16), default="none")
    auth_token_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str | None] = mapped_column(String(128), default=None)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=20_000)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


class LineageFlow(IdMixin, TimestampMixin, Base):
    """Versioned, configurable definition of the four-stage insight pipeline."""

    __tablename__ = "lineage_flow"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_lineage_flow_code_version"),
        Index("ix_lineage_flow_due", "enabled", "next_run_at"),
    )

    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_type: Mapped[str] = mapped_column(String(16), default="event")
    interval_unit: Mapped[str] = mapped_column(String(8), default="hour")
    interval_value: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    alert_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)


class LineageFlowRun(IdMixin, TimestampMixin, Base):
    """One execution of a flow.  ``input_hash`` provides idempotent caching."""

    __tablename__ = "lineage_flow_run"
    __table_args__ = (
        Index("ix_lineage_flow_run_flow_started", "flow_id", "started_at"),
        Index("ix_lineage_flow_run_hash", "flow_id", "input_hash"),
    )

    flow_id: Mapped[int] = mapped_column(
        ForeignKey("lineage_flow.id", ondelete="CASCADE"), index=True
    )
    flow_version: Mapped[int] = mapped_column(Integer, default=1)
    flow_code: Mapped[str] = mapped_column(String(64), default="")
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual")
    actor: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    spec_version: Mapped[str] = mapped_column(String(32), default="")
    ingest_run_id: Mapped[int | None] = mapped_column(Integer, default=None)
    cursor_from: Mapped[str | None] = mapped_column(String(64), default=None)
    cursor_to: Mapped[str | None] = mapped_column(String(64), default=None)
    input_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stages_result: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)


class LineageIssue(IdMixin, TimestampMixin, Base):
    """A problem found by an agent or by the deterministic rule engine."""

    __tablename__ = "lineage_issue"
    __table_args__ = (
        Index("ix_lineage_issue_run_status", "run_id", "status"),
        Index("ix_lineage_issue_dedupe", "dedupe_key"),
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("lineage_flow_run.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="rule")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    affected: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    suggestion: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    assignee: Mapped[str | None] = mapped_column(String(64), default=None)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    review_note: Mapped[str | None] = mapped_column(Text, default=None)
    dedupe_key: Mapped[str] = mapped_column(String(128), default="", index=True)


class LineageAlert(IdMixin, TimestampMixin, Base):
    """In-app alert record.  Dedupe key suppresses repeated alerts across runs."""

    __tablename__ = "lineage_alert"
    __table_args__ = (Index("ix_lineage_alert_run", "run_id", "status"),)

    run_id: Mapped[int] = mapped_column(
        ForeignKey("lineage_flow_run.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("lineage_issue.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16), default="in_app")
    level: Mapped[str] = mapped_column(String(16), default="warning")
    status: Mapped[str] = mapped_column(String(16), default="sent", index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    dedupe_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
