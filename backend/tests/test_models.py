"""Schema-level checks that need no database.

The privacy constraints in section 7 are the point here: the assertions below
fail if someone ever adds a raw URL or IP column.
"""

from __future__ import annotations

from app.models import Base, Job, JobStatus, UsageDaily

FORBIDDEN_COLUMN_FRAGMENTS = ("url", "ip", "cookie", "password", "token", "email")
ALLOWED_HASHED_COLUMNS = {"url_hash", "ip_hash"}


def test_jobs_table_stores_no_raw_identifiers():
    for column in Job.__table__.columns:
        if column.name in ALLOWED_HASHED_COLUMNS:
            continue
        lowered = column.name.lower()
        assert not any(fragment in lowered for fragment in FORBIDDEN_COLUMN_FRAGMENTS), (
            f"jobs.{column.name} looks like it stores an identifier in the clear"
        )


def test_identifier_columns_are_hashed_forms():
    assert "ip_hash" in Job.__table__.columns
    assert "url_hash" in Job.__table__.columns
    assert Job.__table__.columns["url_hash"].type.length == 32
    assert Job.__table__.columns["ip_hash"].type.length == 64


def test_usage_table_has_no_per_user_dimension():
    columns = set(UsageDaily.__table__.columns.keys())
    assert columns == {"id", "day", "platform", "content_type", "action", "count"}


def test_baseline_covers_every_mapped_table():
    """Guards against a model added without a migration."""
    assert set(Base.metadata.tables) == {"jobs", "usage_daily"}


def test_terminal_states_are_classified_correctly():
    assert JobStatus.COMPLETED.is_terminal
    assert JobStatus.FAILED.is_terminal
    assert JobStatus.EXPIRED.is_terminal
    assert JobStatus.CANCELLED.is_terminal
    assert not JobStatus.QUEUED.is_terminal
    assert not JobStatus.RUNNING.is_terminal


def test_status_check_constraint_lists_every_status():
    """The database must reject a status the enum doesn't know about, so the
    constraint has to stay in step with JobStatus."""
    from sqlalchemy import CheckConstraint

    checks = [
        str(c.sqltext) for c in Job.__table__.constraints if isinstance(c, CheckConstraint)
    ]
    status_check = next(c for c in checks if "status" in c)
    for status in JobStatus:
        assert f"'{status.value}'" in status_check


def test_jobs_expiry_is_indexed_for_the_sweeper():
    index_columns = {tuple(i.columns.keys()) for i in Job.__table__.indexes}
    assert ("expires_at",) in index_columns
