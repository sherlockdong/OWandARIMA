"""Add check-in type to athlete check-ins.

Revision ID: 2c93ee51b44c
Revises: 7a8a0828fece
"""

from alembic import op
import sqlalchemy as sa


revision: str = "2c93ee51b44c"
down_revision: str | None = "7a8a0828fece"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "athlete_checkin",
        sa.Column("checkin_type", sa.String(length=32), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE athlete_checkin
            SET checkin_type = 'pre_event'
            WHERE checkin_type IS NULL
            """
        )
    )

    op.alter_column(
        "athlete_checkin",
        "checkin_type",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    op.create_check_constraint(
        "ck_athlete_checkin_type",
        "athlete_checkin",
        "checkin_type IN ('daily', 'pre_event', 'post_event')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_athlete_checkin_type",
        "athlete_checkin",
        type_="check",
    )
    op.drop_column("athlete_checkin", "checkin_type")
