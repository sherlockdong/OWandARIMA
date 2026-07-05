"""Tests for WHOOP 24/7 data normalization."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
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


class TestWhoopCycleNormalization:
    @pytest.fixture
    def data_247(self) -> Whoop247Data:
        return WhoopStrategy().data_247

    @pytest.fixture
    def raw_cycle(self) -> dict:
        return {
            "id": 93845,
            "user_id": 10129,
            "created_at": "2026-07-05T12:00:00.000Z",
            "updated_at": "2026-07-05T12:15:00.000Z",
            "start": "2026-07-05T11:00:00.000Z",
            "end": "2026-07-06T10:30:00.000Z",
            "timezone_offset": "-04:00",
            "score_state": "SCORED",
            "score": {
                "strain": 14.275,
                "kilojoule": 8_288.297,
                "average_heart_rate": 68,
                "max_heart_rate": 141,
            },
        }

    def test_normalize_cycle_creates_daily_strain(
        self,
        data_247: Whoop247Data,
        raw_cycle: dict,
    ) -> None:
        health_score = data_247._normalize_cycle_health_score(
            raw_cycle,
            uuid4(),
        )

        assert health_score is not None
        assert health_score.value == pytest.approx(14.275)
        assert health_score.qualifier == "cycle"
        assert health_score.zone_offset == "-04:00"
        assert health_score.recorded_at == datetime(
            2026,
            7,
            5,
            11,
            tzinfo=timezone.utc,
        )
        assert health_score.components is not None
        assert health_score.components["average_heart_rate"].value == 68
        assert health_score.components["max_heart_rate"].value == 141

    def test_normalize_cycle_skips_unscored_cycle(
        self,
        data_247: Whoop247Data,
        raw_cycle: dict,
    ) -> None:
        raw_cycle["score_state"] = "PENDING"

        assert (
            data_247._normalize_cycle_health_score(
                raw_cycle,
                uuid4(),
            )
            is None
        )

    def test_load_and_save_all_includes_cycle_sync(
        self,
        data_247: Whoop247Data,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            data_247,
            "load_and_save_sleep",
            lambda *_args, **_kwargs: 1,
        )
        monkeypatch.setattr(
            data_247,
            "load_and_save_cycles",
            lambda *_args, **_kwargs: 2,
        )
        monkeypatch.setattr(
            data_247,
            "load_and_save_recovery",
            lambda *_args, **_kwargs: 3,
        )
        monkeypatch.setattr(
            data_247,
            "load_and_save_body_measurement",
            lambda *_args, **_kwargs: 4,
        )

        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 6, tzinfo=timezone.utc)

        result = data_247.load_and_save_all(
            object(),
            uuid4(),
            start,
            end,
        )

        assert result == {
            "sleep_sessions_synced": 1,
            "cycle_samples_synced": 2,
            "recovery_samples_synced": 3,
            "activity_samples_synced": 0,
            "body_measurement_samples_synced": 4,
        }


class TestWhoopRecoveryNormalization:
    @pytest.fixture
    def data_247(self) -> Whoop247Data:
        return WhoopStrategy().data_247

    @pytest.fixture
    def raw_recovery(self) -> dict:
        return {
            "cycle_id": 93845,
            "sleep_id": "27f70d06-4f31-4d89-ae8d-62d7bed0876a",
            "user_id": 10129,
            "created_at": "2026-07-05T11:15:00.000Z",
            "updated_at": "2026-07-05T11:20:00.000Z",
            "score_state": "SCORED",
            "score": {
                "recovery_score": 82,
                "resting_heart_rate": 49,
                "hrv_rmssd_milli": 61.25,
                "spo2_percentage": 97.2,
                "skin_temp_celsius": 33.8,
            },
        }

    def test_normalize_recovery_preserves_sleep_context(
        self,
        data_247: Whoop247Data,
        raw_recovery: dict,
    ) -> None:
        user_id = uuid4()
        sleep_record_id = uuid4()
        data_source_id = uuid4()

        normalized, health_score = data_247.normalize_recovery(
            raw_recovery,
            user_id,
            zone_offset="-04:00",
            sleep_record_id=sleep_record_id,
            data_source_id=data_source_id,
        )

        assert normalized["zone_offset"] == "-04:00"
        assert normalized["sleep_record_id"] == sleep_record_id
        assert health_score is not None
        assert health_score.zone_offset == "-04:00"
        assert health_score.sleep_record_id == sleep_record_id
        assert health_score.data_source_id == data_source_id
        assert health_score.components is not None
        assert health_score.components["hrv_rmssd_milli"].value == pytest.approx(61.25)

    def test_get_recovery_record_uses_cycle_endpoint(
        self,
        data_247: Whoop247Data,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

        def fake_request(
            _db: object,
            _user_id: object,
            endpoint: str,
            **_kwargs: object,
        ) -> dict:
            captured["endpoint"] = endpoint
            return {"cycle_id": 93845}

        monkeypatch.setattr(
            data_247,
            "_make_api_request",
            fake_request,
        )
        monkeypatch.setattr(
            "app.services.providers.whoop.data_247.store_raw_payload",
            lambda **_kwargs: None,
        )

        result = data_247.get_recovery_record(
            object(),
            uuid4(),
            "93845",
        )

        assert result == {"cycle_id": 93845}
        assert captured["endpoint"] == "/v2/cycle/93845/recovery"

    def test_save_recovery_propagates_offsets_to_all_samples(
        self,
        data_247: Whoop247Data,
        raw_recovery: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        normalized, _ = data_247.normalize_recovery(
            raw_recovery,
            uuid4(),
            zone_offset="-04:00",
            sleep_record_id=uuid4(),
            data_source_id=uuid4(),
        )
        captured: list = []

        def fake_bulk_create(
            _db: object,
            samples: list,
        ) -> int:
            captured.extend(samples)
            return len(samples)

        monkeypatch.setattr(
            "app.services.providers.whoop.data_247.timeseries_service.bulk_create_samples",
            fake_bulk_create,
        )

        db = MagicMock()
        count = data_247.save_recovery_data(
            db,
            normalized["user_id"],
            normalized,
        )

        assert count == 4
        assert len(captured) == 4
        assert all(sample.zone_offset == "-04:00" for sample in captured)
        assert all(sample.external_id is not None for sample in captured)
        db.commit.assert_called_once()
