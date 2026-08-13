"""Proof that a client actually received an entire object.

This exists because of one failure mode, and it is the worst one this service
has: **deleting a user's file while they hold an incomplete copy**. It is
unrecoverable from the client's side -- they cannot ask for the missing bytes
back, and the job they paid for is gone.

The naive version of this counts bytes delivered and spends the token when the
count reaches the object size. That is wrong, and not subtly:

    5 MB object. A resuming downloader on a flaky connection requests
    `bytes=0-499999` ten times. Cumulative delivered = 5 MB. The counter says
    "complete". The client has never seen a byte past offset 500000.

Cumulative bytes are not coverage. Coverage is a set-cover problem over
intervals, so that is what this module tracks.

Intervals are half-open `[start, end)` internally. HTTP `Range` is inclusive on
both ends, so `bytes=0-499` becomes `[0, 500)`; convert at the boundary with
`from_http_range` rather than by hand at each call site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

# A real client produces very few intervals: a straight download is one, and a
# resume is one per interruption, all of which merge as they meet. Sixty-four
# is far past any legitimate pattern and exists to bound the Redis value, not
# to accommodate normal use.
MAX_INTERVALS = 64

Interval = tuple[int, int]


def _merge(intervals: list[Interval]) -> list[Interval]:
    """Sort and coalesce. Adjacent intervals merge: [0,5) and [5,9) is [0,9)."""
    if not intervals:
        return []

    ordered = sorted(intervals)
    merged: list[Interval] = [ordered[0]]

    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        # `start <= last_end` rather than `<`: touching intervals are
        # contiguous coverage, and failing to join them would leak intervals
        # until the cap trips on a perfectly ordinary resumed download.
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


@dataclass(frozen=True)
class ByteCoverage:
    """Which byte ranges of an object a client has provably received."""

    intervals: tuple[Interval, ...] = ()
    # Latched when the interval count exceeds MAX_INTERVALS. Once set, the
    # record is no longer a complete account of what was delivered, so
    # `covers()` refuses for good. The consequence is that the object survives
    # until the TTL sweeper removes it -- the user keeps their file and we pay
    # for a few more minutes of storage, which is the right way round.
    overflowed: bool = False

    def add(self, start: int, end: int) -> ByteCoverage:
        """Record a delivered half-open range, returning a new coverage."""
        if end <= start:
            # Zero-length deliveries carry no information.
            return self
        if self.overflowed:
            return self

        merged = _merge([*self.intervals, (max(0, start), end)])

        if len(merged) > MAX_INTERVALS:
            return replace(self, overflowed=True)

        return replace(self, intervals=tuple(merged))

    def add_http_range(self, first_byte: int, last_byte: int) -> ByteCoverage:
        """Record an inclusive HTTP byte range, as it appears in Content-Range."""
        return self.add(first_byte, last_byte + 1)

    def covers(self, size: int) -> bool:
        """True only when `[0, size)` is completely accounted for."""
        if self.overflowed:
            return False
        if size <= 0:
            # A zero-byte object is trivially complete once any response for it
            # has finished; there is nothing to be missing.
            return True
        if not self.intervals:
            return False

        first_start, first_end = self.intervals[0]
        # A single merged interval starting at 0 is the only shape that can
        # cover a contiguous object; anything else has a hole by construction.
        return first_start == 0 and first_end >= size and len(self.intervals) == 1

    def delivered_bytes(self) -> int:
        """Distinct bytes delivered. Reporting only -- never a spend condition."""
        return sum(end - start for start, end in self.intervals)

    def missing_before(self, size: int) -> int:
        """How many bytes of `[0, size)` are still outstanding. For logging."""
        if size <= 0:
            return 0
        covered = sum(
            max(0, min(end, size) - min(start, size)) for start, end in self.intervals
        )
        return max(0, size - covered)

    # --- serialisation -------------------------------------------------
    # Stored beside the token in Redis. Compact on purpose: this value is read
    # and written on every chunk boundary of every download.

    def to_json(self) -> str:
        return json.dumps(
            {"i": [[s, e] for s, e in self.intervals], "o": self.overflowed},
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> ByteCoverage:
        if not raw:
            return cls()
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return cls(overflowed=True)
            intervals = tuple(
                (int(s), int(e)) for s, e in parsed.get("i", []) if int(e) > int(s)
            )
            return cls(intervals=tuple(_merge(list(intervals))), overflowed=bool(parsed.get("o")))
        except (ValueError, TypeError, KeyError, AttributeError):
            # A corrupt record must not spend a token. Start over: the client
            # re-downloads, which is recoverable; deleting their object is not.
            return cls(overflowed=True)


EMPTY = ByteCoverage()


@dataclass
class CoverageLedger:
    """Mutable accumulator for one in-flight response.

    A streaming response learns how many bytes it actually delivered only as it
    goes, and may stop early. The ledger tracks the high-water mark so an
    interrupted transfer records what genuinely reached the client rather than
    what was promised.
    """

    start_offset: int
    bytes_written: int = field(default=0)

    def record(self, chunk_length: int) -> None:
        self.bytes_written += chunk_length

    def apply_to(self, coverage: ByteCoverage) -> ByteCoverage:
        return coverage.add(self.start_offset, self.start_offset + self.bytes_written)
