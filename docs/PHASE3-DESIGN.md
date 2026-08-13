# Phase 3 design — download pipeline

Settled before any code is written. Four points were called out as needing a
decision up front; each is answered here with the failure it is protecting
against, because that is what makes the design reviewable.

Scope: job queue, worker, `services/storage.py`, `/api/download/{token}`,
single-use tokens, TTL cleanup. Video and audio for YouTube.

---

## A. Token semantics on interrupted streams

**The failure being avoided.** A token consumed when the stream *starts* means
a dropped connection costs the user the whole job. On a mobile network that is
not an edge case, it is Tuesday. They paid the extraction, waited for the job,
and received nothing — with no way to retry short of running the job again.

### States

A token is a Redis key, `dl:{token}`, holding a small JSON record and carrying
the TTL. There is no "consumed" boolean; there are three states:

| State | Meaning | Next action allowed |
| --- | --- | --- |
| `ready` | Issued, never streamed | Stream it |
| `streaming` | At least one transfer started, none completed | Stream it again (resume or restart) |
| `spent` | The client demonstrably has the whole object | Nothing; 404 `CONTENT_REMOVED` |

`streaming` is the important addition. **The token is marked in-use, not
spent.** Retries are permitted for the whole TTL.

### Record

```jsonc
{
  "job_id":        "uuid",
  "object_key":    "jobs/<uuid>/output.mp4",
  "size_bytes":    123456789,   // authoritative, from storage after the job
  "content_type":  "video/mp4",
  "filename":      "…",         // Content-Disposition
  "state":         "ready",
  "attempts":      0,
  "delivered":     0            // cumulative bytes across all attempts
}
```

### What counts as "complete"

The token is spent when the client has provably received the whole object, not
when a response happens to finish. Two conditions, both required:

1. The response ran to completion — the byte count actually written to the
   client equals what that response promised.
2. `delivered >= size_bytes`, where `delivered` is `INCRBY`-accumulated across
   every attempt.

The cumulative counter is what makes resume work. A client that fetches
0–50 MB, drops, then resumes 50 MB–end has `delivered == size_bytes` and the
token spends correctly at the end of the second request. A client that
requests only the last byte does not, because `delivered` is 1.

Only after spending is the stored object deleted. Deleting on first touch is
exactly the bug this section exists to prevent.

### Interruption

A dropped connection surfaces as the response generator being closed early.
The handler catches it, leaves the state at `streaming`, records the partial
`delivered` count, and does **not** delete the object. The next request with
the same token resumes.

### Abuse bounds

Retries are not unlimited:

- `attempts` is capped (start at 10). Beyond that: 429 `RATE_LIMITED`.
- The TTL is unchanged at 15 minutes and is never extended by a retry, so a
  token cannot be kept alive by touching it.
- Egress is bounded by `attempts × size_bytes` in the worst case, which is why
  the cap exists at all.

### Tests

- Interrupted stream → token still usable, object still present.
- Resume via Range across two requests → `delivered` reaches `size_bytes`,
  token spends, object deleted.
- Suffix range alone (`bytes=-1`) → does **not** spend the token.
- Replay after spend → 404 `CONTENT_REMOVED`.
- Attempt cap → 429.
- TTL expiry mid-retry → 404, object swept.

---

## B. HTTP Range on the proxy-stream path

Built in from the start. Retrofitting Range into a working streaming response
means rewriting the response path, and the resume behaviour in section A
depends on it.

**Semantics**

- `Accept-Ranges: bytes` on every download response, including the 200.
- Single range → `206 Partial Content`, with `Content-Range: bytes s-e/size`
  and `Content-Length: e-s+1`.
- Open (`bytes=500-`) and suffix (`bytes=-500`) forms both supported.
- Unsatisfiable (`s >= size`) → `416` with `Content-Range: bytes */size`.
- Multi-range → served as a normal `200` with the full body. Correct per
  RFC 9110, and `multipart/byteranges` is not worth implementing for a
  download endpoint no browser will request it from.
- Malformed `Range` header → ignored, full `200`. Also per spec.

This requires the storage layer to expose ranged reads, which is why
`open_range` is in the protocol below rather than a plain `open`.

**Tests:** each form above; a resume sequence that reconstructs a
byte-identical file; `416`; malformed header; and that `Content-Length` matches
the bytes actually emitted (a mismatch hangs clients).

---

## C. The 200 MB threshold uses an estimate — what happens when it is wrong

**The failure being avoided.** `yt-dlp` reports `filesize` for some formats and
`filesize_approx` for others, and both are frequently absent or wrong. A job
estimated at 150 MB that finishes at 400 MB must not fail. Nothing about
delivery should hinge on a number we already know to be unreliable.

**Resolution: the estimate never makes the routing decision.**

| Number | Where it comes from | What it is allowed to do |
| --- | --- | --- |
| Estimate | `filesize` / `filesize_approx` at analyze time | Show a size in the picker (already flagged `filesize_is_estimate`); reject absurd jobs early against `MAX_FILESIZE_MB` |
| Actual | `storage.size(key)` after the job finishes | **Decide proxy-stream vs signed URL.** Nothing else |

So the 150 MB → 400 MB case is not an error path at all: the job completes, the
actual size is measured, 400 MB exceeds `STREAM_THRESHOLD_MB`, and the token
resolves to a signed URL. The user sees a working download. This is what
D-001's "cost optimisation, not a correctness boundary" means in practice —
both paths are correct for any size, so being wrong about which one to use
costs latency, never success.

Two guards remain, both against *actual* size:

- Above `MAX_FILESIZE_MB` (2 GiB) the job fails with `FILESIZE_EXCEEDED`. To
  avoid discovering this after burning the bandwidth, the worker also passes
  `--max-filesize` to yt-dlp so the download aborts early.
- A job whose actual size is unknowable (storage reports nothing) takes the
  signed-URL path, because the proxy path cannot set `Content-Length` without
  it.

**Tests:** estimate under / actual over → signed URL; estimate over / actual
under → proxy stream; no estimate at all → routes on actual; actual over the
hard cap → `FILESIZE_EXCEEDED` and the object is deleted.

---

## D. `storage.py` — one contract, two backends, one test suite

**The failure being avoided.** The dev backend is a shared compose volume and
the prod backend is GCS. If they are tested separately they drift, and the
drift is discovered in production.

### Protocol

```python
class Storage(Protocol):
    async def put(self, key: str, source: Path | AsyncIterator[bytes],
                  *, content_type: str) -> int: ...      # returns bytes written
    async def size(self, key: str) -> int | None: ...    # None when absent
    async def exists(self, key: str) -> bool: ...
    async def open_range(self, key: str, start: int = 0,
                         end: int | None = None) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> bool: ...        # idempotent
    async def signed_url(self, key: str, *, ttl_seconds: int,
                         filename: str) -> str | None: ...  # None if unsupported
    def supports_signed_urls(self) -> bool: ...
```

Implementations: `LocalVolumeStorage` (dev; the compose volume shared between
api and worker) and `GCSStorage` (prod).

`signed_url` returning `None` is deliberate. The local backend cannot sign, so
in dev an over-threshold job falls back to proxy-streaming. That difference is
real and must be visible, so it is in the contract rather than hidden behind an
exception — and it is asserted in the shared suite.

### The shared suite

One parametrised test class, identical assertions, run against both backends:

```python
@pytest.fixture(params=["local", "gcs"])
def storage(request): ...   # gcs skipped unless GCS_TEST_BUCKET is set
```

Assertions that must hold identically: round-trip integrity; `size` matches
bytes written; `size`/`exists` on a missing key return `None`/`False` rather
than raising; every Range form from section B, including `start=0`,
`end=size-1`, single-byte, and past-the-end; `delete` is idempotent; `delete`
then `exists` is `False`; keys containing slashes and unicode; concurrent reads
of the same key; and zero-byte objects.

Backend-specific behaviour is confined to two tests — `supports_signed_urls`,
and that a signed URL is time-limited — and everything else is shared. CI runs
`local` always; `gcs` runs when `GCS_TEST_BUCKET` is configured, and
`fake-gcs-server` is added to compose so the GCS path is exercised locally
without a real bucket.

---

## Endpoint shape

```
POST /api/jobs            -> { job_id }              rate limited per IP
GET  /api/jobs/{id}       -> { status, progress, error, download_token? }
GET  /api/download/{tok}  -> 200 / 206 / 302-to-signed-URL
```

`progress` stays a monotonic int and `status` an enum, per D-002, so SSE can be
added later without changing the client contract.

## Open questions for Phase 3

1. **Signed-URL egress is unmetered by our rate limiter.** Once the URL is
   handed out, GCS serves it directly and our per-IP limits do not apply for
   its 5-minute life. Mitigation is the short TTL plus the attempt cap; worth
   confirming that is acceptable before the first deploy.
2. **`fake-gcs-server` in compose** adds a service to the dev stack. Reasonable,
   but it is another moving part in a stack that has only just been verified —
   flagging rather than assuming.
