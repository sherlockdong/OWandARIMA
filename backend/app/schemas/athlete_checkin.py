from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AthleteCheckinType(str, Enum):
    DAILY = "daily"
    PRE_EVENT = "pre_event"
    POST_EVENT = "post_event"


class AthleteCheckinCreate(BaseModel):
    checkin_type: AthleteCheckinType
    event_type: str = Field(min_length=1, max_length=32)
    event_name: str | None = Field(default=None, max_length=255)

    stress_rating: int = Field(ge=1, le=10)
    energy_rating: int = Field(ge=1, le=10)
    focus_rating: int = Field(ge=1, le=10)
    confidence_rating: int = Field(ge=1, le=10)
    soreness_rating: int = Field(ge=1, le=10)


class AthleteCheckinRead(AthleteCheckinCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime
