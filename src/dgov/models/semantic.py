from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from dgov.models.base import Base, IdMixin, TimestampMixin


class SemanticAsset(IdMixin, TimestampMixin, Base):
    """A business-facing semantic definition (entity / metric / dimension / measure).

    The definition itself lives in MySQL; its embedding is stored in Elasticsearch and is
    written through external embedding pipelines via the embedding API.
    """

    __tablename__ = "semantic_asset"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset.id", ondelete="SET NULL"), default=None, index=True
    )
    definition: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    synonyms: Mapped[list[str]] = mapped_column(JSON, default=list)
    owner: Mapped[str | None] = mapped_column(String(64), default=None)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)

    embedding_model: Mapped[str | None] = mapped_column(String(128), default=None)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
