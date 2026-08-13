# Project brief

**This document is the source of truth.** It is the original brief, amended
with every decision taken since. Where a decision changed the brief, the text
below reflects the *decision*, not the original wording, and the change is
recorded in the [Decision log](#decision-log) at the end.

If a conversation and this document disagree, this document wins. If you make a
new decision, amend the relevant section here and add a log entry in the same
commit.

Last amended: 2026-08-13 (end of Phase 2).

---

## 1. Objective

A web application that lets a user paste a public URL from YouTube, Facebook,
Instagram, LinkedIn or Snapchat and download the media, plus extract metadata
and derived assets that mainstream downloader sites do not offer.

The differentiator is **not** the download itself — it is the extraction layer
(captions, thumbnails, metadata, transcripts, AI summaries). That layer is the
product's core value, not an add-on.

## 2. Non-negotiable constraints

Hard requirements. Do not implement anything that violates them, and do not
silently work around them.

1. **Public content only.** Reject any URL that requires an authenticated
   session belonging to another user. Return a clear, actionable error.
2. **No DRM circumvention.** If a stream is DRM-protected (Widevine, FairPlay,
   PlayReady), fail immediately with an explicit error. Never attempt to
   decrypt.
3. **No credential storage.** Never ask for, log, or persist platform
   passwords. If a feature needs a session, accept a user-supplied cookie
   string, hold it in memory for that single job only, and never write it to
   disk or database.
4. **No paywalled or subscriber-only content.**
5. **Ephemeral files.** Downloaded media must be deleted within 15 minutes.
6. **Rate limiting.** Per-IP limits from day one. No unauthenticated bulk
   endpoints.
7. **Compliance pages required.** Terms of Use, a DMCA/takedown policy with a
   contact route, and a visible notice that the user is responsible for
   respecting copyright and each platform's terms of service.
8. **robots.txt respect** for any HTML scraping path.

> **Amended (D-001).** Constraint 5 originally read "Prefer streaming directly
> to the client so nothing is written to disk at all." That is unachievable
> given the worker/API process split — see §6. The 15-minute deletion
> requirement is unchanged and is enforced by the TTL sweeper.

## 3. Tech stack

**Backend**

- Python 3.11+, FastAPI, Uvicorn
- `yt-dlp` as the primary extraction engine (pinned, but expect frequent bumps
  — it breaks often; see the weekly canary in §12)
- `gallery-dl` as fallback for image carousels and galleries
- `playwright` (chromium, headless) only for HTML-based text extraction where
  no API/extractor exists
- `httpx` for async HTTP
- Redis for the job queue and rate limiting
- `arq` for async job workers
- PostgreSQL + SQLAlchemy 2.x + Alembic
- `pydantic-settings` for config

**AI layer**

- Groq API (`whisper-large-v3`) for transcription
- Google Gemini API for summarization, hashtag generation, caption rewriting
- Both optional: absent key means the feature is hidden in the UI and the
  endpoint returns 503 with a clear message. The core downloader must never
  depend on them.

**Frontend**

- Next.js 16 (App Router), TypeScript, Tailwind CSS 4
- `shadcn/ui` for components
- TanStack Query for server state and job polling
- No global state library

**Infra**

- Docker + docker-compose for local dev (api, worker, redis, postgres, web)
- Target deployment: Google Cloud Run (backend) + Vercel (frontend), with a GCS
  bucket for temporary large files
- Backend fully stateless — no local filesystem assumptions beyond `/tmp`

> **Amended (D-005).** Originally Next.js 14 and Tailwind 3. Moved to Next 16 +
> Tailwind 4 + current shadcn generators: pinning an out-of-support major on a
> greenfield project is unjustifiable debt.
>
> **Amended (D-006).** `asyncpg` only. `psycopg2-binary` is dropped — Alembic
> runs through the same async engine as the app, so a second driver earns
> nothing.
>
> **Note (D-007).** TypeScript is pinned at 5.9.3, not the newer 7.x. TS 7 is
> the native port and the surrounding plugin ecosystem (including
> `eslint-config-next/typescript`) is not yet validated against it. Revisit
> once the ecosystem catches up.

## 4. Platform support matrix

Implemented as an explicit, data-driven capability registry
(`app/platforms/registry.py`), not scattered `if` statements. The frontend
fetches this registry and renders only the capabilities actually available for
the detected URL.

| Platform  | Video          | Audio | Thumbnail     | Post text              | Metadata | Notes                                                        |
| --------- | -------------- | ----- | ------------- | ---------------------- | -------- | ------------------------------------------------------------ |
| YouTube   | Yes            | Yes   | Yes (all res) | Description + chapters | Full     | Includes Shorts. Subtitles via `--write-subs`.               |
| Instagram | Yes            | Yes   | Yes           | Caption + hashtags     | Partial  | Reels/posts/carousels. Public only. Stories need cookies — best-effort. |
| Facebook  | Yes            | Yes   | Yes           | Post text              | Partial  | Public videos and Reels only.                                |
| LinkedIn  | Yes            | Yes   | Yes           | **Full post text**     | Partial  | Native video only. `og:` meta tags first, Playwright fallback. Fragile. |
| Snapchat  | Spotlight only | Yes   | Yes           | Limited                | Minimal  | Stories not reliably accessible. Disabled by default (D-004). |

For every best-effort or fragile capability: degrade gracefully with a specific
error message. Never show a generic "failed" state.

> **Amended (D-004).** Snapchat is implemented in Phase 5 but ships **disabled
> behind an env var**. When disabled it is omitted from the capability registry
> entirely — no greyed-out tab, no failing tab, no mention in the UI.

## 5. Feature spec

### Tier 1 — table stakes

- URL paste with automatic platform + content-type detection
- Format and quality picker (resolution, codec, filesize) rendered from the
  actual available formats, not a hardcoded list
- MP4 video download
- MP3 / M4A audio-only extraction with a bitrate selector
- Progress indication driven by real job state, not a fake animation
- Clipboard paste button, mobile-first responsive layout
- Clear, specific error states per failure mode

### Tier 2 — differentiators (the reason this project exists)

- **Post text extractor** — full caption or post body for Instagram, LinkedIn
  and Facebook, with one-click copy. Preserve line breaks and emoji. Show a
  character count.
- **Hashtag and mention extractor** — parsed into separate copyable chips, plus
  "copy all hashtags".
- **Thumbnail / cover downloader** — every available resolution for Instagram
  Reels, YouTube videos and Facebook videos, as a grid with dimensions
  labelled. Include the maxres YouTube variant.
- **Full carousel download** — every image in a multi-image Instagram post,
  bundled as a streamed ZIP.
- **Subtitle / caption download** — SRT and VTT for YouTube, including
  auto-generated tracks, with language selection.
- **Metadata JSON export** — title, author, upload date, duration, view/like/
  comment counts where public, dimensions, codecs, all format variants. One
  download button, one copy button.
- **Batch mode** — up to 10 URLs, queued, producing a single ZIP. Rate-limited
  and session-gated. Built last.
- **Frame grabber** — extract a still frame at a user-specified timestamp as
  PNG.

> **Removed (D-003).** "HD profile picture downloader" is cut entirely — not
> the gated variant either. It was the most abuse-adjacent item in the list,
> the one platforms most actively rate-limit, and it does not serve the
> extraction-layer thesis. **Do not reintroduce it in any later phase.**

### Tier 3 — AI layer

- **Auto-transcription** — extracted audio to Groq Whisper, returning plain
  text plus timestamped SRT. Input duration capped (start at 10 minutes);
  reject longer media with a clear message.
- **Video summary** — transcript to Gemini, returning a short abstract plus 3–5
  key bullet points.
- **Caption repurposer** — platform-adapted variants of an extracted caption
  (LinkedIn version of an Instagram caption and vice versa). Obvious in the UI
  that output is AI-generated.
- **Suggested hashtags** — derived from transcript or caption content.

Every Tier 3 feature runs as a separate, explicitly user-triggered job. Never
automatically on download — they cost money and add latency.

## 6. Architecture

```
Client → POST /api/analyze (fast, synchronous, ~2–5s)
       ← platform, content type, available formats, capabilities, metadata, extracted text
Client → POST /api/jobs  (creates an async job)
       ← job_id
Client → GET  /api/jobs/{id}  (polled by TanStack Query until terminal state)
       ← status, progress, error, download_token
Client → GET  /api/download/{download_token}
       ← streams the file, single-use token, 15-minute TTL
```

Decisions to honour:

- `/api/analyze` never downloads media. Metadata extraction only.
- Every `yt-dlp` invocation runs in the worker with a hard timeout and a
  subprocess boundary, never in the API request path.
- Wrap `yt-dlp` in a single adapter module. The rest of the codebase must not
  know it exists — this is the component most likely to be replaced.
- Structured JSON logging with a request ID. Never log full URLs at info level;
  log platform and content type instead.

### Storage and delivery (D-001)

The original brief said large files stream through the API and that the backend
should avoid disk. Those cannot both hold: the API and worker are separate
processes (separate containers locally, separate services on Cloud Run), so a
file in the worker's `/tmp` is not readable by the API.

Resolved as follows:

- **The worker always writes to object storage**, through a `storage.py`
  abstraction with two backends: a shared volume in local compose, GCS in
  production. There is no code path where the worker keeps output only on its
  own local disk.
- **Default delivery: `/api/download/{token}` proxy-streams from storage** and
  deletes the object immediately once the stream completes. This preserves the
  single-use token guarantee and never exposes a bucket path.
- **Above 200 MB: return a signed URL with a 5-minute TTL.** Signed URLs are
  the exception, not the default, because a signed URL can be shared and
  replayed until it expires and it leaks the object path. Accepting that
  weaker guarantee above the threshold is a deliberate trade for Cloud Run
  request-timeout and egress cost. **Document this trade-off at the call
  site.**
- The 200 MB threshold is a **cost/timeout optimisation, not a correctness
  boundary**. Both paths must be correct on their own.

### Progress reporting (D-002)

Job progress is **polled**, not streamed. TanStack Query polls
`GET /api/jobs/{id}` every 2 seconds. SSE was considered and declined for v1:
on Cloud Run each open connection pins an instance, and it brings reconnect
handling, proxy-buffering edge cases and its own test surface. At 2-second
polls a 60-second download shows ~30 progress steps, which is adequate.

**Keep the job model SSE-ready**: `progress` is a monotonically increasing int
(0–100) and `status` is an enum. SSE can then be added later without changing
the client contract.

## 7. Data model

Minimal and privacy-conscious.

- `jobs` — id (uuid), platform, content_type, action, requested_format, status
  enum, progress int, error_code, error_message, created_at, started_at,
  completed_at, expires_at, ip_hash (salted SHA-256, never the raw IP).
  **The source URL is not stored** beyond a truncated, salted digest for
  deduplication.
- `usage_daily` — day, platform, content_type, action, count. Aggregate
  counters for a stats page. No per-user rows.

No user accounts in v1. If batch mode needs gating, use a signed anonymous
session cookie.

## 8. Frontend spec

- Single-page primary flow: paste → analyze → capability tabs → action.
- After analysis, render tabs only for capabilities the registry reports as
  available: `Video`, `Audio`, `Thumbnail`, `Text`, `Subtitles`, `Metadata`,
  `AI Tools`.
- Copy-to-clipboard must give visible confirmation.
- Dark mode by default, with a toggle.
- Clean, restrained aesthetic. The visual bar in this category is low, so a
  deliberately designed interface is itself a differentiator. Avoid the
  ad-cluttered look of existing downloader sites. No interstitials, no fake
  download buttons.
- Loading skeletons, not spinners, for the analyze step.
- Full keyboard accessibility and correct ARIA labels on copy and download
  actions.

## 9. Repo structure

```
/backend
  /app
    main.py
    config.py
    /api          routes: analyze, jobs, download, health, registry
    /platforms    registry.py, base.py, youtube.py, instagram.py, facebook.py, linkedin.py, snapchat.py
    /extractors   ytdlp_adapter.py, gallerydl_adapter.py, html_scraper.py
    /services     transcription.py, summarizer.py, media_ops.py, storage.py
    /workers      tasks.py
    /models       db models + pydantic schemas
    /core         ratelimit.py, security.py, logging.py, errors.py
  /tests
  alembic/
  Dockerfile
/frontend
  /app  /components  /lib  /hooks
  Dockerfile
/docs
  BRIEF.md
docker-compose.yml
README.md
```

## 10. Error taxonomy

A single error enum shared by backend and frontend. Every failure maps to one
of these, each with a user-facing message and a suggested action:

`INVALID_URL`, `UNSUPPORTED_PLATFORM`, `UNSUPPORTED_CONTENT_TYPE`,
`PRIVATE_CONTENT`, `LOGIN_REQUIRED`, `DRM_PROTECTED`, `GEOBLOCKED`,
`CONTENT_REMOVED`, `RATE_LIMITED`, `EXTRACTOR_OUTDATED`, `DURATION_EXCEEDED`,
`FILESIZE_EXCEEDED`, `AI_UNAVAILABLE`, `UPSTREAM_TIMEOUT`, `INTERNAL_ERROR`.

`EXTRACTOR_OUTDATED` matters: when `yt-dlp` breaks after a platform change, the
user sees "this platform changed recently, we're updating support" rather than
a stack trace.

> **Implementation (D-008).** `backend/app/core/errors.py` is the single source
> of truth. `backend/scripts/gen_error_codes.py` generates
> `frontend/lib/errors.ts` from it, and CI fails if the checked-in file is
> stale. Do not hand-edit the TypeScript file.

## 11. Build phases

In order. Each phase ends in a running, testable state, and reports back before
the next begins.

1. **Skeleton** ✅ — docker-compose brings the full stack online. Health
   endpoint, config loading, structured logging, Alembic baseline, CI-ready
   pytest.
2. **Analyze pipeline** ✅ — platform registry, yt-dlp adapter, `/api/analyze`
   end to end for YouTube only. Full error taxonomy wired. Per-IP rate limiting
   landed here rather than in Phase 3, because constraint 6 says day one and
   analyze is the expensive unauthenticated endpoint.
3. **Download pipeline** — job queue, worker, storage abstraction, streaming
   download endpoint, single-use tokens, TTL cleanup, per-IP rate limiting.
   Video and audio for YouTube.
4. **Frontend core** — paste, analyze, format picker, download, progress, error
   states. Tier 1 complete for YouTube.
5. **Platform expansion** — Instagram, then Facebook, then LinkedIn, then
   Snapchat. One at a time, each with integration tests and honest capability
   flags. LinkedIn and Snapchat are expected to be partial; document exactly
   what works. Snapchat ships disabled (D-004).
6. **Tier 2 features** — text extractor, hashtag chips, thumbnail grid,
   carousel ZIP, subtitles, metadata export, frame grabber. Batch mode last.
7. **Tier 3 AI layer** — transcription, then summary, then caption repurposer.
   All behind feature flags with graceful degradation.
8. **Hardening** — compliance pages, DMCA route, stats page, cleanup cron,
   Cloud Run deployment config, README with setup and legal considerations.

## 12. Testing

- Unit tests for URL parsing, the platform registry and error mapping — no
  network.
- Integration tests for extractors using recorded fixtures (committed JSON
  snapshots), so the suite passes offline and in CI.
- A separate, opt-in `live` marker that hits real URLs. This is the canary for
  platform breakage; it is expected to fail periodically.
- Frontend: component tests for copy and download flows.
- Target 80% coverage on `/platforms` and `/api`. Do not chase coverage on
  adapter glue code.

> **Added (D-009).** A weekly scheduled GitHub Actions workflow bumps `yt-dlp`
> to latest, runs the `live` suite against it, and opens a PR on success or an
> issue on failure. `yt-dlp` breakage is the single most likely cause of a
> production outage for this product, so it needs an automated canary rather
> than someone noticing.

## 13. Definition of done

- `docker compose up` gives a working app with no manual steps beyond copying
  `.env.example`.
- README documents setup, the capability matrix **as actually implemented**,
  known limitations per platform, and legal considerations.
- Every capability shown in the UI actually works, or is explicitly labelled
  best-effort.
- No stored credentials, no persisted media beyond the TTL, no raw IPs in the
  database.

---

## Decision log

Newest last. Each entry records what changed and why, so a later session does
not relitigate it.

### D-001 — Worker always writes to object storage; proxy-stream by default

**Date:** 2026-08-13 · **Phase:** 1 → 3 · **Status:** accepted

The brief simultaneously required streaming large files through the API and
forbade extraction in the API process. With api and worker as separate
processes, a file written to the worker's `/tmp` is unreachable from the API,
so the two requirements were contradictory.

Adopted: the worker always writes to object storage via `services/storage.py`
(shared volume in dev, GCS in prod). `/api/download/{token}` proxy-streams from
storage and deletes the object once the stream completes. Above 200 MB, return
a signed URL with a 5-minute TTL.

Signed URLs are deliberately **not** the default: a signed URL can be shared
and replayed until it expires, and it exposes the bucket path, which breaks the
single-use token guarantee. Above the threshold that weaker guarantee is
accepted in exchange for avoiding Cloud Run request timeouts and double egress.
The 200 MB line is a cost/timeout optimisation, not a correctness boundary —
both paths must be independently correct.

### D-002 — Polling, not SSE, for job progress

**Date:** 2026-08-13 · **Phase:** 3 · **Status:** declined (SSE)

SSE gives smoother progress, but on Cloud Run each open connection pins an
instance, and it adds reconnect handling, proxy-buffering edge cases and its
own tests. At 2-second polls a 60-second download shows ~30 steps, which is
adequate.

Build polling as specified. Keep the job model SSE-ready — monotonic int
`progress` plus a `status` enum — so SSE can be added later without changing
the client contract.

### D-003 — Cut the HD profile picture downloader

**Date:** 2026-08-13 · **Phase:** 6 · **Status:** accepted

Removed entirely, including the "author of an already-analyzed post" variant,
which adds complexity for negligible value. It was the most abuse-adjacent
feature in the spec and does not serve the extraction-layer thesis. Removed
from §5 so no later phase reintroduces it.

### D-004 — Snapchat ships disabled behind an env var

**Date:** 2026-08-13 · **Phase:** 5 · **Status:** accepted

Per the matrix, Snapchat is Spotlight-only with minimal metadata and unreliable
Stories access. Shipping a tab that mostly fails costs more trust than an
absent platform. Implement it in Phase 5, ship it disabled, and when disabled
omit it from the capability registry entirely — not a greyed-out tab, not a
failing tab.

### D-005 — Next.js 16 + Tailwind 4, not Next 14 + Tailwind 3

**Date:** 2026-08-13 · **Phase:** 1 · **Status:** accepted

Next 14 is two majors behind and off mainline security support, and current
shadcn generators target Tailwind 4. Pinning an out-of-support major on a
greenfield project is unjustifiable debt. Migrated during Phase 1, while the
frontend was still one page and the change was nearly free.

### D-006 — asyncpg only

**Date:** 2026-08-13 · **Phase:** 1 · **Status:** accepted

Alembic runs through the same async engine as the application, so
`psycopg2-binary` adds a second driver, a second failure mode and a second
thing to pin, for nothing.

### D-007 — TypeScript held at 5.9.3

**Date:** 2026-08-13 · **Phase:** 1 · **Status:** accepted (engineer's call)

TypeScript 7.x is the native port. The plugin ecosystem, including
`eslint-config-next/typescript`, is not yet validated against it. Holding at
5.9.3, which is current on the 5.x line and fully supported. Revisit when the
ecosystem catches up. Note this is the one place the stack is deliberately not
on the newest major, and the reasoning is the opposite of D-005: there the risk
was staying on an unsupported version, here the risk is moving to an unproven
one.

### D-008 — The error taxonomy is generated, not duplicated

**Date:** 2026-08-13 · **Phase:** 1 · **Status:** accepted

§10 requires one enum shared by both sides. Hand-syncing drifts, so
`app/core/errors.py` is the source of truth and `scripts/gen_error_codes.py`
generates `frontend/lib/errors.ts`. A test fails when the checked-in file is
stale. The same enforcement-over-convention approach guards §7: a test fails if
anyone adds a `jobs` column whose name suggests a cleartext URL, IP, cookie or
token.

### D-009 — Weekly yt-dlp canary

**Date:** 2026-08-13 · **Phase:** 1 · **Status:** accepted

`yt-dlp` breakage is the most likely cause of a production outage here. A
scheduled weekly workflow bumps it to latest, runs the `live` suite, and opens
a PR on success or an issue on failure — an automated canary rather than
someone noticing that downloads stopped working.

### D-010 — `/api/analyze` runs yt-dlp as a subprocess from the API process

**Date:** 2026-08-13 · **Phase:** 2 · **Status:** accepted (engineer's call)

§6 says `/api/analyze` is fast and synchronous (2–5s) *and* that every yt-dlp
invocation runs in the worker, never in the API request path. Taken literally
those conflict: routing analyze through the job queue would make it
asynchronous and cost a poll round-trip on the most latency-sensitive call in
the product.

Read the constraint as being about **blast radius, not process identity**. The
danger is unbounded, unkillable work inside the web process. So analyze spawns
`python -m yt_dlp` as a child process, awaited with `asyncio.wait_for`, and on
expiry the whole process *group* is killed (yt-dlp spawns ffmpeg; killing only
the parent orphans it). The event loop never blocks, the work is bounded and
kill-able, and yt-dlp's global state stays out of our address space.

Downloads remain worker-only, as specified — that is where the long-running,
disk-touching work happens.

If analyze latency or instance CPU becomes a problem on Cloud Run, the fix is
to move analyze behind the queue and accept the round-trip, not to run yt-dlp
in-process.

**Amended: the subprocess must be bounded.** Approving the design without a
concurrency cap would have shipped a guaranteed OOM. Each subprocess is a
separate OS process whose resident memory is charged to the container:

| Figure | Value | Source |
| --- | --- | --- |
| Floor | **45 MiB** | Measured `ru_maxrss` of the child on Python 3.11 with the network blocked, so it exits before parsing a format list — interpreter plus yt-dlp imports and nothing more. |
| Budget | **80 MiB** | Floor plus headroom for a real extraction: a large `formats` array, TLS buffers, and the JSON document held in memory while it is written to stdout. |

Cloud Run's default `containerConcurrency` is 80. At 80 MiB each that is
**~6.4 GiB** of concurrent extraction on an instance we would plausibly pay
1–2 GiB for. No functional test surfaces it, because every test issues one
request at a time.

So `MAX_CONCURRENT_EXTRACTIONS` (default 4) gates the subprocess behind an
`asyncio.Semaphore`, held only around the child process itself. A caller that
waits longer than `EXTRACTION_QUEUE_WAIT_SECONDS` (2s) gets `429 RATE_LIMITED`
with `Retry-After` rather than joining an unbounded queue: a fast honest
rejection beats a request that dies at the load balancer.

The bound lives in the application, not only in deployment config, so it holds
however the service is run. `containerConcurrency` is set explicitly to 8 —
**it must not exceed `MAX_CONCURRENT_EXTRACTIONS` by more than about 2x**, the
multiple being the queue depth behind the semaphore. Full arithmetic in
`deploy/cloudrun/README.md`; the in-process half is pinned by
`tests/test_extractor_concurrency.py`, which fires 20 concurrent calls and
asserts observed peak concurrency never exceeds the limit.

### D-011 — Error messages never enumerate platforms

**Date:** 2026-08-13 · **Phase:** 2 · **Status:** accepted

The `UNSUPPORTED_PLATFORM` catalog entry originally read "Supported platforms
are YouTube, Instagram, Facebook, LinkedIn and Snapchat." With only YouTube
implemented and Snapchat disabled, that message was false, and it would go
stale again every time a platform was toggled.

The static catalog entry no longer names platforms. The live list is derived
from the registry (`supported_platform_names`) and passed as the error
`detail`, so a user is never told we support something that does not work.
This is §13's "every capability shown in the UI actually works" applied to
error copy, which is otherwise easy to overlook.

### D-012 — Next.js `AGENTS.md` / `CLAUDE.md` stay committed

**Date:** 2026-08-13 · **Phase:** 2 · **Status:** accepted after review

`next dev` wrote `frontend/AGENTS.md` and `frontend/CLAUDE.md` into the repo.
They were committed without justification, which is a fair thing to challenge:
an unattributed file that instructs coding agents is an injection surface.

Provenance was then established from the installed package, not from memory:

- **Generator:** `node_modules/next/dist/server/lib/generate-agent-files.js`
  builds the block; the shipped source produces our files byte-for-byte,
  including `CLAUDE.md` being exactly `@AGENTS.md`.
- **Documentation:** `node_modules/next/dist/docs/01-app/02-guides/ai-agents.md`
  ("How to set up your Next.js project for AI coding agents"), section
  *Existing projects*: "On Next.js 16.3 or later, run `next dev`. When an AI
  coding agent is detected in the environment and no managed block is present,
  Next.js auto-generates `AGENTS.md` and `CLAUDE.md` at the project root." The
  page reproduces the exact block text we have, including the line advising
  that committing it keeps the tree clean.
- **Trigger:** `start-server.js` logs "Generated … for AI agents. Set
  `agentRules: false` in next.config to disable."
- **Scope:** the block only tells an agent to read the version-matched docs
  bundled in `node_modules`. It grants nothing our dependency does not already
  have — we execute that package's code on every build.

**Why committed rather than gitignored.** Gitignoring would not remove the
file. `next dev` recreates it, agents still read it, and changes to it would
land silently on disk where nobody reviews them. That makes the surface
invisible, not absent — strictly worse. Committed, any change to the managed
block arrives as a reviewable diff.

**The actual off switch** is `agentRules: false` in `next.config.mjs`, which
stops generation entirely. Not taken: the content is verified, the docs are
genuinely useful given Next 16 postdates most training data, and the
review-the-diff property is worth more than the file's absence.

Revisit if a future Next version puts anything in that block beyond "read the
bundled docs" — which is precisely the change a committed file makes visible.

### D-013 — Download completion is interval coverage, never a byte count

**Date:** 2026-08-13 · **Phase:** 3 · **Status:** accepted after a design bug

The first Phase 3 draft spent a download token when a cumulative `delivered`
byte counter reached the object size. That is wrong, and the failure is not
exotic:

> A 5 MB object. A resuming downloader on a flaky mobile connection requests
> `bytes=0-499999` ten times. Cumulative delivered reaches 5 MB, the condition
> is satisfied, the token spends and the object is deleted. The client never
> received a single byte past offset 500000.

**Cumulative bytes are not coverage.** The draft's `bytes=-1` guard caught one
instance of this, not the class of bug.

Completion is a set-cover question and is now tracked as merged half-open
intervals (`app/services/byte_coverage.py`). A token spends only when the
intervals cover `[0, size_bytes)` completely. Repeating a chunk merges into
what is already recorded and therefore adds nothing; out-of-order ranges that
genuinely tile the object do complete it; a one-byte hole anywhere prevents
spending.

The interval list is capped at 64. Exceeding it latches an `overflowed` flag
and coverage refuses permanently, so the object survives until the TTL sweeper
reclaims it. A corrupt record fails the same way. Both directions favour the
user keeping their file over us reclaiming storage.

**Why this is an invariant rather than an optimisation:** deleting a user's
object while they hold an incomplete copy is unrecoverable from the client's
side. They cannot request the missing bytes, and the job they paid for is gone.
It is the worst failure this service can have, and it is worth failing safe in
every ambiguous case to avoid it.

### D-014 — No `fake-gcs-server` in the dev compose stack

**Date:** 2026-08-13 · **Phase:** 3 · **Status:** accepted

Proposed so the GCS path could be exercised locally; rejected. Dev uses the
local volume backend, which cannot sign URLs and therefore falls back to
proxy-streaming — a genuine behavioural difference that the parametrised
storage suite asserts rather than hides. Adding another service to a compose
stack that is only now being brought up for the first time is the wrong
sequencing.

The GCS backend is covered in the shared suite with a mocked client and
verified against a real bucket in staging. The limitation is stated plainly: a
mocked backend proves our call sequence, not Google's behaviour.

Do not reintroduce `fake-gcs-server` without a specific failure it would have
caught.

### D-015 — Signed-URL egress is budgeted in bytes at issuance

**Date:** 2026-08-13 · **Phase:** 3 · **Status:** accepted risk

Once a signed URL is issued, GCS serves the bytes and our per-IP limiter never
sees the traffic. Mitigations:

- TTL cut from 5 minutes to **2**.
- The limiter is charged **at issuance for the object's full size**, so a
  client cannot mint URLs cheaply and fan the egress out elsewhere.
- A per-IP **daily byte budget** specific to signed-URL issuance, separate from
  request-count limits. Counts are the wrong unit: ten 2 GB URLs and ten 2 MB
  URLs are identical under a count limit and three orders of magnitude apart on
  the bill.

**Residual risk, unfixable on our side:** a signed URL is a bearer token.
For two minutes, anyone holding it can fetch the object. That is inherent to
the mechanism, and it is why signed URLs are the exception above 200 MB rather
than the default delivery path (D-001).
