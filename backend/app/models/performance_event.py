from uuid import UUID
from datetime import datetime
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from app.database import BaseDbModel
from app.mappings import (
    FKUser,
    FKPerformanceEvent,
    FKMetricDefinition,
    PrimaryKey,
    str_32,
    str_64,
    str_100,
    str_255,
    numeric_10_3,
)


class PerformanceEvent(BaseDbModel):
    __tablename__ = "performance_event"
    __table_args__ = (
        Index("ix_perf_event_user_time", "user_id", "started_at"),
        Index("ix_perf_event_user_sport_time", "user_id", "sport", "started_at"),
    )

    id: Mapped[PrimaryKey[UUID]]
    user_id: Mapped[FKUser]
    sport: Mapped[str_64]
    event_type: Mapped[str_64]
    event_name: Mapped[str_100 | None]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    timezone: Mapped[str_32 | None]
    notes: Mapped[str_255 | None]

    # Relationships
    metric_values: Mapped[list["PerformanceMetricValue"]] = relationship(cascade="all, delete-orphan")


class PerformanceMetricDefinition(BaseDbModel):
    __tablename__ = "performance_metric_definition"
    __table_args__ = (UniqueConstraint("sport", "metric_key", name="uq_sport_metric_key"),)

    id: Mapped[PrimaryKey[UUID]]
    sport: Mapped[str_64]
    metric_key: Mapped[str_64]
    display_name: Mapped[str_100]
    value_type: Mapped[str_32]  # e.g., 'numeric', 'text', 'boolean'
    unit: Mapped[str_32 | None]


class PerformanceMetricValue(BaseDbModel):
    __tablename__ = "performance_metric_value"
    __table_args__ = (UniqueConstraint("performance_event_id", "metric_definition_id", name="uq_event_metric"),)

    id: Mapped[PrimaryKey[UUID]]
    performance_event_id: Mapped[FKPerformanceEvent]
    metric_definition_id: Mapped[FKMetricDefinition]

    numeric_value: Mapped[numeric_10_3 | None]
    text_value: Mapped[str_255 | None]
    boolean_value: Mapped[bool | None]
