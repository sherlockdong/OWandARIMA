"""Scope health score sleep links by category and provider.

Revision ID: d53dc90fa7a9
Revises: 32f41ea801e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d53dc90fa7a9"
down_revision: str | None = "32f41ea801e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_health_score_sleep_record",
        table_name="health_score",
    )
    op.create_index(
        "uq_health_score_sleep_record_category_provider",
        "health_score",
        ["sleep_record_id", "category", "provider"],
        unique=True,
        postgresql_where=sa.text("sleep_record_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_health_score_sleep_record_category_provider",
        table_name="health_score",
    )
    op.create_index(
        "uq_health_score_sleep_record",
        "health_score",
        ["sleep_record_id"],
        unique=True,
        postgresql_where=sa.text("sleep_record_id IS NOT NULL"),
    )
