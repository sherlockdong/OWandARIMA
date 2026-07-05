"""Tests for the athlete daily-feature rebuild CLI."""

from datetime import date
from uuid import UUID

from app.cli.rebuild_athlete_daily_features import build_parser


def test_cli_parses_required_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--user-id",
            "3a6cf026-1e78-4a54-b157-d3442ba7a9d1",
            "--start-date",
            "2026-05-01",
            "--end-date",
            "2026-07-05",
        ]
    )

    assert args.user_id == UUID("3a6cf026-1e78-4a54-b157-d3442ba7a9d1")
    assert args.start_date == date(2026, 5, 1)
    assert args.end_date == date(2026, 7, 5)
