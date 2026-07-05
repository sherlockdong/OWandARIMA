"""Build normalized daily athlete features from health and sleep records."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DataPointSeries, DataSource, EventRecord, HealthScore, SleepDetails
from app.models.athlete_daily_feature import AthleteDailyFeature
from app.schemas.enums import HealthScoreCategory, SeriesType, get_series_type_id

_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")
_MAX_TIMEZONE_SHIFT = timedelta(hours=14)
_BASELINE_LOOKBACK_DAYS = 28
_BASELINE_PROPAGATION_DAYS = 28


@dataclass(frozen=True, slots=True)
class _MetricObservation:
    value: float
    recorded_at: datetime
    zone_offset: str
    record_id: str
    provider: str
    priority: int = 0


@dataclass(frozen=True, slots=True)
class _SleepObservation:
    duration_seconds: int | None
    efficiency_percentage: float | None
    end_at: datetime
    zone_offset: str
    record_id: str
    provider: str
    is_nap: bool


@dataclass(slots=True)
class _DailyBucket:
    recovery: list[_MetricObservation] = field(default_factory=list)
    strain: list[_MetricObservation] = field(default_factory=list)
    hrv: list[_MetricObservation] = field(default_factory=list)
    resting_hr: list[_MetricObservation] = field(default_factory=list)
    sleeps: list[_SleepObservation] = field(default_factory=list)


@dataclass(slots=True)
class _AggregationState:
    buckets: dict[date, _DailyBucket] = field(default_factory=dict)
    missing_timezone: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True, slots=True)
class _DailySnapshot:
    timezone_value: str
    recovery_score: float | None
    strain_score: float | None
    hrv_rmssd_ms: float | None
    resting_heart_rate_bpm: float | None
    sleep_duration_seconds: int | None
    sleep_efficiency_percentage: float | None
    coverage: dict[str, Any]


class AthleteDailyFeatureService:
    """Aggregate normalized source records into one feature row per local day."""

    def rebuild_for_source_window(
        self,
        db: Session,
        user_id: UUID,
        source_start: datetime,
        source_end: datetime,
        *,
        through_date: date | None = None,
    ) -> list[AthleteDailyFeature]:
        """Rebuild dates affected by a changed source timestamp window."""
        source_start_utc = self._as_utc(source_start)
        source_end_utc = self._as_utc(source_end)

        if source_end_utc < source_start_utc:
            raise ValueError("source_end must be on or after source_start")

        rebuild_start = (source_start_utc - _MAX_TIMEZONE_SHIFT).date()
        last_source_date = (source_end_utc + _MAX_TIMEZONE_SHIFT).date()
        rebuild_end = last_source_date + timedelta(days=_BASELINE_PROPAGATION_DAYS)

        maximum_date = through_date if through_date is not None else datetime.now(timezone.utc).date()
        rebuild_end = min(rebuild_end, maximum_date)

        if rebuild_end < rebuild_start:
            rebuild_end = rebuild_start

        return self.rebuild_range(
            db,
            user_id,
            rebuild_start,
            rebuild_end,
        )

    def rebuild_range(
        self,
        db: Session,
        user_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[AthleteDailyFeature]:
        """Rebuild and upsert daily features for an inclusive local-date range."""
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")

        lookback_start = start_date - timedelta(days=_BASELINE_LOOKBACK_DAYS)
        query_start = datetime.combine(lookback_start, time.min, tzinfo=timezone.utc) - _MAX_TIMEZONE_SHIFT
        query_end = (
            datetime.combine(
                end_date + timedelta(days=1),
                time.min,
                tzinfo=timezone.utc,
            )
            + _MAX_TIMEZONE_SHIFT
        )

        state = self._load_state(
            db=db,
            user_id=user_id,
            query_start=query_start,
            query_end=query_end,
            minimum_local_date=lookback_start,
            maximum_local_date=end_date,
        )

        snapshots = {
            day: self._build_snapshot(
                state.buckets.get(day, _DailyBucket()),
                state.missing_timezone,
            )
            for day in self._date_range(lookback_start, end_date)
        }

        computed_at = datetime.now(timezone.utc)
        rows_to_upsert: list[dict[str, Any]] = []

        feature_start_date = start_date
        feature_end_date = end_date
        source_dates = [day for day in state.buckets if start_date <= day <= end_date]

        if source_dates:
            feature_start_date = max(start_date, min(source_dates))
            feature_end_date = min(end_date, max(source_dates))

        for day in self._date_range(
            feature_start_date,
            feature_end_date,
        ):
            snapshot = snapshots[day]

            hrv_7d, hrv_7d_count = self._prior_mean(
                snapshots,
                day,
                "hrv_rmssd_ms",
                7,
            )
            hrv_28d, hrv_28d_count = self._prior_mean(
                snapshots,
                day,
                "hrv_rmssd_ms",
                28,
            )
            resting_hr_7d, resting_hr_7d_count = self._prior_mean(
                snapshots,
                day,
                "resting_heart_rate_bpm",
                7,
            )
            resting_hr_28d, resting_hr_28d_count = self._prior_mean(
                snapshots,
                day,
                "resting_heart_rate_bpm",
                28,
            )
            sleep_7d, sleep_7d_count = self._prior_mean(
                snapshots,
                day,
                "sleep_duration_seconds",
                7,
            )
            strain_7d, strain_7d_count = self._prior_mean(
                snapshots,
                day,
                "strain_score",
                7,
            )
            strain_28d, strain_28d_count = self._prior_mean(
                snapshots,
                day,
                "strain_score",
                28,
            )

            coverage = dict(snapshot.coverage)
            coverage["baseline_sample_counts"] = {
                "hrv_7d": hrv_7d_count,
                "hrv_28d": hrv_28d_count,
                "resting_hr_7d": resting_hr_7d_count,
                "resting_hr_28d": resting_hr_28d_count,
                "sleep_duration_7d": sleep_7d_count,
                "strain_7d": strain_7d_count,
                "strain_28d": strain_28d_count,
            }

            rows_to_upsert.append(
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "local_date": day,
                    "timezone": snapshot.timezone_value,
                    "recovery_score": snapshot.recovery_score,
                    "strain_score": snapshot.strain_score,
                    "hrv_rmssd_ms": snapshot.hrv_rmssd_ms,
                    "resting_heart_rate_bpm": snapshot.resting_heart_rate_bpm,
                    "sleep_duration_seconds": snapshot.sleep_duration_seconds,
                    "sleep_efficiency_percentage": (snapshot.sleep_efficiency_percentage),
                    "hrv_baseline_7d": hrv_7d,
                    "hrv_baseline_28d": hrv_28d,
                    "resting_hr_baseline_7d": resting_hr_7d,
                    "resting_hr_baseline_28d": resting_hr_28d,
                    "sleep_duration_baseline_7d": (round(sleep_7d) if sleep_7d is not None else None),
                    "strain_acute_7d": strain_7d,
                    "strain_chronic_28d": strain_28d,
                    "source_coverage": coverage,
                    "feature_version": 1,
                    "computed_at": computed_at,
                }
            )

        if rows_to_upsert:
            statement = insert(AthleteDailyFeature).values(rows_to_upsert)
            excluded = statement.excluded

            statement = statement.on_conflict_do_update(
                constraint="uq_athlete_daily_feature_user_date",
                set_={
                    "timezone": excluded.timezone,
                    "recovery_score": excluded.recovery_score,
                    "strain_score": excluded.strain_score,
                    "hrv_rmssd_ms": excluded.hrv_rmssd_ms,
                    "resting_heart_rate_bpm": excluded.resting_heart_rate_bpm,
                    "sleep_duration_seconds": excluded.sleep_duration_seconds,
                    "sleep_efficiency_percentage": (excluded.sleep_efficiency_percentage),
                    "hrv_baseline_7d": excluded.hrv_baseline_7d,
                    "hrv_baseline_28d": excluded.hrv_baseline_28d,
                    "resting_hr_baseline_7d": (excluded.resting_hr_baseline_7d),
                    "resting_hr_baseline_28d": (excluded.resting_hr_baseline_28d),
                    "sleep_duration_baseline_7d": (excluded.sleep_duration_baseline_7d),
                    "strain_acute_7d": excluded.strain_acute_7d,
                    "strain_chronic_28d": excluded.strain_chronic_28d,
                    "source_coverage": excluded.source_coverage,
                    "feature_version": excluded.feature_version,
                    "computed_at": excluded.computed_at,
                },
            )
            db.execute(statement)
            db.commit()

        result = db.scalars(
            select(AthleteDailyFeature)
            .where(
                AthleteDailyFeature.user_id == user_id,
                AthleteDailyFeature.local_date >= feature_start_date,
                AthleteDailyFeature.local_date <= feature_end_date,
            )
            .order_by(AthleteDailyFeature.local_date)
        ).all()

        return list(result)

    def _load_state(
        self,
        db: Session,
        user_id: UUID,
        query_start: datetime,
        query_end: datetime,
        minimum_local_date: date,
        maximum_local_date: date,
    ) -> _AggregationState:
        state = _AggregationState()

        self._load_health_scores(
            db,
            user_id,
            query_start,
            query_end,
            minimum_local_date,
            maximum_local_date,
            state,
        )
        self._load_timeseries(
            db,
            user_id,
            query_start,
            query_end,
            minimum_local_date,
            maximum_local_date,
            state,
        )
        self._load_sleep(
            db,
            user_id,
            query_start,
            query_end,
            minimum_local_date,
            maximum_local_date,
            state,
        )

        return state

    def _load_health_scores(
        self,
        db: Session,
        user_id: UUID,
        query_start: datetime,
        query_end: datetime,
        minimum_local_date: date,
        maximum_local_date: date,
        state: _AggregationState,
    ) -> None:
        rows = db.execute(
            select(HealthScore, EventRecord)
            .outerjoin(
                EventRecord,
                HealthScore.sleep_record_id == EventRecord.id,
            )
            .where(
                HealthScore.user_id == user_id,
                HealthScore.recorded_at >= query_start,
                HealthScore.recorded_at < query_end,
                HealthScore.category.in_(
                    [
                        HealthScoreCategory.RECOVERY,
                        HealthScoreCategory.STRAIN,
                    ]
                ),
            )
        ).all()

        for score, linked_sleep in rows:
            if score.category == HealthScoreCategory.RECOVERY:
                metric_name = "recovery_score"
            elif score.category == HealthScoreCategory.STRAIN and score.qualifier == "cycle":
                metric_name = "cycle_strain"
            else:
                continue

            if score.value is None:
                continue

            source_timestamp = score.recorded_at
            zone_offset = score.zone_offset

            if metric_name == "recovery_score" and linked_sleep is not None and linked_sleep.zone_offset is not None:
                source_timestamp = linked_sleep.end_datetime
                zone_offset = linked_sleep.zone_offset

            if zone_offset is None:
                state.missing_timezone[metric_name] += 1
                continue

            local_day = self._local_date(
                source_timestamp,
                zone_offset,
            )
            if local_day is None:
                state.missing_timezone[metric_name] += 1
                continue
            if not minimum_local_date <= local_day <= maximum_local_date:
                continue

            provider = self._provider_value(score.provider)
            recorded_at = self._as_utc(source_timestamp)
            bucket = state.buckets.setdefault(
                local_day,
                _DailyBucket(),
            )

            observation = _MetricObservation(
                value=float(score.value),
                recorded_at=recorded_at,
                zone_offset=zone_offset,
                record_id=str(score.id),
                provider=provider,
            )

            if metric_name == "recovery_score":
                bucket.recovery.append(observation)

                component_targets = (
                    (
                        "hrv_rmssd_milli",
                        "hrv_rmssd_ms",
                        bucket.hrv,
                    ),
                    (
                        "resting_heart_rate",
                        "resting_heart_rate_bpm",
                        bucket.resting_hr,
                    ),
                )

                for (
                    component_name,
                    coverage_name,
                    target,
                ) in component_targets:
                    component_value = self._component_value(
                        score.components,
                        component_name,
                    )
                    if component_value is None:
                        continue

                    target.append(
                        _MetricObservation(
                            value=component_value,
                            recorded_at=recorded_at,
                            zone_offset=zone_offset,
                            record_id=(f"{score.id}:{component_name}"),
                            provider=provider,
                            priority=1,
                        )
                    )
            else:
                bucket.strain.append(observation)

    def _load_timeseries(
        self,
        db: Session,
        user_id: UUID,
        query_start: datetime,
        query_end: datetime,
        minimum_local_date: date,
        maximum_local_date: date,
        state: _AggregationState,
    ) -> None:
        resting_hr_id = get_series_type_id(SeriesType.resting_heart_rate)
        hrv_id = get_series_type_id(SeriesType.heart_rate_variability_rmssd)
        metric_by_type = {
            resting_hr_id: "resting_heart_rate_bpm",
            hrv_id: "hrv_rmssd_ms",
        }

        rows = db.execute(
            select(DataPointSeries, DataSource.provider)
            .join(
                DataSource,
                DataPointSeries.data_source_id == DataSource.id,
            )
            .where(
                DataSource.user_id == user_id,
                DataPointSeries.recorded_at >= query_start,
                DataPointSeries.recorded_at < query_end,
                DataPointSeries.series_type_definition_id.in_([resting_hr_id, hrv_id]),
            )
        ).all()

        for sample, provider in rows:
            metric_name = metric_by_type[sample.series_type_definition_id]
            zone_offset = sample.zone_offset
            if zone_offset is None:
                state.missing_timezone[metric_name] += 1
                continue

            local_day = self._local_date(
                sample.recorded_at,
                zone_offset,
            )
            if local_day is None:
                state.missing_timezone[metric_name] += 1
                continue
            if not minimum_local_date <= local_day <= maximum_local_date:
                continue

            observation = _MetricObservation(
                value=float(sample.value),
                recorded_at=self._as_utc(sample.recorded_at),
                zone_offset=zone_offset,
                record_id=str(sample.id),
                provider=self._provider_value(provider),
            )
            bucket = state.buckets.setdefault(local_day, _DailyBucket())

            if metric_name == "hrv_rmssd_ms":
                bucket.hrv.append(observation)
            else:
                bucket.resting_hr.append(observation)

    def _load_sleep(
        self,
        db: Session,
        user_id: UUID,
        query_start: datetime,
        query_end: datetime,
        minimum_local_date: date,
        maximum_local_date: date,
        state: _AggregationState,
    ) -> None:
        rows = db.execute(
            select(EventRecord, DataSource.provider)
            .join(
                DataSource,
                EventRecord.data_source_id == DataSource.id,
            )
            .where(
                DataSource.user_id == user_id,
                EventRecord.category == "sleep",
                EventRecord.end_datetime >= query_start,
                EventRecord.end_datetime < query_end,
            )
        ).all()

        for record, provider in rows:
            detail = record.detail
            if not isinstance(detail, SleepDetails):
                continue

            zone_offset = record.zone_offset
            if zone_offset is None:
                state.missing_timezone["sleep"] += 1
                continue

            local_day = self._local_date(
                record.end_datetime,
                zone_offset,
            )
            if local_day is None:
                state.missing_timezone["sleep"] += 1
                continue
            if not minimum_local_date <= local_day <= maximum_local_date:
                continue

            duration_seconds = None
            if detail.sleep_total_duration_minutes is not None:
                duration_seconds = detail.sleep_total_duration_minutes * 60
            elif record.duration_seconds is not None:
                duration_seconds = record.duration_seconds

            efficiency = float(detail.sleep_efficiency_score) if detail.sleep_efficiency_score is not None else None

            observation = _SleepObservation(
                duration_seconds=duration_seconds,
                efficiency_percentage=efficiency,
                end_at=self._as_utc(record.end_datetime),
                zone_offset=zone_offset,
                record_id=str(record.id),
                provider=self._provider_value(provider),
                is_nap=bool(detail.is_nap),
            )
            state.buckets.setdefault(
                local_day,
                _DailyBucket(),
            ).sleeps.append(observation)

    def _build_snapshot(
        self,
        bucket: _DailyBucket,
        missing_timezone: Counter[str],
    ) -> _DailySnapshot:
        recovery = self._latest_metric(bucket.recovery)
        strain = self._latest_metric(bucket.strain)
        hrv = self._latest_metric(bucket.hrv)
        resting_hr = self._latest_metric(bucket.resting_hr)

        main_sleeps = [sleep for sleep in bucket.sleeps if not sleep.is_nap]
        naps = [sleep for sleep in bucket.sleeps if sleep.is_nap]

        main_sleep = self._select_main_sleep(main_sleeps)
        latest_nap = max(naps, key=lambda item: (item.end_at, item.record_id)) if naps else None

        timezone_candidates: list[tuple[str, _MetricObservation | _SleepObservation | None]] = [
            ("cycle_strain", strain),
            ("main_sleep", main_sleep),
            ("recovery_score", recovery),
            ("hrv_rmssd_ms", hrv),
            ("resting_heart_rate_bpm", resting_hr),
            ("nap", latest_nap),
        ]
        timezone_source = None
        timezone_value = "unknown"

        for source_name, observation in timezone_candidates:
            if observation is not None:
                timezone_source = source_name
                timezone_value = observation.zone_offset
                break

        all_observations: list[_MetricObservation | _SleepObservation] = [
            *bucket.recovery,
            *bucket.strain,
            *bucket.hrv,
            *bucket.resting_hr,
            *bucket.sleeps,
        ]
        offsets_seen = sorted({observation.zone_offset for observation in all_observations})

        missing_metrics = [
            name
            for name, value in {
                "recovery_score": recovery,
                "strain_score": strain,
                "hrv_rmssd_ms": hrv,
                "resting_heart_rate_bpm": resting_hr,
                "sleep_duration_seconds": main_sleep,
            }.items()
            if value is None
        ]

        coverage: dict[str, Any] = {
            "metrics": {
                "recovery_score": self._metric_coverage(
                    bucket.recovery,
                    recovery,
                ),
                "cycle_strain": self._metric_coverage(
                    bucket.strain,
                    strain,
                ),
                "hrv_rmssd_ms": self._metric_coverage(
                    bucket.hrv,
                    hrv,
                ),
                "resting_heart_rate_bpm": self._metric_coverage(
                    bucket.resting_hr,
                    resting_hr,
                ),
                "sleep": {
                    "main_records": len(main_sleeps),
                    "main_duplicates": max(0, len(main_sleeps) - 1),
                    "selected_record_id": (main_sleep.record_id if main_sleep is not None else None),
                    "selected_provider": (main_sleep.provider if main_sleep is not None else None),
                    "nap_records": len(naps),
                    "nap_duration_seconds": sum(sleep.duration_seconds or 0 for sleep in naps),
                },
            },
            "missing_metrics": missing_metrics,
            "timezone": {
                "value": timezone_value,
                "source": timezone_source,
                "offsets_seen": offsets_seen,
                "conflict": len(offsets_seen) > 1,
                "missing": timezone_value == "unknown",
            },
            "scan_missing_timezone": dict(sorted(missing_timezone.items())),
        }

        return _DailySnapshot(
            timezone_value=timezone_value,
            recovery_score=recovery.value if recovery else None,
            strain_score=strain.value if strain else None,
            hrv_rmssd_ms=hrv.value if hrv else None,
            resting_heart_rate_bpm=(resting_hr.value if resting_hr else None),
            sleep_duration_seconds=(main_sleep.duration_seconds if main_sleep else None),
            sleep_efficiency_percentage=(main_sleep.efficiency_percentage if main_sleep else None),
            coverage=coverage,
        )

    @staticmethod
    def _component_value(
        components: dict[str, Any] | None,
        component_name: str,
    ) -> float | None:
        if not components:
            return None

        component = components.get(component_name)
        if component is None:
            return None

        value = component.get("value") if isinstance(component, dict) else getattr(component, "value", None)

        if value is None:
            return None

        return float(value)

    @staticmethod
    def _metric_coverage(
        observations: list[_MetricObservation],
        selected: _MetricObservation | None,
    ) -> dict[str, Any]:
        return {
            "records": len(observations),
            "duplicates": max(0, len(observations) - 1),
            "selected_record_id": (selected.record_id if selected is not None else None),
            "selected_provider": (selected.provider if selected is not None else None),
        }

    @staticmethod
    def _latest_metric(
        observations: list[_MetricObservation],
    ) -> _MetricObservation | None:
        if not observations:
            return None
        return max(
            observations,
            key=lambda item: (
                item.priority,
                item.recorded_at,
                item.record_id,
            ),
        )

    @staticmethod
    def _select_main_sleep(
        sleeps: list[_SleepObservation],
    ) -> _SleepObservation | None:
        if not sleeps:
            return None
        return max(
            sleeps,
            key=lambda item: (
                item.duration_seconds if item.duration_seconds is not None else -1,
                item.end_at,
                item.record_id,
            ),
        )

    @staticmethod
    def _prior_mean(
        snapshots: dict[date, _DailySnapshot],
        current_date: date,
        attribute: str,
        window_days: int,
    ) -> tuple[float | None, int]:
        values: list[float] = []

        for day_offset in range(1, window_days + 1):
            previous_day = current_date - timedelta(days=day_offset)
            snapshot = snapshots.get(previous_day)
            if snapshot is None:
                continue

            value = getattr(snapshot, attribute)
            if value is not None:
                values.append(float(value))

        if not values:
            return None, 0

        return fmean(values), len(values)

    @classmethod
    def _local_date(
        cls,
        recorded_at: datetime,
        zone_offset: str | None,
    ) -> date | None:
        offset = cls._parse_offset(zone_offset)
        if offset is None:
            return None
        return (cls._as_utc(recorded_at) + offset).date()

    @staticmethod
    def _parse_offset(value: str | None) -> timedelta | None:
        if value is None:
            return None

        match = _OFFSET_PATTERN.fullmatch(value)
        if match is None:
            return None

        sign_text, hours_text, minutes_text = match.groups()
        hours = int(hours_text)
        minutes = int(minutes_text)

        if hours > 23 or minutes > 59:
            return None

        offset = timedelta(hours=hours, minutes=minutes)
        return offset if sign_text == "+" else -offset

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _provider_value(provider: Any) -> str:
        value = getattr(provider, "value", provider)
        return str(value)

    @staticmethod
    def _date_range(start_date: date, end_date: date) -> list[date]:
        day_count = (end_date - start_date).days
        return [start_date + timedelta(days=offset) for offset in range(day_count + 1)]


athlete_daily_feature_service = AthleteDailyFeatureService()
