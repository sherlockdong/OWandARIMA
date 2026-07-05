"""Tests for WHOOP workout normalization."""

from uuid import uuid4

import pytest

from app.schemas.enums import HealthScoreCategory
from app.schemas.providers.whoop import WhoopWorkoutJSON
from app.services.providers.whoop.strategy import WhoopStrategy
from app.services.providers.whoop.workouts import WhoopWorkouts


class TestWhoopWorkoutNormalization:
    @pytest.fixture
    def workouts(self) -> WhoopWorkouts:
        return WhoopStrategy().workouts

    @pytest.fixture
    def raw_workout(self) -> WhoopWorkoutJSON:
        return WhoopWorkoutJSON(
            id="4653c74c-9c9c-4551-bf08-7e261a4cb696",
            user_id=12345,
            created_at="2026-07-05T13:00:00.000Z",
            updated_at="2026-07-05T13:00:00.000Z",
            start="2026-07-05T10:00:00.000Z",
            end="2026-07-05T11:00:00.000Z",
            timezone_offset="-04:00",
            sport_name="running",
            score_state="SCORED",
            score={
                "strain": 11.493,
                "average_heart_rate": 142,
                "max_heart_rate": 181,
                "kilojoule": 1_250.5,
                "percent_recorded": 1.0,
            },
        )

    def test_normalize_workout_preserves_timezone_offset(
        self,
        workouts: WhoopWorkouts,
        raw_workout: WhoopWorkoutJSON,
    ) -> None:
        record, _, _ = workouts._normalize_workout(
            raw_workout,
            uuid4(),
        )

        assert record.zone_offset == "-04:00"

    def test_workout_strain_is_explicitly_classified(
        self,
        workouts: WhoopWorkouts,
        raw_workout: WhoopWorkoutJSON,
    ) -> None:
        _, _, health_score = workouts._normalize_workout(
            raw_workout,
            uuid4(),
        )

        assert health_score is not None
        assert health_score.category == HealthScoreCategory.STRAIN
        assert health_score.qualifier == "workout"
        assert health_score.zone_offset == "-04:00"
        assert float(health_score.value) == pytest.approx(11.493)
