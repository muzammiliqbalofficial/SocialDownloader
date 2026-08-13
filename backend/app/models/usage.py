"""Aggregate usage counters for the public stats page.

Rows are per (date, platform, content_type, action) -- never per user, per
session or per IP. Nothing here can be joined back to an individual.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("day", "platform", "content_type", "action", name="uq_usage_daily_bucket"),
        CheckConstraint("count >= 0", name="count_non_negative"),
        # The stats page queries by date range.
        Index("ix_usage_daily_day", "day"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UsageDaily {self.day} {self.platform}/{self.action}={self.count}>"
