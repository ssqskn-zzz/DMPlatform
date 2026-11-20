from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dgov.models.base import Base, IdMixin, TimestampMixin

agent_service = Table(
    "gov_agent_service",
    Base.metadata,
    Column("agent_id", ForeignKey("gov_agent.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("gov_service.id", ondelete="CASCADE"), primary_key=True),
)


class GovernanceService(IdMixin, TimestampMixin, Base):
    """Agent-ready service contract exposed through the unified platform interface."""

    __tablename__ = "gov_service"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(64))
    input_desc: Mapped[str] = mapped_column(Text)
    output_desc: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    # Four permission dimensions must stay separated: system / retrieval / training / output.
    allow_system: Mapped[str] = mapped_column(String(8), default="✓")
    allow_retrieval: Mapped[str] = mapped_column(String(8), default="✓")
    allow_training: Mapped[str] = mapped_column(String(8), default="✕")
    allow_output: Mapped[str] = mapped_column(String(8), default="✕")
    description: Mapped[str | None] = mapped_column(Text, default=None)


class GovernanceAgent(IdMixin, TimestampMixin, Base):
    """Registered governance or business agent with a hard decision boundary."""

    __tablename__ = "gov_agent"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="治理")
    owner: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="on")
    boundary: Mapped[str] = mapped_column(String(16), default="复核")
    description: Mapped[str] = mapped_column(Text)

    services: Mapped[list[GovernanceService]] = relationship(
        secondary=agent_service, lazy="selectin", order_by=GovernanceService.id
    )


class SemanticTerm(IdMixin, TimestampMixin, Base):
    """Business term whose definition is owned by exactly one accountable person."""

    __tablename__ = "gov_semantic_term"
    __table_args__ = (UniqueConstraint("name", name="uk_semantic_term_name"),)

    name: Mapped[str] = mapped_column(String(128))
    domain: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="active")
    definition: Mapped[str | None] = mapped_column(Text, default=None)
    effective_date: Mapped[str | None] = mapped_column(String(16), default=None)


class WorkOrder(IdMixin, TimestampMixin, Base):
    """A governance task: who must do what, by when, and from which rule it came."""

    __tablename__ = "gov_work_order"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[str | None] = mapped_column(String(255), default=None)
    domain: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str] = mapped_column(String(128))
    action_label: Mapped[str] = mapped_column(String(16), default="认责")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    kind: Mapped[str] = mapped_column(String(16), default="work_order")
    description: Mapped[str | None] = mapped_column(Text, default=None)
    evidence: Mapped[str | None] = mapped_column(Text, default=None)
    impact: Mapped[str | None] = mapped_column(Text, default=None)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class ChangeRequest(IdMixin, TimestampMixin, Base):
    """Semantic definition change that needs the owner's approval before it is published."""

    __tablename__ = "gov_change_request"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[str | None] = mapped_column(String(255), default=None)
    term_name: Mapped[str] = mapped_column(String(128))
    owner: Mapped[str] = mapped_column(String(64))
    from_version: Mapped[str] = mapped_column(String(16))
    to_version: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    affected_agents: Mapped[list[str]] = mapped_column(JSON, default=list)
    affected_objects: Mapped[list[str]] = mapped_column(JSON, default=list)


class EvaluationRun(IdMixin, TimestampMixin, Base):
    """Outcome of one agent task, scored on AI effect rather than integration volume."""

    __tablename__ = "gov_evaluation_run"

    task_code: Mapped[str] = mapped_column(String(32), index=True)
    agent_code: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    feedback: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)


class RuntimeLog(IdMixin, Base):
    """Runtime policy decision trail; permissions are enforced here, not in prompts."""

    __tablename__ = "gov_runtime_log"

    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gov_evaluation_run.id", ondelete="SET NULL"), default=None, index=True
    )
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
