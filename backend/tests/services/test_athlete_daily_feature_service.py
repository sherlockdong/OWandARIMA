"""Integration tests for athlete daily feature aggregation."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.athlete_daily_feature import AthleteDailyFeature
from app.schemas.enums import HealthScoreCategory, ProviderName
from app.services.athlete_daily_feature_service import (
    AthleteDailyFeatureService,
    athlete_daily_feature_service,
)
from tests.factories import (
    DataPointSeriesFactory,
    DataSourceFactory,
    EventRecordFactory,
    HealthScoreFactory,
    SeriesTypeDefinitionFactory,
    SleepDetailsFactory,
    UserFactory,
)


def _add_sleep(
    *,
    data_source: object,
    start_at: datetime,
    end_at: datetime,
    duration_minutes: int,
    efficiency: float,
    is_nap: bool,
    zone_offset: str | None = "-04:00",
) -> None:
    record = EventRecordFactory(
        data_source=data_source,
        category="sleep",
        type="sleep_session",
        source_name="Whoop",
        start_datetime=start_at,
        end_datetime=end_at,
        duration_seconds=int((end_at - start_at).total_seconds()),
        zone_offset=zone_offset,
    )
    SleepDetailsFactory(
        event_record=record,
        sleep_total_duration_minutes=duration_minutes,
        sleep_time_in_bed_minutes=int((end_at - start_at).total_seconds() / 60),
        sleep_efficiency_score=Decimal(str(efficiency)),
        is_nap=is_nap,
    )


def test_rebuild_uses_local_date_and_cycle_strain(db: Session) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )

    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.STRAIN,
        qualifier="cycle",
        value=Decimal("12.5"),
        recorded_at=datetime(
            2026,
            7,
            6,
            1,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )
    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.STRAIN,
        qualifier="workout",
        value=Decimal("19.9"),
        recorded_at=datetime(
            2026,
            7,
            6,
            1,
            5,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )
    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.RECOVERY,
        qualifier=None,
        value=Decimal("78"),
        recorded_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )

    DataPointSeriesFactory(
        data_source=data_source,
        series_type=(SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()),
        value=Decimal("61"),
        recorded_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )
    DataPointSeriesFactory(
        data_source=data_source,
        series_type=(SeriesTypeDefinitionFactory.get_or_create_resting_heart_rate()),
        value=Decimal("49"),
        recorded_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )

    _add_sleep(
        data_source=data_source,
        start_at=datetime(
            2026,
            7,
            5,
            3,
            tzinfo=timezone.utc,
        ),
        end_at=datetime(
            2026,
            7,
            5,
            11,
            tzinfo=timezone.utc,
        ),
        duration_minutes=450,
        efficiency=91.5,
        is_nap=False,
    )

    rows = athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 5),
        date(2026, 7, 5),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.local_date == date(2026, 7, 5)
    assert row.timezone == "-04:00"
    assert row.recovery_score == 78
    assert row.strain_score == 12.5
    assert row.hrv_rmssd_ms == 61
    assert row.resting_heart_rate_bpm == 49
    assert row.sleep_duration_seconds == 27_000
    assert row.sleep_efficiency_percentage == 91.5
    assert row.source_coverage["metrics"]["cycle_strain"]["records"] == 1


def test_rebuild_selects_longest_main_sleep_and_tracks_naps(
    db: Session,
) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )

    _add_sleep(
        data_source=data_source,
        start_at=datetime(
            2026,
            7,
            5,
            3,
            tzinfo=timezone.utc,
        ),
        end_at=datetime(
            2026,
            7,
            5,
            11,
            tzinfo=timezone.utc,
        ),
        duration_minutes=420,
        efficiency=85,
        is_nap=False,
    )
    _add_sleep(
        data_source=data_source,
        start_at=datetime(
            2026,
            7,
            5,
            2,
            tzinfo=timezone.utc,
        ),
        end_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        duration_minutes=480,
        efficiency=92,
        is_nap=False,
    )
    _add_sleep(
        data_source=data_source,
        start_at=datetime(
            2026,
            7,
            5,
            17,
            tzinfo=timezone.utc,
        ),
        end_at=datetime(
            2026,
            7,
            5,
            18,
            tzinfo=timezone.utc,
        ),
        duration_minutes=30,
        efficiency=88,
        is_nap=True,
    )

    row = athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 5),
        date(2026, 7, 5),
    )[0]

    assert row.sleep_duration_seconds == 28_800
    assert row.sleep_efficiency_percentage == 92

    sleep_coverage = row.source_coverage["metrics"]["sleep"]
    assert sleep_coverage["main_records"] == 2
    assert sleep_coverage["main_duplicates"] == 1
    assert sleep_coverage["nap_records"] == 1
    assert sleep_coverage["nap_duration_seconds"] == 1_800


def test_baselines_use_prior_days_and_exclude_current_day(
    db: Session,
) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )

    first_day = date(2026, 7, 1)

    for offset in range(8):
        day = first_day + timedelta(days=offset)
        strain_value = 100 if offset == 7 else offset + 1

        HealthScoreFactory(
            data_source=data_source,
            user_id=user.id,
            provider=ProviderName.WHOOP,
            category=HealthScoreCategory.STRAIN,
            qualifier="cycle",
            value=Decimal(str(strain_value)),
            recorded_at=datetime(
                day.year,
                day.month,
                day.day,
                12,
                tzinfo=timezone.utc,
            ),
            zone_offset="-04:00",
        )

    athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        first_day,
        date(2026, 7, 8),
    )

    row = db.scalar(
        select(AthleteDailyFeature).where(
            AthleteDailyFeature.user_id == user.id,
            AthleteDailyFeature.local_date == date(2026, 7, 8),
        )
    )

    assert row is not None
    assert row.strain_score == 100
    assert row.strain_acute_7d == 4
    assert row.strain_chronic_28d == 4
    assert row.source_coverage["baseline_sample_counts"]["strain_7d"] == 7
    assert row.source_coverage["baseline_sample_counts"]["strain_28d"] == 7


def test_missing_timezone_is_not_assigned_to_utc_date(
    db: Session,
) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )

    recorded_at = datetime(
        2026,
        7,
        5,
        12,
        tzinfo=timezone.utc,
    )

    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.RECOVERY,
        value=Decimal("80"),
        recorded_at=recorded_at,
        zone_offset=None,
    )
    DataPointSeriesFactory(
        data_source=data_source,
        series_type=(SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()),
        value=Decimal("58"),
        recorded_at=recorded_at,
        zone_offset=None,
    )

    row = athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 5),
        date(2026, 7, 5),
    )[0]

    assert row.timezone == "unknown"
    assert row.recovery_score is None
    assert row.hrv_rmssd_ms is None
    assert row.source_coverage["timezone"]["missing"] is True
    assert row.source_coverage["scan_missing_timezone"]["recovery_score"] == 1
    assert row.source_coverage["scan_missing_timezone"]["hrv_rmssd_ms"] == 1


def test_rebuild_is_idempotent(db: Session) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )

    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.STRAIN,
        qualifier="cycle",
        value=Decimal("10"),
        recorded_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
    )

    athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 5),
        date(2026, 7, 5),
    )
    athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 5),
        date(2026, 7, 5),
    )

    row_count = db.scalar(
        select(func.count())
        .select_from(AthleteDailyFeature)
        .where(
            AthleteDailyFeature.user_id == user.id,
            AthleteDailyFeature.local_date == date(2026, 7, 5),
        )
    )

    assert row_count == 1


def test_recovery_components_use_linked_sleep_wake_date(
    db: Session,
) -> None:
    user = UserFactory()
    data_source = DataSourceFactory(
        user=user,
        provider=ProviderName.WHOOP,
        source="whoop",
        device_model="Whoop",
    )
    sleep_record = EventRecordFactory(
        data_source=data_source,
        category="sleep",
        type="sleep_session",
        source_name="Whoop",
        start_datetime=datetime(
            2026,
            7,
            4,
            19,
            tzinfo=timezone.utc,
        ),
        end_datetime=datetime(
            2026,
            7,
            5,
            3,
            tzinfo=timezone.utc,
        ),
        duration_seconds=28_800,
        zone_offset="-04:00",
    )
    SleepDetailsFactory(
        event_record=sleep_record,
        sleep_total_duration_minutes=450,
        sleep_time_in_bed_minutes=480,
        sleep_efficiency_score=Decimal("92"),
        is_nap=False,
    )
    HealthScoreFactory(
        data_source=data_source,
        user_id=user.id,
        provider=ProviderName.WHOOP,
        category=HealthScoreCategory.RECOVERY,
        value=Decimal("82"),
        recorded_at=datetime(
            2026,
            7,
            5,
            12,
            tzinfo=timezone.utc,
        ),
        zone_offset="-04:00",
        sleep_record_id=sleep_record.id,
        components={
            "hrv_rmssd_milli": {"value": 61.25},
            "resting_heart_rate": {"value": 49},
        },
    )

    rows = athlete_daily_feature_service.rebuild_range(
        db,
        user.id,
        date(2026, 7, 4),
        date(2026, 7, 5),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.local_date == date(2026, 7, 4)
    assert row.recovery_score == 82
    assert row.hrv_rmssd_ms == 61.25
    assert row.resting_heart_rate_bpm == 49


def test_source_window_propagates_baseline_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AthleteDailyFeatureService()
    captured = {}

    def fake_rebuild(
        _db: object,
        _user_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[object]:
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return []

    monkeypatch.setattr(service, "rebuild_range", fake_rebuild)

    service.rebuild_for_source_window(
        object(),
        UserFactory.build().id,
        datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
        through_date=date(2026, 7, 10),
    )

    assert captured["start_date"] == date(2026, 5, 31)
    assert captured["end_date"] == date(2026, 7, 1)
