"""Whoop 247 Data implementation for sleep, recovery, and activity samples."""

from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from app.database import DbSession
from app.models import DataPointSeries, DataSource, EventRecord
from app.repositories import EventRecordRepository, UserConnectionRepository
from app.repositories.data_source_repository import DataSourceRepository
from app.schemas.enums import HealthScoreCategory, ProviderName, SeriesType, get_series_type_id
from app.schemas.model_crud.activities import (
    EventRecordCreate,
    EventRecordDetailCreate,
    HealthScoreCreate,
    ScoreComponent,
    TimeSeriesSampleCreate,
)
from app.services.event_record_service import event_record_service
from app.services.health_score_service import health_score_service
from app.services.providers.api_client import make_authenticated_request
from app.services.providers.templates.base_247_data import Base247DataTemplate
from app.services.providers.templates.base_oauth import BaseOAuthTemplate
from app.services.providers.whoop.coverage import RECOVERY_SERIES
from app.services.raw_payload_storage import store_raw_payload
from app.services.timeseries_service import timeseries_service
from app.utils.structured_logging import log_structured


class Whoop247Data(Base247DataTemplate):
    """Whoop implementation for 247 data (sleep, recovery, activity)."""

    def __init__(
        self,
        provider_name: str,
        api_base_url: str,
        oauth: BaseOAuthTemplate,
    ):
        super().__init__(provider_name, api_base_url, oauth)
        self.event_record_repo = EventRecordRepository(EventRecord)
        self.data_source_repo = DataSourceRepository(DataSource)
        self.connection_repo = UserConnectionRepository()

    def _make_api_request(
        self,
        db: DbSession,
        user_id: UUID,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Make authenticated request to Whoop API."""
        log_structured(
            self.logger,
            "debug",
            f"Making API request to {endpoint}",
            provider="whoop",
            endpoint=endpoint,
            params=params,
        )
        return make_authenticated_request(
            db=db,
            user_id=user_id,
            connection_repo=self.connection_repo,
            oauth=self.oauth,
            api_base_url=self.api_base_url,
            provider_name=self.provider_name,
            endpoint=endpoint,
            method="GET",
            params=params,
            headers=headers,
        )

    # -------------------------------------------------------------------------
    # Sleep Data - Whoop /v2/activity/sleep
    # -------------------------------------------------------------------------

    def get_sleep_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch sleep data from Whoop API via v2 endpoint with pagination."""
        all_sleep_data = []
        next_token = None
        max_limit = 25  # Whoop API limit

        # Convert datetimes to ISO 8601 strings
        start_iso = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            params: dict[str, Any] = {
                "start": start_iso,
                "end": end_iso,
                "limit": max_limit,
            }

            if next_token:
                params["nextToken"] = next_token

            try:
                response = self._make_api_request(db, user_id, "/v2/activity/sleep", params=params)
                store_raw_payload(
                    source="api_response",
                    provider="whoop",
                    payload=response,
                    user_id=str(user_id),
                    trace_id="/v2/activity/sleep",
                )

                # Extract records from response
                records = response.get("records", []) if isinstance(response, dict) else []
                all_sleep_data.extend(records)

                # Check for next page
                next_token = response.get("next_token") if isinstance(response, dict) else None

                # Stop if no more records or no next token
                if not records or not next_token:
                    break

            except Exception as e:
                log_structured(
                    self.logger,
                    "error",
                    f"Error fetching Whoop sleep data: {e}",
                    provider="whoop",
                    task="get_sleep_data",
                    user_id=str(user_id),
                )
                # If we got some data, return what we have; otherwise re-raise
                if all_sleep_data:
                    log_structured(
                        self.logger,
                        "warning",
                        f"Returning partial sleep data due to error: {e}",
                        provider="whoop",
                        task="get_sleep_data",
                        user_id=str(user_id),
                    )
                    break
                raise

        return all_sleep_data

    def _normalize_sleep_health_score(
        self,
        normalized: dict[str, Any],
        user_id: UUID,
    ) -> HealthScoreCreate | None:
        """Build a HealthScoreCreate for Whoop sleep score."""
        if normalized.get("score_state") != "SCORED":
            return None
        performance = normalized.get("sleep_performance_percentage")
        timestamp = normalized.get("timestamp")
        if performance is None or timestamp is None:
            return None
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return None
        components = {
            k: ScoreComponent(value=v)
            for k, v in {
                "sleep_consistency_percentage": normalized.get("sleep_consistency_percentage"),
                "sleep_efficiency_percentage": normalized.get("sleep_efficiency_percentage"),
                "respiratory_rate": normalized.get("respiratory_rate"),
            }.items()
            if v is not None
        }
        return HealthScoreCreate(
            id=uuid4(),
            user_id=user_id,
            provider=ProviderName.WHOOP,
            category=HealthScoreCategory.SLEEP,
            value=performance,
            recorded_at=timestamp,
            zone_offset=normalized.get("zone_offset"),
            components=components or None,
        )

    def normalize_sleep(
        self,
        raw_sleep: dict[str, Any],
        user_id: UUID,
    ) -> tuple[dict[str, Any], HealthScoreCreate | None]:  # ty:ignore[invalid-method-override]
        """Normalize Whoop sleep data to our schema."""
        # Extract basic fields
        sleep_id = raw_sleep.get("id")
        start_time = raw_sleep.get("start")
        end_time = raw_sleep.get("end")
        nap = raw_sleep.get("nap", False)
        cycle_id = raw_sleep.get("cycle_id")
        zone_offset = raw_sleep.get("timezone_offset")

        # Extract score data (may be None if not scored yet)
        score = raw_sleep.get("score", {}) or {}
        stage_summary = score.get("stage_summary", {}) or {}

        # Time conversions: Whoop provides durations in milliseconds
        # Convert to seconds for our schema
        total_in_bed_ms = stage_summary.get("total_in_bed_time_milli", 0)
        total_awake_ms = stage_summary.get("total_awake_time_milli", 0)
        total_light_ms = stage_summary.get("total_light_sleep_time_milli", 0)
        total_slow_wave_ms = stage_summary.get("total_slow_wave_sleep_time_milli", 0)
        total_rem_ms = stage_summary.get("total_rem_sleep_time_milli", 0)

        # Convert milliseconds to seconds
        duration_seconds = int(total_in_bed_ms / 1000) if total_in_bed_ms else 0
        deep_seconds = int(total_slow_wave_ms / 1000) if total_slow_wave_ms else 0
        light_seconds = int(total_light_ms / 1000) if total_light_ms else 0
        rem_seconds = int(total_rem_ms / 1000) if total_rem_ms else 0
        awake_seconds = int(total_awake_ms / 1000) if total_awake_ms else 0

        # If duration is 0 but we have start/end times, calculate from timestamps
        if duration_seconds == 0 and start_time and end_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                duration_seconds = int((end_dt - start_dt).total_seconds())
            except (ValueError, AttributeError):
                pass

        # Efficiency percentage
        efficiency = score.get("sleep_efficiency_percentage")

        # Generate UUID for our internal ID (use Whoop ID if it's a valid UUID string)
        internal_id = uuid4()
        if sleep_id:
            with suppress(ValueError, TypeError):
                internal_id = UUID(sleep_id)

        normalized = {
            "id": internal_id,
            "user_id": user_id,
            "provider": self.provider_name,
            "timestamp": start_time or end_time,
            "start_time": start_time,
            "end_time": end_time,
            "zone_offset": zone_offset,
            "duration_seconds": duration_seconds,
            "efficiency_percent": float(efficiency) if efficiency is not None else None,
            "is_nap": nap,
            "stages": {
                "deep_seconds": deep_seconds,
                "light_seconds": light_seconds,
                "rem_seconds": rem_seconds,
                "awake_seconds": awake_seconds,
            },
            "whoop_sleep_id": sleep_id,
            "whoop_cycle_id": cycle_id,
            "score_state": raw_sleep.get("score_state"),
            "sleep_performance_percentage": score.get("sleep_performance_percentage"),
            "sleep_consistency_percentage": score.get("sleep_consistency_percentage"),
            "sleep_efficiency_percentage": efficiency,
            "respiratory_rate": score.get("respiratory_rate"),
            "raw": raw_sleep,  # Keep raw for debugging
        }
        return normalized, self._normalize_sleep_health_score(normalized, user_id)

    def save_sleep_data(
        self,
        db: DbSession,
        user_id: UUID,
        normalized_sleep: dict[str, Any],
    ) -> EventRecord | None:
        """Save normalized sleep data and return its final EventRecord."""
        sleep_id = normalized_sleep["id"]

        start_dt = None
        end_dt = None

        if normalized_sleep.get("start_time"):
            start_time = normalized_sleep["start_time"]
            if isinstance(start_time, str):
                start_dt = datetime.fromisoformat(
                    start_time.replace("Z", "+00:00"),
                )
            elif isinstance(start_time, datetime):
                start_dt = start_time

        if normalized_sleep.get("end_time"):
            end_time = normalized_sleep["end_time"]
            if isinstance(end_time, str):
                end_dt = datetime.fromisoformat(
                    end_time.replace("Z", "+00:00"),
                )
            elif isinstance(end_time, datetime):
                end_dt = end_time

        if not start_dt or not end_dt:
            log_structured(
                self.logger,
                "warning",
                f"Skipping sleep record {sleep_id}: missing start/end time",
                provider="whoop",
                task="save_sleep_data",
                user_id=str(user_id),
            )
            return None

        record = EventRecordCreate(
            id=sleep_id,
            category="sleep",
            type="sleep_session",
            source_name="Whoop",
            device_model=None,
            duration_seconds=normalized_sleep.get("duration_seconds"),
            start_datetime=start_dt,
            end_datetime=end_dt,
            zone_offset=normalized_sleep.get("zone_offset"),
            external_id=(
                str(normalized_sleep.get("whoop_sleep_id")) if normalized_sleep.get("whoop_sleep_id") else None
            ),
            source=self.provider_name,
            user_id=user_id,
        )

        stages = normalized_sleep.get("stages", {})
        total_sleep_seconds = (
            stages.get("deep_seconds", 0) + stages.get("light_seconds", 0) + stages.get("rem_seconds", 0)
        )

        detail = EventRecordDetailCreate(
            record_id=sleep_id,
            sleep_total_duration_minutes=total_sleep_seconds // 60,
            sleep_time_in_bed_minutes=(normalized_sleep.get("duration_seconds", 0) // 60),
            sleep_efficiency_score=(
                Decimal(
                    str(normalized_sleep.get("efficiency_percent", 0)),
                )
                if normalized_sleep.get("efficiency_percent") is not None
                else None
            ),
            sleep_deep_minutes=stages.get("deep_seconds", 0) // 60,
            sleep_light_minutes=stages.get("light_seconds", 0) // 60,
            sleep_rem_minutes=stages.get("rem_seconds", 0) // 60,
            sleep_awake_minutes=stages.get("awake_seconds", 0) // 60,
            is_nap=normalized_sleep.get("is_nap", False),
        )

        try:
            return event_record_service.create_or_merge_sleep(
                db,
                user_id,
                record,
                detail,
                settings.sleep_end_gap_minutes,
            )
        except Exception as e:
            log_structured(
                self.logger,
                "error",
                f"Error saving sleep record {sleep_id}: {e}",
                provider="whoop",
                task="save_sleep_data",
                user_id=str(user_id),
            )
            return None

    def _save_sleep_bundle(
        self,
        db: DbSession,
        user_id: UUID,
        raw_sleep: dict[str, Any],
    ) -> EventRecord | None:
        """Save a WHOOP sleep and upsert its linked provider sleep score."""
        normalized, health_score = self.normalize_sleep(
            raw_sleep,
            user_id,
        )
        saved_record = self.save_sleep_data(
            db,
            user_id,
            normalized,
        )

        if saved_record is not None and health_score is not None:
            linked_score = health_score.model_copy(
                update={
                    "data_source_id": saved_record.data_source_id,
                    "sleep_record_id": saved_record.id,
                    "zone_offset": saved_record.zone_offset,
                }
            )
            health_score_service.bulk_create(db, [linked_score])
            db.commit()

        return saved_record

    def get_sleep_record(
        self,
        db: DbSession,
        user_id: UUID,
        sleep_id: str,
    ) -> dict[str, Any]:
        """Fetch a single sleep record from /v2/activity/sleep/{sleepId}."""
        response = self._make_api_request(
            db,
            user_id,
            f"/v2/activity/sleep/{sleep_id}",
        )
        store_raw_payload(
            source="api_response",
            provider="whoop",
            payload=response,
            user_id=str(user_id),
            trace_id=f"/v2/activity/sleep/{sleep_id}",
        )
        return response if isinstance(response, dict) else {}

    def load_single_sleep(
        self,
        db: DbSession,
        user_id: UUID,
        sleep_id: str,
    ) -> int:
        """Fetch, normalize, and upsert one WHOOP sleep."""
        raw = self.get_sleep_record(db, user_id, sleep_id)
        if not raw:
            return 0

        try:
            return 1 if self._save_sleep_bundle(db, user_id, raw) else 0
        except Exception as e:
            log_structured(
                self.logger,
                "warning",
                f"Failed to save sleep record {sleep_id}: {e}",
                provider="whoop",
                task="load_single_sleep",
            )
            return 0

    def load_and_save_sleep(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """Load WHOOP sleep data and save it idempotently."""
        raw_data = self.get_sleep_data(
            db,
            user_id,
            start_time,
            end_time,
        )
        count = 0

        for item in raw_data:
            try:
                if self._save_sleep_bundle(db, user_id, item):
                    count += 1
            except Exception as e:
                db.rollback()
                log_structured(
                    self.logger,
                    "warning",
                    f"Failed to save sleep data: {e}",
                    provider="whoop",
                    task="load_and_save_sleep",
                    user_id=str(user_id),
                )

        return count

    # -------------------------------------------------------------------------
    # Physiological Cycle Data
    # -------------------------------------------------------------------------

    def get_cycle_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch physiological cycles from the Whoop v2 API."""
        all_cycle_data: list[dict[str, Any]] = []
        next_token = None
        max_limit = 25

        start_iso = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            params: dict[str, Any] = {
                "start": start_iso,
                "end": end_iso,
                "limit": max_limit,
            }
            if next_token:
                params["nextToken"] = next_token

            try:
                response = self._make_api_request(
                    db,
                    user_id,
                    "/v2/cycle",
                    params=params,
                )
                store_raw_payload(
                    source="api_response",
                    provider="whoop",
                    payload=response,
                    user_id=str(user_id),
                    trace_id="/v2/cycle",
                )

                records = response.get("records", []) if isinstance(response, dict) else []
                all_cycle_data.extend(records)

                next_token = response.get("next_token") if isinstance(response, dict) else None
                if not records or not next_token:
                    break
            except Exception as e:
                log_structured(
                    self.logger,
                    "error",
                    f"Error fetching Whoop cycle data: {e}",
                    provider="whoop",
                    task="get_cycle_data",
                    user_id=str(user_id),
                )
                if all_cycle_data:
                    log_structured(
                        self.logger,
                        "warning",
                        f"Returning partial cycle data due to error: {e}",
                        provider="whoop",
                        task="get_cycle_data",
                        user_id=str(user_id),
                    )
                    break
                raise

        return all_cycle_data

    def _normalize_cycle_health_score(
        self,
        raw_cycle: dict[str, Any],
        user_id: UUID,
    ) -> HealthScoreCreate | None:
        """Convert a scored Whoop physiological cycle into daily strain."""
        if raw_cycle.get("score_state") != "SCORED":
            return None

        score = raw_cycle.get("score") or {}
        strain = score.get("strain")
        start = raw_cycle.get("start")

        if strain is None or start is None:
            return None

        try:
            recorded_at = datetime.fromisoformat(
                start.replace("Z", "+00:00"),
            )
        except (ValueError, AttributeError):
            return None

        components = {
            key: ScoreComponent(value=value)
            for key, value in {
                "kilojoule": score.get("kilojoule"),
                "average_heart_rate": score.get("average_heart_rate"),
                "max_heart_rate": score.get("max_heart_rate"),
            }.items()
            if value is not None
        }

        return HealthScoreCreate(
            id=uuid4(),
            user_id=user_id,
            provider=ProviderName.WHOOP,
            category=HealthScoreCategory.STRAIN,
            value=strain,
            qualifier="cycle",
            recorded_at=recorded_at,
            zone_offset=raw_cycle.get("timezone_offset"),
            components=components or None,
        )

    def load_and_save_cycles(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """Fetch and save scored Whoop physiological cycles."""
        raw_data = self.get_cycle_data(
            db,
            user_id,
            start_time,
            end_time,
        )

        cycle_scores = [
            score
            for raw_cycle in raw_data
            if (
                score := self._normalize_cycle_health_score(
                    raw_cycle,
                    user_id,
                )
            )
            is not None
        ]

        if cycle_scores:
            health_score_service.bulk_create(db, cycle_scores)
            db.commit()

        return len(cycle_scores)

    def load_and_save_all(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        is_first_sync: bool = False,
    ) -> dict[str, int]:
        """Load and save all 247 data types (sleep, recovery, activity).

        Args:
            db: Database session
            user_id: User UUID
            start_time: Start of date range (defaults to 30 days ago)
            end_time: End of date range (defaults to now)
            is_first_sync: Whether this is the first sync (unused, for API compatibility)
        """
        # Handle date defaults (last 30 days if not specified)
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc)

        results = {
            "sleep_sessions_synced": 0,
            "cycle_samples_synced": 0,
            "recovery_samples_synced": 0,
            "activity_samples_synced": 0,
            "body_measurement_samples_synced": 0,
        }

        try:
            results["sleep_sessions_synced"] = self.load_and_save_sleep(db, user_id, start_time, end_time)
        except Exception as e:
            db.rollback()
            log_structured(
                self.logger,
                "error",
                f"Failed to sync sleep data: {e}",
                provider="whoop",
                task="load_and_save_all",
                user_id=str(user_id),
            )

        try:
            results["cycle_samples_synced"] = self.load_and_save_cycles(
                db,
                user_id,
                start_time,
                end_time,
            )
        except Exception as e:
            db.rollback()
            log_structured(
                self.logger,
                "error",
                f"Failed to sync cycle data: {e}",
                provider="whoop",
                task="load_and_save_all",
                user_id=str(user_id),
            )

        try:
            results["recovery_samples_synced"] = self.load_and_save_recovery(db, user_id, start_time, end_time)
        except Exception as e:
            db.rollback()
            log_structured(
                self.logger,
                "error",
                f"Failed to sync recovery data: {e}",
                provider="whoop",
                task="load_and_save_all",
                user_id=str(user_id),
            )

        try:
            results["body_measurement_samples_synced"] = self.load_and_save_body_measurement(db, user_id)
        except Exception as e:
            db.rollback()
            log_structured(
                self.logger,
                "error",
                f"Failed to sync body measurement data: {e}",
                provider="whoop",
                task="load_and_save_all",
                user_id=str(user_id),
            )

        return results

    # -------------------------------------------------------------------------
    # Body Measurement Data (Height/Weight)
    # -------------------------------------------------------------------------

    def get_body_measurement(
        self,
        db: DbSession,
        user_id: UUID,
    ) -> dict[str, Any]:
        """Fetch body measurements from Whoop API.

        Returns height_meter, weight_kilogram, and max_heart_rate.
        See: https://developer.whoop.com/api/#tag/Body-Measurement
        """
        try:
            response = self._make_api_request(db, user_id, "/v2/user/measurement/body")
            store_raw_payload(
                source="api_response",
                provider="whoop",
                payload=response,
                user_id=str(user_id),
                trace_id="/v2/user/measurement/body",
            )
            return response if isinstance(response, dict) else {}
        except Exception as e:
            log_structured(
                self.logger,
                "error",
                f"Error fetching Whoop body measurement: {e}",
                provider="whoop",
                task="get_body_measurement",
                user_id=str(user_id),
            )
            return {}

    def _get_latest_value(
        self,
        db: DbSession,
        user_id: UUID,
        series_type: SeriesType,
    ) -> Decimal | None:
        """Get the most recent value for a series type for this user/provider."""
        type_id = get_series_type_id(series_type)
        result = (
            db.query(DataPointSeries.value)
            .join(DataSource, DataPointSeries.data_source_id == DataSource.id)
            .filter(
                DataSource.user_id == user_id,
                DataSource.source == self.provider_name,
                DataPointSeries.series_type_definition_id == type_id,
            )
            .order_by(DataPointSeries.recorded_at.desc())
            .first()
        )
        return result[0] if result else None

    def load_and_save_body_measurement(
        self,
        db: DbSession,
        user_id: UUID,
    ) -> int:
        """Fetch body measurements and save height/weight to data_point_series.

        Only saves if the value has changed from the most recent entry.
        Returns the number of samples saved.
        """
        body = self.get_body_measurement(db, user_id)
        if not body:
            return 0

        recorded_at = datetime.now(timezone.utc)
        samples_to_create: list[TimeSeriesSampleCreate] = []

        # Save height (convert meters to centimeters) if changed
        height_meter = body.get("height_meter")
        if height_meter is not None:
            try:
                height_cm = Decimal(str(height_meter)) * 100
                latest_height = self._get_latest_value(db, user_id, SeriesType.height)

                if latest_height is None or abs(latest_height - height_cm) > Decimal("0.01"):
                    samples_to_create.append(
                        TimeSeriesSampleCreate(
                            id=uuid4(),
                            user_id=user_id,
                            source=self.provider_name,
                            recorded_at=recorded_at,
                            value=height_cm,
                            series_type=SeriesType.height,
                        )
                    )
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    f"Failed to build height sample: {e}",
                    provider="whoop",
                    task="load_and_save_body_measurement",
                    user_id=str(user_id),
                )

        # Save weight (already in kilograms) if changed
        weight_kg = body.get("weight_kilogram")
        if weight_kg is not None:
            try:
                weight = Decimal(str(weight_kg))
                latest_weight = self._get_latest_value(db, user_id, SeriesType.weight)

                if latest_weight is None or abs(latest_weight - weight) > Decimal("0.01"):
                    samples_to_create.append(
                        TimeSeriesSampleCreate(
                            id=uuid4(),
                            user_id=user_id,
                            source=self.provider_name,
                            recorded_at=recorded_at,
                            value=weight,
                            series_type=SeriesType.weight,
                        )
                    )
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    f"Failed to build weight sample: {e}",
                    provider="whoop",
                    task="load_and_save_body_measurement",
                    user_id=str(user_id),
                )

        counts: int = 0
        if samples_to_create:
            counts = timeseries_service.bulk_create_samples(db, samples_to_create)
            db.commit()

        return counts

    # -------------------------------------------------------------------------
    # Recovery Data
    # -------------------------------------------------------------------------

    def get_recovery_data(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch recovery data from Whoop API via v2 endpoint with pagination.

        Returns list of recovery records containing recovery_score, resting_heart_rate,
        hrv_rmssd_milli, spo2_percentage, and skin_temp_celsius.
        """
        all_recovery_data = []
        next_token = None
        max_limit = 25  # Whoop API limit

        # Convert datetimes to ISO 8601 strings
        start_iso = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            params: dict[str, Any] = {
                "start": start_iso,
                "end": end_iso,
                "limit": max_limit,
            }

            if next_token:
                params["nextToken"] = next_token

            try:
                response = self._make_api_request(db, user_id, "/v2/recovery", params=params)
                store_raw_payload(
                    source="api_response",
                    provider="whoop",
                    payload=response,
                    user_id=str(user_id),
                    trace_id="/v2/recovery",
                )

                # Extract records from response
                records = response.get("records", []) if isinstance(response, dict) else []
                all_recovery_data.extend(records)

                # Check for next page
                next_token = response.get("next_token") if isinstance(response, dict) else None

                # Stop if no more records or no next token
                if not records or not next_token:
                    break

            except Exception as e:
                log_structured(
                    self.logger,
                    "error",
                    f"Error fetching Whoop recovery data: {e}",
                    provider="whoop",
                    task="get_recovery_data",
                    user_id=str(user_id),
                )
                # If we got some data, return what we have; otherwise re-raise
                if all_recovery_data:
                    log_structured(
                        self.logger,
                        "warning",
                        f"Returning partial recovery data due to error: {e}",
                        provider="whoop",
                        task="get_recovery_data",
                        user_id=str(user_id),
                    )
                    break
                raise

        return all_recovery_data

    def _normalize_recovery_health_score(
        self,
        normalized: dict[str, Any],
        user_id: UUID,
    ) -> HealthScoreCreate | None:
        """Build a linked WHOOP recovery health score."""
        recovery_score = normalized.get("recovery_score")
        timestamp = normalized.get("timestamp")

        if recovery_score is None or timestamp is None:
            return None

        components = {
            key: ScoreComponent(value=normalized.get(key))
            for key in (
                "resting_heart_rate",
                "hrv_rmssd_milli",
                "spo2_percentage",
                "skin_temp_celsius",
            )
            if normalized.get(key) is not None
        }

        return HealthScoreCreate(
            id=uuid4(),
            user_id=user_id,
            data_source_id=normalized.get("data_source_id"),
            provider=ProviderName.WHOOP,
            category=HealthScoreCategory.RECOVERY,
            value=recovery_score,
            recorded_at=timestamp,
            zone_offset=normalized.get("zone_offset"),
            sleep_record_id=normalized.get("sleep_record_id"),
            components=components or None,
        )

    def normalize_recovery(
        self,
        raw_recovery: dict[str, Any],
        user_id: UUID,
        *,
        zone_offset: str | None = None,
        sleep_record_id: UUID | None = None,
        data_source_id: UUID | None = None,
    ) -> tuple[dict[str, Any], HealthScoreCreate | None]:  # ty:ignore[invalid-method-override]
        """Normalize a scored WHOOP recovery with resolved sleep context."""
        if raw_recovery.get("score_state") != "SCORED":
            return {}, None

        created_at = raw_recovery.get("created_at")
        timestamp = None

        if isinstance(created_at, datetime):
            timestamp = created_at
        elif isinstance(created_at, str):
            try:
                timestamp = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00"),
                )
            except (ValueError, AttributeError):
                return {}, None

        if timestamp is None:
            return {}, None

        score = raw_recovery.get("score", {}) or {}

        normalized = {
            "user_id": user_id,
            "provider": self.provider_name,
            "timestamp": timestamp,
            "cycle_id": raw_recovery.get("cycle_id"),
            "sleep_id": raw_recovery.get("sleep_id"),
            "sleep_record_id": sleep_record_id,
            "data_source_id": data_source_id,
            "zone_offset": zone_offset,
            "recovery_score": score.get("recovery_score"),
            "resting_heart_rate": score.get("resting_heart_rate"),
            "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
            "spo2_percentage": score.get("spo2_percentage"),
            "skin_temp_celsius": score.get("skin_temp_celsius"),
            "raw": raw_recovery,
        }

        return (
            normalized,
            self._normalize_recovery_health_score(
                normalized,
                user_id,
            ),
        )

    def save_recovery_data(
        self,
        db: DbSession,
        user_id: UUID,
        normalized_recovery: dict[str, Any],
    ) -> int:
        """Upsert WHOOP recovery metrics with their resolved timezone."""
        if not normalized_recovery:
            return 0

        timestamp = normalized_recovery.get("timestamp")
        if not timestamp:
            return 0

        cycle_id = normalized_recovery.get("cycle_id")
        zone_offset = normalized_recovery.get("zone_offset")
        samples_to_create: list[TimeSeriesSampleCreate] = []

        for field_name, series_type in RECOVERY_SERIES.items():
            value = normalized_recovery.get(field_name)
            if value is None:
                continue

            samples_to_create.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    external_id=(f"{cycle_id}:{field_name}" if cycle_id is not None else None),
                    user_id=user_id,
                    source=self.provider_name,
                    provider=self.provider_name,
                    recorded_at=timestamp,
                    zone_offset=zone_offset,
                    value=Decimal(str(value)),
                    series_type=series_type,
                )
            )

        count = 0
        if samples_to_create:
            count = timeseries_service.bulk_create_samples(
                db,
                samples_to_create,
            )
            db.commit()

        return count

    def _get_recovery_sleep_record(
        self,
        db: DbSession,
        user_id: UUID,
        sleep_id: str,
    ) -> EventRecord | None:
        """Resolve or repair the EventRecord associated with a recovery."""
        existing = self.event_record_repo.get_by_external_id(
            db,
            user_id,
            sleep_id,
            source=self.provider_name,
        )

        if existing is not None and existing.zone_offset is not None:
            return existing

        raw_sleep = self.get_sleep_record(
            db,
            user_id,
            sleep_id,
        )
        if not raw_sleep:
            return existing

        return (
            self._save_sleep_bundle(
                db,
                user_id,
                raw_sleep,
            )
            or existing
        )

    def get_recovery_record(
        self,
        db: DbSession,
        user_id: UUID,
        cycle_id: str,
    ) -> dict[str, Any]:
        """Fetch recovery from /v2/cycle/{cycleId}/recovery."""
        endpoint = f"/v2/cycle/{cycle_id}/recovery"
        response = self._make_api_request(
            db,
            user_id,
            endpoint,
        )
        store_raw_payload(
            source="api_response",
            provider="whoop",
            payload=response,
            user_id=str(user_id),
            trace_id=endpoint,
        )
        return response if isinstance(response, dict) else {}

    def load_single_recovery(
        self,
        db: DbSession,
        user_id: UUID,
        sleep_id: str,
    ) -> int:
        """Process a v2 recovery webhook whose resource ID is a sleep UUID."""
        try:
            raw_sleep = self.get_sleep_record(
                db,
                user_id,
                sleep_id,
            )
            if not raw_sleep:
                return 0

            cycle_id = raw_sleep.get("cycle_id")
            if cycle_id is None:
                return 0

            sleep_record = self._save_sleep_bundle(
                db,
                user_id,
                raw_sleep,
            )
            if sleep_record is None or sleep_record.zone_offset is None:
                return 0

            raw_recovery = self.get_recovery_record(
                db,
                user_id,
                str(cycle_id),
            )
            if not raw_recovery:
                return 0

            normalized, health_score = self.normalize_recovery(
                raw_recovery,
                user_id,
                zone_offset=sleep_record.zone_offset,
                sleep_record_id=sleep_record.id,
                data_source_id=sleep_record.data_source_id,
            )
            if not normalized:
                return 0

            count = self.save_recovery_data(
                db,
                user_id,
                normalized,
            )

            if health_score:
                health_score_service.bulk_create(
                    db,
                    [health_score],
                )
                db.commit()

            return count
        except Exception as e:
            db.rollback()
            log_structured(
                self.logger,
                "warning",
                f"Failed to save recovery for sleep {sleep_id}: {e}",
                provider="whoop",
                task="load_single_recovery",
            )
            return 0

    def load_and_save_recovery(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """Load and repair WHOOP recovery data idempotently."""
        raw_data = self.get_recovery_data(
            db,
            user_id,
            start_time,
            end_time,
        )
        total_count = 0
        health_scores: list[HealthScoreCreate] = []

        for item in raw_data:
            try:
                sleep_id = item.get("sleep_id")
                if sleep_id is None:
                    continue

                sleep_record = self._get_recovery_sleep_record(
                    db,
                    user_id,
                    str(sleep_id),
                )
                if sleep_record is None or sleep_record.zone_offset is None:
                    continue

                normalized, health_score = self.normalize_recovery(
                    item,
                    user_id,
                    zone_offset=sleep_record.zone_offset,
                    sleep_record_id=sleep_record.id,
                    data_source_id=sleep_record.data_source_id,
                )
                if not normalized:
                    continue

                total_count += self.save_recovery_data(
                    db,
                    user_id,
                    normalized,
                )
                if health_score:
                    health_scores.append(health_score)
            except Exception as e:
                db.rollback()
                log_structured(
                    self.logger,
                    "warning",
                    f"Failed to save recovery data: {e}",
                    provider="whoop",
                    task="load_and_save_recovery",
                    user_id=str(user_id),
                )

        if health_scores:
            health_score_service.bulk_create(
                db,
                health_scores,
            )
            db.commit()

        return total_count

    # -------------------------------------------------------------------------
    # Activity Samples
    # -------------------------------------------------------------------------

    def get_activity_samples(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch activity samples from Whoop API."""
        return []

    def normalize_activity_samples(
        self,
        raw_samples: list[dict[str, Any]],
        user_id: UUID,
    ) -> dict[str, list[dict[str, Any]]]:
        """Normalize activity samples into categorized data."""
        return {}

    # -------------------------------------------------------------------------
    # Daily Activity Statistics
    # -------------------------------------------------------------------------

    def get_daily_activity_statistics(
        self,
        db: DbSession,
        user_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch aggregated daily activity statistics."""
        return []

    def normalize_daily_activity(
        self,
        raw_stats: dict[str, Any],
        user_id: UUID,
    ) -> dict[str, Any]:
        """Normalize daily activity statistics to our schema."""
        return {}
