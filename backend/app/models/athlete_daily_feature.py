import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BaseDbModel


class AthleteDailyFeature(BaseDbModel):
    __tablename__ = "athlete_daily_feature"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    local_date: Mapped[datetime.date]
    timezone: Mapped[str] = mapped_column(String(64))

    recovery_score: Mapped[float | None]
    strain_score: Mapped[float | None]
    hrv_rmssd_ms: Mapped[float | None]
    resting_heart_rate_bpm: Mapped[float | None]
    sleep_duration_seconds: Mapped[int | None]
    sleep_efficiency_percentage: Mapped[float | None]

    hrv_baseline_7d: Mapped[float | None]
    hrv_baseline_28d: Mapped[float | None]
    resting_hr_baseline_7d: Mapped[float | None]
    resting_hr_baseline_28d: Mapped[float | None]
    sleep_duration_baseline_7d: Mapped[int | None]
    strain_acute_7d: Mapped[float | None]
    strain_chronic_28d: Mapped[float | None]

    source_coverage: Mapped[dict | None] = mapped_column(JSONB)
    feature_version: Mapped[int] = mapped_column(default=1)
    computed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "local_date", name="uq_athlete_daily_feature_user_date"),)
