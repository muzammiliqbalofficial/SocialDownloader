# SocialDownloader

A web app for extracting media **and everything around it** from publicly
accessible social media posts: captions, hashtags, thumbnails at every
resolution, subtitles, metadata, transcripts and AI summaries.

The download itself is the commodity part. The extraction layer is the product.

> **Status: Phase 2 of 8 (analyze pipeline).** URLs can be analyzed; nothing is
> downloadable yet — the job queue and download endpoint land in Phase 3. See
> [Implementation status](#implementation-status) for what actually works today
> — that section describes reality, not intent, and is updated at the end of
> each phase.

**[`docs/BRIEF.md`](docs/BRIEF.md) is the source of truth** for requirements,
architecture decisions and the decision log. Read it before changing anything
structural; amend it in the same commit as any new decision.

---

## Quick start

Requires Docker and Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

That is the whole setup. It brings up five services:

| Service    | URL                            | Purpose                              |
| ---------- | ------------------------------ | ------------------------------------ |
| `web`      | http://localhost:3000          | Next.js frontend                     |
| `api`      | http://localhost:8000          | FastAPI backend                      |
| `worker`   | —                              | arq job worker and the TTL sweeper   |
| `postgres` | localhost:5432                 | Job records and aggregate counters   |
| `redis`    | localhost:6379                 | Queue, rate limits, download tokens  |

Migrations run automatically in a one-shot `migrate` service before `api` and
`worker` start, so there is no window where the app is up against an unmigrated
schema.

Useful endpoints while developing:

- http://localhost:8000/api/health — component-level status, including the
  pinned yt-dlp version and whether ffmpeg is present
- http://localhost:8000/api/registry — the capability matrix the UI renders from
- http://localhost:8000/api/errors — the full error taxonomy
- http://localhost:8000/docs — OpenAPI (disabled in production)

Analyze a URL:

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=aqz-KE-bpKQ"}'
```

Run `make help` for the common tasks.

### Working without Docker

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                  # 103 tests, no services needed
.venv/bin/uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

---

## Implementation status

### Phase 1 — skeleton ✅

- `docker compose up` brings the full five-service stack online
- Config via `pydantic-settings`, with production refusing to boot on a
  placeholder IP salt
- Structured JSON logging with a request ID threaded through every record
- Alembic baseline migration (`jobs`, `usage_daily`), verified to apply,
  downgrade and round-trip against a real Postgres
- The complete error taxonomy, with the TypeScript catalog generated from the
  Python enum so the two cannot drift
- TTL sweeper deleting expired scratch media every two minutes
- 103 backend tests (89% coverage), CI on GitHub Actions

### Phase 2 — analyze pipeline ✅

- `POST /api/analyze` works end to end for YouTube (videos and Shorts):
  formats, thumbnails at every resolution, manual and auto-generated subtitle
  tracks, chapters, metadata, and description text parsed into hashtags and
  mentions
- Data-driven capability registry at `app/platforms/registry.py`, exposed on
  `GET /api/registry`. All five platforms have specs; only YouTube is marked
  implemented, and disabled platforms are omitted from the response entirely
- yt-dlp behind a single adapter, run as a killable subprocess under a hard
  timeout, with stderr mapped onto the error taxonomy
- Per-IP rate limiting on `/api/analyze` (constraint 6 says day one)
- DRM, live streams and age-gated content are refused before anything else
  happens

### Not built yet

Phases 3–8: the job queue and download pipeline, the frontend flow, platform
expansion beyond YouTube, the remaining Tier 2 features, the AI layer, and the
compliance pages. **Only YouTube is supported today** — the capability matrix
below is the target, not the current state.

Frontend stack is Next.js 16 (App Router), React 19, Tailwind 4 and shadcn/ui,
with TypeScript held at 5.9.x — see decision D-007 in the brief for why that
one is deliberately not the newest major.

### Target capability matrix

Each row will be marked with what actually works as its phase lands.

| Platform  | Video          | Audio | Thumbnail    | Post text          | Metadata | Notes                                            |
| --------- | -------------- | ----- | ------------ | ------------------ | -------- | ------------------------------------------------ |
| YouTube   | Planned        | ✓p    | ✓p (all res) | Description + chapters | Full  | Includes Shorts; subtitles including auto-generated |
| Instagram | Planned        | ✓p    | ✓p           | Caption + hashtags | Partial  | Public reels/posts/carousels. Stories need cookies — best-effort |
| Facebook  | Planned        | ✓p    | ✓p           | Post text          | Partial  | Public videos and reels only                     |
| LinkedIn  | Planned        | ✓p    | ✓p           | Full post text     | Partial  | Native video only; **fragile** — `og:` tags first, Playwright fallback |
| Snapchat  | Spotlight only | ✓p    | ✓p           | Limited            | Minimal  | Stories not reliably accessible; **ships disabled** behind an env var |

`✓p` = planned. Nothing in this table is implemented as of Phase 1. When
Snapchat is disabled it is omitted from the capability registry entirely, so no
greyed-out or failing tab appears in the UI.

---

## Architecture

```
Client → POST /api/analyze          metadata only, never downloads      (Phase 2)
       → POST /api/jobs             queues async work, returns job_id   (Phase 3)
       → GET  /api/jobs/{id}        polled until a terminal state       (Phase 3)
       → GET  /api/download/{token} single-use, 15-minute TTL           (Phase 3)
```

Decisions worth knowing before changing things:

- **`yt-dlp` lives behind one adapter module.** It is the component most likely
  to need replacing, and the rest of the codebase must not know it exists.
- **Extraction never runs in the request path.** Every `yt-dlp` invocation
  happens in the worker, in a subprocess, under a hard timeout.
- **The backend is stateless** beyond `/tmp`. Cloud Run recycles instances
  freely; nothing may assume local disk persists.
- **Platform capabilities are a data-driven registry**, not scattered
  conditionals. The frontend renders tabs from what the registry reports.
- **The error taxonomy is generated, not duplicated.** Edit
  `backend/app/core/errors.py`, then run `make codegen`. CI fails if
  `frontend/lib/errors.ts` is stale.

### Privacy by construction

The data model is deliberately unable to answer "what did this person
download":

- Source URLs are **never stored** — only a salted, truncated digest, for
  deduplication
- Client IPs are **never stored** — only a salted SHA-256
- URLs are never logged at INFO; `redact_url` reduces them to host plus a short
  digest
- `usage_daily` holds aggregate counters only, with no per-user dimension
- Unhandled exceptions return no exception text, which would otherwise leak
  internal hostnames and source URLs

A test (`test_models.py`) fails if anyone adds a column that looks like it
stores an identifier in the clear.

---

## Testing

```bash
cd backend
pytest                      # offline; database-backed tests skip
pytest -m live              # opt-in canary against real URLs (expect breakage)
pytest --cov --cov-report=term-missing
```

Three layers, per the brief:

- **Unit** — URL parsing, registry, error mapping. No network, no services.
- **Integration** — extractors against recorded fixtures, so CI passes offline.
  Database tests run when `TEST_DATABASE_URL` is set (CI always sets it).
- **Live** — marked `live` and excluded from CI. This is the canary for
  platform breakage and is *expected* to fail periodically.

A weekly scheduled workflow (`.github/workflows/ytdlp-canary.yml`) bumps
`yt-dlp` to latest, runs the live suite, and opens a PR on success or files an
issue on failure. `yt-dlp` breaking after a platform change is the single most
likely cause of a production outage here, so noticing it is automated.

---

## Legal considerations

**Read this before deploying anything.**

This project is built for retrieving content that is already public, for
purposes such as archiving your own posts, accessibility, and research. It is
not built for, and actively refuses, everything else.

Enforced in code, not just documented:

1. **Public content only.** Content requiring an authenticated session
   belonging to another user is rejected with `PRIVATE_CONTENT` or
   `LOGIN_REQUIRED`.
2. **No DRM circumvention.** A DRM-protected stream fails immediately with
   `DRM_PROTECTED`. No decryption is attempted, ever. Circumventing DRM is a
   criminal matter in many jurisdictions (DMCA §1201 in the US, the EUCD in the
   EU) regardless of what you do with the result.
3. **No credential storage.** Platform passwords are never requested, logged or
   persisted. Where a feature needs a session, a user-supplied cookie string is
   held in memory for that single job and never written to disk or database.
4. **No paywalled or subscriber-only content.**
5. **Ephemeral media.** Downloads are deleted within 15 minutes; the sweeper
   runs every two.
6. **Rate limiting from day one.** Per-IP, no unauthenticated bulk endpoints.
7. **robots.txt is respected** on every HTML scraping path.

What the code cannot do for you:

- **Platform terms of service.** Automated downloading violates the ToS of
  most platforms here, including YouTube's. That is a contract matter between
  the operator and the platform, and running this may get an IP range or
  account blocked.
- **Copyright.** Public does not mean unencumbered. Downloading someone else's
  video is a reproduction, and whether that is lawful depends on your purpose
  and your jurisdiction. Fair use and fair dealing are narrow and
  fact-specific.
- **AI-generated output** (summaries, rewritten captions) is derived from
  someone else's work and is labelled as AI-generated in the UI. It carries
  the same copyright questions as the source.

Operators are responsible for the DMCA/takedown route and Terms of Use page
that ship in Phase 8, and for responding to takedown notices. Users are
responsible for respecting copyright and each platform's terms.

If you intend to run this as a public service, get legal advice first. The
constraints above reduce risk; they do not eliminate it.

---

## Repository layout

```
docs/
  BRIEF.md        requirements, decisions and the decision log
backend/
  app/
    api/          routes: health, errors (analyze, jobs, download to come)
    core/         logging, errors, middleware, security, redis
    models/       ORM models, session management, pydantic schemas
    workers/      arq worker settings and tasks
    platforms/    capability registry and per-platform modules  (Phase 2)
    extractors/   yt-dlp, gallery-dl and HTML adapters          (Phase 2)
    services/     transcription, summarizer, media ops, storage (Phase 3+)
  alembic/        migrations
  scripts/        gen_error_codes.py
  tests/
frontend/
  app/            App Router pages
  lib/            api client, generated error catalog
  components/     shadcn/ui components                          (Phase 4)
  hooks/                                                        (Phase 4)
```
