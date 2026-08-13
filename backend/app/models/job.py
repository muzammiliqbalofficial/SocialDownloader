"""Job records.

Deliberately minimal (section 7). Note what is *absent*: no source URL, no raw
IP, no output filename. `url_hash` is a salted truncated digest used only for
deduplication and cannot be reversed to the original link.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.EXPIRED, JobStatus.CANCELLED}
)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_format: Mapped[str | None] = mapped_column(String(64))

    # Stored as a plain string rather than a native PG enum: adding a status to
    # a native enum requires a migration and a lock, and this column changes
    # more often than the schema should.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.QUEUED, server_default=JobStatus.QUEUED
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the produced media and its download token stop being valid.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Salted SHA-256 of the client IP -- never the address itself.
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    # Truncated salted digest of the normalised source URL, for dedup only.
    url_hash: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','expired','cancelled')",
            name="status_valid",
        ),
        Index("ix_jobs_status_created_at", "status", "created_at"),
        # The TTL sweeper scans on this every couple of minutes.
        Index("ix_jobs_expires_at", "expires_at"),
        # Per-IP rate-limit windows and abuse investigation.
        Index("ix_jobs_ip_hash_created_at", "ip_hash", "created_at"),
        # Dedup lookups for repeat requests of the same content.
        Index("ix_jobs_url_hash", "url_hash"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Job {self.id} {self.platform}/{self.action} {self.status}>"
