"""Tests for WHOOP webhook daily-feature rebuilding."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.schemas.providers.whoop import (
    WhoopWebhookNotificationType,
)
from app.services.providers.whoop.strategy import WhoopStrategy


@pytest.mark.parametrize(
    ("event_type", "loader_name"),
    [
        (
            WhoopWebhookNotificationType.SLEEP_UPDATED,
            "load_single_sleep",
        ),
        (
            WhoopWebhookNotificationType.RECOVERY_UPDATED,
            "load_single_recovery",
        ),
    ],
)
def test_sleep_related_webhooks_rebuild_daily_features(
    monkeypatch: pytest.MonkeyPatch,
    event_type: WhoopWebhookNotificationType,
    loader_name: str,
) -> None:
    handler = WhoopStrategy().webhooks
    user_id = uuid4()
    resource_id = "sleep-resource-id"
    db = MagicMock()
    end_datetime = datetime(
        2026,
        7,
        5,
        11,
        tzinfo=timezone.utc,
    )
    sleep_record = SimpleNamespace(
        end_datetime=end_datetime,
    )
    captured = {}

    monkeypatch.setattr(
        handler.data_247,
        loader_name,
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        handler.data_247.event_record_repo,
        "get_by_external_id",
        lambda *_args, **_kwargs: sleep_record,
    )

    def fake_rebuild(
        rebuild_db: object,
        rebuild_user_id: UUID,
        source_start: datetime,
        source_end: datetime,
    ) -> list[object]:
        captured["db"] = rebuild_db
        captured["user_id"] = rebuild_user_id
        captured["start"] = source_start
        captured["end"] = source_end
        return [object(), object()]

    monkeypatch.setattr(
        "app.services.providers.whoop.webhook_handler.athlete_daily_feature_service.rebuild_for_source_window",
        fake_rebuild,
    )

    result = handler._handle_updated(
        db,
        event_type,
        user_id,
        resource_id,
    )

    assert result["records_saved"] == 1
    assert result["daily_features_rebuilt"] == 2
    assert captured["db"] is db
    assert captured["user_id"] == user_id
    assert captured["start"] == end_datetime
    assert captured["end"] == end_datetime


def test_workout_webhook_does_not_rebuild_daily_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = WhoopStrategy().webhooks

    monkeypatch.setattr(
        handler.workouts,
        "load_single_workout",
        lambda *_args, **_kwargs: 1,
    )

    result = handler._handle_updated(
        MagicMock(),
        WhoopWebhookNotificationType.WORKOUT_UPDATED,
        uuid4(),
        "workout-resource-id",
    )

    assert result["records_saved"] == 1
    assert result["daily_features_rebuilt"] == 0
