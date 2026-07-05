"""Tests for post-sync athlete daily-feature rebuilding."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.integrations.celery.tasks.sync_vendor_data_task import (
    _rebuild_daily_features_after_sync,
)


def test_rebuild_daily_features_after_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_at = datetime(
        2026,
        6,
        1,
        tzinfo=timezone.utc,
    )
    end_at = datetime(
        2026,
        7,
        5,
        tzinfo=timezone.utc,
    )
    captured = {}

    def fake_rebuild(
        db: object,
        user_id: UUID,
        source_start: datetime,
        source_end: datetime,
    ) -> list[object]:
        captured["db"] = db
        captured["user_id"] = user_id
        captured["source_start"] = source_start
        captured["source_end"] = source_end
        return [object(), object()]

    monkeypatch.setattr(
        "app.integrations.celery.tasks.sync_vendor_data_task.athlete_daily_feature_service.rebuild_for_source_window",
        fake_rebuild,
    )

    db = MagicMock()
    user_id = uuid4()

    result = _rebuild_daily_features_after_sync(
        db,
        user_id,
        start_at,
        end_at,
    )

    assert result["success"] is True
    assert result["rows_rebuilt"] == 2
    assert captured["db"] is db
    assert captured["user_id"] == user_id
    assert captured["source_start"] == start_at
    assert captured["source_end"] == end_at
