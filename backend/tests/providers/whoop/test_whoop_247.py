"""Tests for WHOOP 24/7 data normalization."""

from uuid import uuid4

import pytest

from app.services.providers.whoop.data_247 import Whoop247Data
from app.services.providers.whoop.strategy import WhoopStrategy


class TestWhoopSleepNormalization:
    @pytest.fixture
    def data_247(self) -> Whoop247Data:
        return WhoopStrategy().data_247

    @pytest.fixture
    def raw_sleep(self) -> dict:
        return {
            "id": "27f70d06-4f31-4d89-ae8d-62d7bed0876a",
            "user_id": 12345,
            "created_at": "2026-07-05T12:00:00.000Z",
            "updated_at": "2026-07-05T12:00:00.000Z",
            "start": "2026-07-05T03:00:00.000Z",
            "end": "2026-07-05T11:00:00.000Z",
            "timezone_offset": "-04:00",
            "nap": False,
            "score_state": "SCORED",
            "cycle_id": 98765,
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_awake_time_milli": 1_800_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 5_400_000,
                    "total_rem_sleep_time_milli": 7_200_000,
                },
                "sleep_performance_percentage": 88,
                "sleep_consistency_percentage": 82,
                "sleep_efficiency_percentage": 93.75,
                "respiratory_rate": 14.5,
            },
        }

    def test_normalize_sleep_preserves_timezone_offset(
        self,
        data_247: Whoop247Data,
        raw_sleep: dict,
    ) -> None:
        normalized, health_score = data_247.normalize_sleep(
            raw_sleep,
            uuid4(),
        )

        assert normalized["zone_offset"] == "-04:00"
        assert health_score is not None
        assert health_score.zone_offset == "-04:00"

    def test_normalize_sleep_preserves_duration_and_efficiency(
        self,
        data_247: Whoop247Data,
        raw_sleep: dict,
    ) -> None:
        normalized, _ = data_247.normalize_sleep(
            raw_sleep,
            uuid4(),
        )

        assert normalized["duration_seconds"] == 28_800
        assert normalized["efficiency_percent"] == pytest.approx(93.75)
        assert normalized["is_nap"] is False
