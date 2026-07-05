"""CLI for idempotently rebuilding athlete daily features."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from app.database import SessionLocal
from app.services.athlete_daily_feature_service import (
    athlete_daily_feature_service,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Idempotently rebuild athlete_daily_feature rows for one user and inclusive local-date range.")
    )
    parser.add_argument(
        "--user-id",
        required=True,
        type=UUID,
    )
    parser.add_argument(
        "--start-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=date.fromisoformat,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date")

    with SessionLocal() as db:
        rows = athlete_daily_feature_service.rebuild_range(
            db,
            args.user_id,
            args.start_date,
            args.end_date,
        )

    result = {
        "user_id": str(args.user_id),
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "rows_rebuilt": len(rows),
        "first_row_date": (rows[0].local_date.isoformat() if rows else None),
        "last_row_date": (rows[-1].local_date.isoformat() if rows else None),
    }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
