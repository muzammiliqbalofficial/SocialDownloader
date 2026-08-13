"""Token lifecycle: the grace window, and the absence of spend on signed URLs.

The grace window exists because coverage is fed by bytes written to the ASGI
send channel, which are not bytes the client received. These tests pin the
behaviour that assumption buys us.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ErrorCode
from app.services.byte_coverage import ByteCoverage
from app.services.download_token import (
    Delivery,
    DownloadToken,
    TokenPolicy,
    TokenState,
)

SIZE = 5 * 1024 * 1024
T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
POLICY = TokenPolicy(ttl=timedelta(minutes=15), grace=timedelta(minutes=3), max_attempts=10)


def make_token(**overrides) -> DownloadToken:
    defaults = dict(
        job_id="job-1",
        object_key="jobs/job-1/output.mp4",
        size_bytes=SIZE,
        issued_at=T0,
    )
    return DownloadToken(**{**defaults, **overrides})


def stream_fully(token: DownloadToken, now: datetime) -> DownloadToken:
    verdict = token.can_stream(now, POLICY)
    assert verdict.allowed
    token = token.begin_attempt(verdict)
    token = token.record_delivery(0, SIZE)
    return token.maybe_spend(now)


# --------------------------------------------------------------------------
# The grace window
# --------------------------------------------------------------------------


def test_full_coverage_spends_the_token():
    token = stream_fully(make_token(), T0)
    assert token.state is TokenState.SPENT
    assert token.spent_at == T0


def test_object_survives_the_grace_window_after_spend():
    """The whole point: coverage says the bytes left our process, not that the
    client received them. A client that dropped near the end must still be
    able to come back."""
    token = stream_fully(make_token(), T0)

    assert token.is_object_deletable(T0, POLICY) is False
    assert token.is_object_deletable(T0 + timedelta(minutes=2), POLICY) is False
    assert token.is_object_deletable(T0 + timedelta(minutes=3), POLICY) is True


def test_restream_inside_the_grace_window_is_allowed():
    token = stream_fully(make_token(), T0)

    verdict = token.can_stream(T0 + timedelta(minutes=1), POLICY)
    assert verdict.allowed is True
    # A fresh accounting: this attempt must not extend anything.
    assert verdict.reset_coverage is True


def test_restream_after_the_grace_window_is_refused():
    token = stream_fully(make_token(), T0)

    verdict = token.can_stream(T0 + timedelta(minutes=4), POLICY)
    assert verdict.allowed is False
    assert verdict.error is ErrorCode.CONTENT_REMOVED


def test_restreaming_does_not_extend_the_deletion_deadline():
    """Otherwise a client could hold an object open indefinitely by
    re-fetching it, and the 15-minute guarantee would be advisory."""
    token = stream_fully(make_token(), T0)
    original_deadline = token.deletion_due_at(POLICY)

    later = T0 + timedelta(minutes=2)
    verdict = token.can_stream(later, POLICY)
    token = token.begin_attempt(verdict)
    token = token.record_delivery(0, SIZE).maybe_spend(later)

    assert token.spent_at == T0
    assert token.deletion_due_at(POLICY) == original_deadline


def test_restream_starts_from_empty_coverage():
    token = stream_fully(make_token(), T0)
    verdict = token.can_stream(T0 + timedelta(minutes=1), POLICY)
    restreamed = token.begin_attempt(verdict)

    assert restreamed.coverage == ByteCoverage()
    assert restreamed.missing_bytes() == SIZE


def test_restream_counts_against_the_attempts_cap():
    token = stream_fully(make_token(), T0)
    assert token.attempts == 1

    verdict = token.can_stream(T0 + timedelta(minutes=1), POLICY)
    assert token.begin_attempt(verdict).attempts == 2


def test_ttl_caps_the_grace_window():
    """Constraint 5 is a hard ceiling: grace may shorten an object's life,
    never extend it past 15 minutes."""
    late_spend = T0 + timedelta(minutes=14)
    token = stream_fully(make_token(), late_spend)

    assert token.deletion_due_at(POLICY) == T0 + timedelta(minutes=15)
    assert token.is_object_deletable(T0 + timedelta(minutes=15), POLICY) is True


def test_unspent_token_lives_until_ttl():
    token = make_token()
    assert token.deletion_due_at(POLICY) == T0 + timedelta(minutes=15)
    assert token.is_object_deletable(T0 + timedelta(minutes=14), POLICY) is False


# --------------------------------------------------------------------------
# Interrupted transfers
# --------------------------------------------------------------------------


def test_partial_delivery_does_not_spend():
    token = make_token()
    verdict = token.can_stream(T0, POLICY)
    token = token.begin_attempt(verdict).record_delivery(0, 1000).maybe_spend(T0)

    assert token.state is TokenState.STREAMING
    assert token.spent_at is None
    assert token.is_object_deletable(T0 + timedelta(minutes=5), POLICY) is False


def test_resume_across_two_attempts_spends_once():
    token = make_token()

    first = token.can_stream(T0, POLICY)
    token = token.begin_attempt(first).record_delivery(0, 2_000_000).maybe_spend(T0)
    assert token.spent_at is None

    resume_at = T0 + timedelta(seconds=30)
    second = token.can_stream(resume_at, POLICY)
    assert second.reset_coverage is False, "an unspent token keeps its coverage"
    token = token.begin_attempt(second).record_delivery(2_000_000, SIZE)
    token = token.maybe_spend(resume_at)

    assert token.state is TokenState.SPENT
    assert token.spent_at == resume_at
    assert token.attempts == 2


def test_maybe_spend_is_idempotent():
    token = stream_fully(make_token(), T0)
    assert token.maybe_spend(T0 + timedelta(minutes=1)) == token


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_expired_token_is_refused():
    verdict = make_token().can_stream(T0 + timedelta(minutes=16), POLICY)
    assert verdict.allowed is False
    assert verdict.error is ErrorCode.CONTENT_REMOVED


def test_attempts_cap_is_enforced():
    token = make_token(attempts=10)
    verdict = token.can_stream(T0, POLICY)
    assert verdict.allowed is False
    assert verdict.error is ErrorCode.RATE_LIMITED


def test_begin_attempt_refuses_a_denied_verdict():
    token = make_token(attempts=10)
    verdict = token.can_stream(T0, POLICY)
    with pytest.raises(ValueError):
        token.begin_attempt(verdict)


# --------------------------------------------------------------------------
# Signed URLs have no spend semantics at all
# --------------------------------------------------------------------------


def test_signed_token_can_never_reach_spent():
    """Coverage is an artifact of proxying, and the signed path is exactly the
    one that does not proxy. There is no observation that could spend it."""
    token = make_token().issue_signed()
    assert token.delivery is Delivery.SIGNED
    assert token.state is TokenState.ISSUED_SIGNED

    # Even given a full-coverage record, spend must not happen.
    forced = DownloadToken(
        job_id="job-1",
        object_key="k",
        size_bytes=SIZE,
        issued_at=T0,
        delivery=Delivery.SIGNED,
        state=TokenState.ISSUED_SIGNED,
        coverage=ByteCoverage().add(0, SIZE),
    )
    assert forced.maybe_spend(T0).state is TokenState.ISSUED_SIGNED
    assert forced.maybe_spend(T0).spent_at is None


def test_signed_token_refuses_coverage_recording():
    """Loudly, so nobody later unifies the two paths by accident."""
    token = make_token().issue_signed()
    with pytest.raises(ValueError, match="no coverage signal"):
        token.record_delivery(0, SIZE)


def test_signed_token_is_not_streamable():
    token = make_token().issue_signed()
    verdict = token.can_stream(T0, POLICY)
    assert verdict.allowed is False
    assert verdict.error is ErrorCode.INTERNAL_ERROR


def test_signed_object_is_deleted_by_ttl_only():
    token = make_token().issue_signed()
    assert token.deletion_due_at(POLICY) == T0 + timedelta(minutes=15)
    assert token.is_object_deletable(T0 + timedelta(minutes=14), POLICY) is False
    assert token.is_object_deletable(T0 + timedelta(minutes=15), POLICY) is True


def test_signed_token_reports_no_missing_bytes():
    assert make_token().issue_signed().missing_bytes() == 0


# --------------------------------------------------------------------------
# Policy is configurable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("grace_minutes", [0, 1, 3, 10])
def test_grace_window_is_configurable(grace_minutes):
    policy = TokenPolicy(grace=timedelta(minutes=grace_minutes))
    token = make_token()
    verdict = token.can_stream(T0, POLICY)
    token = token.begin_attempt(verdict).record_delivery(0, SIZE).maybe_spend(T0)

    assert token.deletion_due_at(policy) == T0 + timedelta(minutes=grace_minutes)


def test_zero_grace_deletes_immediately_on_spend():
    """Available, but the default is 3 minutes for the reason in the module
    docstring: with zero grace, a client whose tail was still in a socket
    buffer loses the file."""
    policy = TokenPolicy(grace=timedelta(0))
    token = stream_fully(make_token(), T0)
    assert token.is_object_deletable(T0, policy) is True
