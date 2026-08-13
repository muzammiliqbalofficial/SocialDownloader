"""Coverage is a correctness invariant, not an optimisation.

Spending a token deletes the stored object. Getting this wrong destroys a
user's file while they hold an incomplete copy, with no way to recover it. The
first test below is the scenario that a cumulative byte counter gets wrong.
"""

from __future__ import annotations

import pytest

from app.services.byte_coverage import MAX_INTERVALS, ByteCoverage, CoverageLedger

FIVE_MB = 5 * 1024 * 1024
HALF_MB = 512 * 1024


# --------------------------------------------------------------------------
# The scenario that motivates the whole module
# --------------------------------------------------------------------------


def test_repeated_first_chunk_never_counts_as_complete():
    """A resuming downloader on a flaky connection re-requests the same opening
    chunk ten times. Cumulative bytes reach the full object size; actual
    coverage is 10% of it. This must not spend the token."""
    coverage = ByteCoverage()
    for _ in range(10):
        coverage = coverage.add(0, HALF_MB)

    assert coverage.delivered_bytes() == HALF_MB
    assert coverage.covers(FIVE_MB) is False
    assert coverage.missing_before(FIVE_MB) == FIVE_MB - HALF_MB


def test_a_naive_byte_sum_would_have_been_satisfied():
    """Pins the distinction explicitly, so nobody reintroduces the counter."""
    naive_total = sum(HALF_MB for _ in range(10))
    assert naive_total >= FIVE_MB  # the wrong condition would have passed

    coverage = ByteCoverage()
    for _ in range(10):
        coverage = coverage.add(0, HALF_MB)
    assert coverage.covers(FIVE_MB) is False  # the right one does not


# --------------------------------------------------------------------------
# Cases that must spend
# --------------------------------------------------------------------------


def test_single_full_range_covers():
    assert ByteCoverage().add(0, FIVE_MB).covers(FIVE_MB) is True


def test_two_contiguous_halves_cover():
    """The ordinary resume: connection drops halfway, client asks for the rest."""
    coverage = ByteCoverage().add(0, HALF_MB).add(HALF_MB, FIVE_MB)
    assert coverage.covers(FIVE_MB) is True
    assert coverage.delivered_bytes() == FIVE_MB


def test_out_of_order_ranges_that_fully_cover_do_spend():
    coverage = (
        ByteCoverage()
        .add(3_000_000, FIVE_MB)
        .add(0, 1_000_000)
        .add(1_000_000, 3_000_000)
    )
    assert coverage.covers(FIVE_MB) is True


def test_overlapping_ranges_still_cover():
    coverage = ByteCoverage().add(0, 3_000_000).add(2_000_000, FIVE_MB)
    assert coverage.covers(FIVE_MB) is True
    assert coverage.delivered_bytes() == FIVE_MB


def test_delivering_more_than_the_object_size_covers():
    """Storage reporting a smaller size than was streamed must not deadlock the
    token."""
    assert ByteCoverage().add(0, FIVE_MB + 999).covers(FIVE_MB) is True


def test_many_small_contiguous_chunks_merge_into_one_interval():
    """A chunked stream produces one interval per chunk before merging; if
    adjacent intervals did not coalesce, an ordinary download would trip the
    interval cap."""
    coverage = ByteCoverage()
    for offset in range(0, 1_000_000, 8192):
        coverage = coverage.add(offset, min(offset + 8192, 1_000_000))

    assert len(coverage.intervals) == 1
    assert coverage.covers(1_000_000) is True


# --------------------------------------------------------------------------
# Cases that must not spend
# --------------------------------------------------------------------------


def test_a_single_byte_hole_prevents_spending():
    coverage = ByteCoverage().add(0, 2_000_000).add(2_000_001, FIVE_MB)
    assert coverage.covers(FIVE_MB) is False
    assert coverage.missing_before(FIVE_MB) == 1


def test_suffix_range_alone_does_not_cover():
    """`bytes=-1` delivers the final byte and nothing else."""
    coverage = ByteCoverage().add_http_range(FIVE_MB - 1, FIVE_MB - 1)
    assert coverage.covers(FIVE_MB) is False


def test_missing_tail_does_not_cover():
    assert ByteCoverage().add(0, FIVE_MB - 1).covers(FIVE_MB) is False


def test_missing_head_does_not_cover():
    assert ByteCoverage().add(1, FIVE_MB).covers(FIVE_MB) is False


def test_empty_coverage_does_not_cover():
    assert ByteCoverage().covers(FIVE_MB) is False


def test_zero_length_ranges_are_ignored():
    coverage = ByteCoverage().add(100, 100).add(500, 200)
    assert coverage.intervals == ()
    assert coverage.covers(FIVE_MB) is False


# --------------------------------------------------------------------------
# Overflow: bounded storage must fail safe
# --------------------------------------------------------------------------


def test_exceeding_the_interval_cap_refuses_to_spend():
    """Fail safe: the user keeps their file and the TTL sweeper reclaims the
    object, rather than a truncated record spending the token."""
    coverage = ByteCoverage()
    # Deliberately non-adjacent so nothing merges.
    for index in range(MAX_INTERVALS + 5):
        coverage = coverage.add(index * 100, index * 100 + 10)

    assert coverage.overflowed is True
    assert coverage.covers(FIVE_MB) is False


def test_overflow_is_latched_even_if_a_full_range_arrives_later():
    coverage = ByteCoverage()
    for index in range(MAX_INTERVALS + 5):
        coverage = coverage.add(index * 100, index * 100 + 10)

    coverage = coverage.add(0, FIVE_MB)
    assert coverage.overflowed is True
    assert coverage.covers(FIVE_MB) is False


def test_just_under_the_cap_still_works():
    coverage = ByteCoverage()
    for index in range(MAX_INTERVALS - 1):
        coverage = coverage.add(index * 100, index * 100 + 10)
    assert coverage.overflowed is False


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_zero_byte_object_is_trivially_covered():
    assert ByteCoverage().covers(0) is True
    assert ByteCoverage().missing_before(0) == 0


def test_negative_start_is_clamped():
    assert ByteCoverage().add(-500, FIVE_MB).covers(FIVE_MB) is True


def test_http_range_conversion_is_inclusive():
    """`bytes=0-499` is 500 bytes, not 499."""
    coverage = ByteCoverage().add_http_range(0, 499)
    assert coverage.delivered_bytes() == 500
    assert coverage.covers(500) is True


# --------------------------------------------------------------------------
# Serialisation -- this round-trips through Redis on every chunk boundary
# --------------------------------------------------------------------------


def test_round_trip_preserves_coverage():
    original = ByteCoverage().add(0, 1000).add(2000, 3000)
    restored = ByteCoverage.from_json(original.to_json())
    assert restored == original


def test_round_trip_preserves_overflow():
    coverage = ByteCoverage()
    for index in range(MAX_INTERVALS + 5):
        coverage = coverage.add(index * 100, index * 100 + 10)
    assert ByteCoverage.from_json(coverage.to_json()).overflowed is True


def test_missing_record_is_empty_not_complete():
    assert ByteCoverage.from_json(None).covers(FIVE_MB) is False
    assert ByteCoverage.from_json("").covers(FIVE_MB) is False


@pytest.mark.parametrize("corrupt", ["{", "null", '{"i": "nonsense"}', '{"i": [[1]]}'])
def test_corrupt_records_fail_closed(corrupt):
    """A garbled record must never spend a token. Re-downloading is
    recoverable; a deleted object is not."""
    restored = ByteCoverage.from_json(corrupt)
    assert restored.covers(FIVE_MB) is False


def test_serialised_form_stays_small():
    coverage = ByteCoverage().add(0, 5_000_000).add(6_000_000, 7_000_000)
    assert len(coverage.to_json()) < 100


# --------------------------------------------------------------------------
# The ledger: what an interrupted response actually delivered
# --------------------------------------------------------------------------


def test_ledger_records_only_bytes_actually_written():
    """The response promised the whole object; the connection died at 1 MB.
    Only the megabyte that arrived may be recorded."""
    ledger = CoverageLedger(start_offset=0)
    for _ in range(128):
        ledger.record(8192)

    coverage = ledger.apply_to(ByteCoverage())
    assert coverage.delivered_bytes() == 1024 * 1024
    assert coverage.covers(FIVE_MB) is False


def test_ledger_offsets_a_resumed_response():
    first = CoverageLedger(start_offset=0)
    first.record(HALF_MB)
    coverage = first.apply_to(ByteCoverage())

    second = CoverageLedger(start_offset=HALF_MB)
    second.record(FIVE_MB - HALF_MB)
    coverage = second.apply_to(coverage)

    assert coverage.covers(FIVE_MB) is True


def test_ledger_that_wrote_nothing_changes_nothing():
    coverage = ByteCoverage().add(0, 1000)
    assert CoverageLedger(start_offset=1000).apply_to(coverage) == coverage


def test_two_contiguous_halves_spend_exactly_once():
    """Once covered, further deliveries do not un-cover it -- the endpoint must
    be able to treat the first crossing as the single spend event."""
    coverage = ByteCoverage().add(0, HALF_MB)
    assert coverage.covers(FIVE_MB) is False

    coverage = coverage.add(HALF_MB, FIVE_MB)
    assert coverage.covers(FIVE_MB) is True

    coverage = coverage.add(0, HALF_MB)
    assert coverage.covers(FIVE_MB) is True
