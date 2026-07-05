import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AthleteDailyFeatureBase(BaseModel):
    local_date: datetime.date
    timezone: str

    recovery_score: float | None = None
    strain_score: float | None = None
    hrv_rmssd_ms: float | None = None
    resting_heart_rate_bpm: float | None = None
    sleep_duration_seconds: int | None = None
    sleep_efficiency_percentage: float | None = None

    hrv_baseline_7d: float | None = None
    hrv_baseline_28d: float | None = None
    resting_hr_baseline_7d: float | None = None
    resting_hr_baseline_28d: float | None = None
    sleep_duration_baseline_7d: int | None = None
    strain_acute_7d: float | None = None
    strain_chronic_28d: float | None = None

    source_coverage: dict | None = None
    feature_version: int = 1


class AthleteDailyFeatureCreate(AthleteDailyFeatureBase):
    pass


class AthleteDailyFeatureResponse(AthleteDailyFeatureBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    computed_at: datetime.datetime | None
    created_at: datetime.datetime
