"""Download token lifecycle: what a token permits, and when its object dies.

Pure decision logic. No Redis, no HTTP, no storage — Phase 3 persists these
records and wires them to the endpoint; this module decides.

Two boundaries are the reason this exists as its own module.

**We cannot observe client receipt.** `ByteCoverage` is fed by bytes written to
the ASGI send channel, and those are not bytes the client got: uvicorn's socket
buffer and Cloud Run's frontend proxy both accept bytes in flight. A client
dropping near the end can therefore produce complete `[0, size)` coverage while
missing the tail. The interval arithmetic is exact; **its input is optimistic**,
and no amount of interval precision fixes that.

So spend is decoupled from deletion. Spending closes the token to fresh use,
but the object is retained for a **grace window**, during which the same token
may stream again. The grace window is what absorbs the overcounting. This is an
explicit assumption, not a proof: coverage shows the bytes left our process, not
that they arrived.

**The signed-URL path has no coverage signal at all.** Above the streaming
threshold GCS serves the bytes directly and we never see them, so spend
semantics do not exist there. Such tokens are `Delivery.SIGNED`, are never
eligible for coverage, and are reclaimed by TTL alone. Coverage is an artifact
of proxying, and proxying is precisely what that path avoids.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.errors import ErrorCode
from app.services.byte_coverage import ByteCoverage


class Delivery(StrEnum):
    """How the object reaches the client. Determines whether spend exists."""

    PROXY = "proxy"
    SIGNED = "signed"


class TokenState(StrEnum):
    READY = "ready"
    STREAMING = "streaming"
    # Coverage completed. The object is retained for the grace window; the
    # token may still stream during it.
    SPENT = "spent"
    # A signed URL was handed out. Terminal: no streaming, no spend, TTL only.
    ISSUED_SIGNED = "issued_signed"


@dataclass(frozen=True)
class TokenPolicy:
    """Injected rather than read from settings, so the rules are testable and
    the module stays pure."""

    ttl: timedelta = timedelta(minutes=15)
    # Retention after spend. Absorbs the gap between "written to the socket"
    # and "received by the client".
    grace: timedelta = timedelta(minutes=3)
    max_attempts: int = 10


@dataclass(frozen=True)
class StreamVerdict:
    allowed: bool
    error: ErrorCode | None = None
    detail: str | None = None
    # True when this attempt should start from empty coverage: a stream after
    # spend is a fresh accounting and must not extend the deletion deadline.
    reset_coverage: bool = False


@dataclass(frozen=True)
class DownloadToken:
    job_id: str
    object_key: str
    size_bytes: int
    issued_at: datetime
    delivery: Delivery = Delivery.PROXY
    state: TokenState = TokenState.READY
    attempts: int = 0
    coverage: ByteCoverage = field(default_factory=ByteCoverage)
    # Set once, on first spend. Never advanced by a later re-stream, or a
    # client could hold the object open indefinitely by re-fetching.
    spent_at: datetime | None = None

    # --- deadlines -----------------------------------------------------

    def expires_at(self, policy: TokenPolicy) -> datetime:
        return self.issued_at + policy.ttl

    def deletion_due_at(self, policy: TokenPolicy) -> datetime:
        """When the stored object may be removed.

        Grace expiry or TTL, whichever comes first. The TTL is a hard ceiling
        (constraint 5: media deleted within 15 minutes), so the grace window
        can shorten an object's life but never extend it.
        """
        ttl_deadline = self.expires_at(policy)
        if self.spent_at is None:
            return ttl_deadline
        return min(self.spent_at + policy.grace, ttl_deadline)

    def is_object_deletable(self, now: datetime, policy: TokenPolicy) -> bool:
        return now >= self.deletion_due_at(policy)

    # --- streaming -----------------------------------------------------

    def can_stream(self, now: datetime, policy: TokenPolicy) -> StreamVerdict:
        if self.delivery is Delivery.SIGNED:
            # Not a refusal the user ever sees: the endpoint redirects instead
            # of streaming. Encoded so that a future caller which tries to
            # stream a signed token fails loudly rather than silently.
            return StreamVerdict(
                allowed=False,
                error=ErrorCode.INTERNAL_ERROR,
                detail="Signed-URL tokens are not streamed.",
            )

        if now >= self.expires_at(policy):
            return StreamVerdict(
                allowed=False,
                error=ErrorCode.CONTENT_REMOVED,
                detail="This download link has expired.",
            )

        if self.is_object_deletable(now, policy):
            return StreamVerdict(
                allowed=False,
                error=ErrorCode.CONTENT_REMOVED,
                detail="This download has already completed and the file has been removed.",
            )

        if self.attempts >= policy.max_attempts:
            return StreamVerdict(
                allowed=False,
                error=ErrorCode.RATE_LIMITED,
                detail=f"This link has been retried {policy.max_attempts} times.",
            )

        # Inside the grace window after spend: allowed, but as a fresh
        # accounting so that re-covering cannot move the deletion deadline.
        return StreamVerdict(allowed=True, reset_coverage=self.state is TokenState.SPENT)

    def begin_attempt(self, verdict: StreamVerdict) -> DownloadToken:
        """Record the start of a transfer. Counts against the attempts cap
        whether or not the transfer completes -- that cap is the backstop
        against unbounded egress, and a client that drops repeatedly costs the
        same bandwidth as one that succeeds."""
        if not verdict.allowed:
            raise ValueError("begin_attempt called on a refused verdict")

        return replace(
            self,
            attempts=self.attempts + 1,
            state=TokenState.STREAMING if self.state is TokenState.READY else self.state,
            coverage=ByteCoverage() if verdict.reset_coverage else self.coverage,
        )

    # --- coverage ------------------------------------------------------

    def record_delivery(self, start: int, end: int) -> DownloadToken:
        """Record a delivered half-open byte range."""
        if self.delivery is Delivery.SIGNED:
            raise ValueError(
                "Signed-URL tokens have no coverage signal; GCS serves those bytes."
            )
        return replace(self, coverage=self.coverage.add(start, end))

    def maybe_spend(self, now: datetime) -> DownloadToken:
        """Transition to SPENT if coverage is complete. Idempotent, and never
        moves `spent_at` once set."""
        if self.delivery is Delivery.SIGNED:
            return self
        if self.spent_at is not None:
            return self
        if not self.coverage.covers(self.size_bytes):
            return self
        return replace(self, state=TokenState.SPENT, spent_at=now)

    def issue_signed(self) -> DownloadToken:
        """Mark the token as delivered by signed URL. Terminal."""
        return replace(self, delivery=Delivery.SIGNED, state=TokenState.ISSUED_SIGNED)

    # --- reporting -----------------------------------------------------

    @property
    def is_spent(self) -> bool:
        return self.state is TokenState.SPENT

    def missing_bytes(self) -> int:
        if self.delivery is Delivery.SIGNED:
            return 0
        return self.coverage.missing_before(self.size_bytes)
