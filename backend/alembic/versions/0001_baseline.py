"""Baseline schema: jobs and usage_daily.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("requested_format", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Salted SHA-256 of the client IP. The raw address is never persisted.
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        # Truncated salted digest of the normalised source URL, for dedup only.
        sa.Column("url_hash", sa.String(length=32), nullable=True),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','expired','cancelled')",
            name="status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_expires_at", "jobs", ["expires_at"])
    op.create_index("ix_jobs_ip_hash_created_at", "jobs", ["ip_hash", "created_at"])
    op.create_index("ix_jobs_url_hash", "jobs", ["url_hash"])

    op.create_table(
        "usage_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("count", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("count >= 0", name="count_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_usage_daily"),
        sa.UniqueConstraint(
            "day", "platform", "content_type", "action", name="uq_usage_daily_bucket"
        ),
    )
    op.create_index("ix_usage_daily_day", "usage_daily", ["day"])


def downgrade() -> None:
    op.drop_index("ix_usage_daily_day", table_name="usage_daily")
    op.drop_table("usage_daily")
    op.drop_index("ix_jobs_url_hash", table_name="jobs")
    op.drop_index("ix_jobs_ip_hash_created_at", table_name="jobs")
    op.drop_index("ix_jobs_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
