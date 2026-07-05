from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from sqlalchemy import CheckConstraint
from app.database import BaseDbModel
from app.mappings import FKUser, PrimaryKey, str_32, str_255
from app.mappings import FKPerformanceEvent


class AthleteCheckin(BaseDbModel):
    __tablename__ = "athlete_checkin"

    __table_args__ = (
        CheckConstraint(
            "checkin_type IN ('daily', 'pre_event', 'post_event')",
            name="ck_athlete_checkin_type",
        ),
        CheckConstraint(
            "stress_rating BETWEEN 1 AND 10",
            name="ck_athlete_checkin_stress",
        ),
        CheckConstraint(
            "energy_rating BETWEEN 1 AND 10",
            name="ck_athlete_checkin_energy",
        ),
        CheckConstraint(
            "focus_rating BETWEEN 1 AND 10",
            name="ck_athlete_checkin_focus",
        ),
        CheckConstraint(
            "confidence_rating BETWEEN 1 AND 10",
            name="ck_athlete_checkin_confidence",
        ),
        CheckConstraint(
            "soreness_rating BETWEEN 1 AND 10",
            name="ck_athlete_checkin_soreness",
        ),
    )

    id: Mapped[PrimaryKey[UUID]]
    user_id: Mapped[FKUser]

    checkin_type: Mapped[str_32]
    event_type: Mapped[str_32]
    event_name: Mapped[str_255 | None]

    stress_rating: Mapped[int]
    energy_rating: Mapped[int]
    focus_rating: Mapped[int]
    confidence_rating: Mapped[int]
    soreness_rating: Mapped[int]

    performance_event_id: Mapped[FKPerformanceEvent | None] = mapped_column(default=None)
