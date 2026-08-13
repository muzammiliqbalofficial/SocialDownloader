# Cloud Run configuration

Two services from one image: `api` (HTTP) and `worker` (arq). The worker YAML
is committed but **not deployable until Phase 3** adds an HTTP listener — see
the note at the top of `worker.service.yaml`.

## The concurrency contract

This is the part that is easy to get wrong and impossible to catch with a
functional test, so it is written down rather than left implicit.

Each `yt-dlp` invocation is a **separate OS process**. Its resident memory is
charged to the container, and on Cloud Run so is everything written to `/tmp`,
because the filesystem is an in-memory tmpfs.

Measured cost per subprocess:

| Figure | Value | How it was obtained |
| --- | --- | --- |
| Floor | **45 MiB** | `ru_maxrss` of the child, measured on Python 3.11 with the network blocked, so the process exits before parsing a format list. This is the interpreter plus yt-dlp's imports and nothing else. |
| Budget | **80 MiB** | The floor plus headroom for a real extraction: a large `formats` array, TLS buffers, and the JSON document held in memory while it is serialised to stdout. |

The arithmetic that sets the limits:

```
                     4 extractions  x  80 MiB   =  320 MiB
  4 stdout buffers at the 32 MiB parse cap      =  128 MiB
  Python + FastAPI + SQLAlchemy + asyncpg       =  250 MiB
                                          peak  ≈  700 MiB
```

Against a 2 GiB limit that is roughly 2.8x headroom. Cloud Run OOM-kills
without warning and takes every in-flight request with it, so the headroom is
the point.

**What happens without the bound.** Cloud Run's default
`containerConcurrency` is 80. Eighty simultaneous analyze calls, each spawning
a subprocess, is 80 x 80 MiB ≈ **6.4 GiB** — an instant OOM on any instance
size we would plausibly pay for. Nothing in the functional test suite would
ever show this, because every test issues one request at a time.

Two independent mechanisms therefore hold the line, and both must stay in
proportion:

| Setting | Where | Value | Role |
| --- | --- | --- | --- |
| `MAX_CONCURRENT_EXTRACTIONS` | app env | 4 | An `asyncio.Semaphore` in `ytdlp_adapter`. The real memory bound. Enforced in-process, so it holds regardless of how the service is deployed. |
| `containerConcurrency` | Cloud Run | 8 | How many requests may be in the instance at all. Bounds the queue standing behind the semaphore. |

**Rule: `containerConcurrency` must not exceed `MAX_CONCURRENT_EXTRACTIONS` by
more than about 2x.** The multiple is the queue. At 2x, four requests extract
and at most four wait; a waiter that does not get a slot within
`EXTRACTION_QUEUE_WAIT_SECONDS` (2s) receives `429 RATE_LIMITED` with
`Retry-After: 5`. At Cloud Run's default of 80 the same semaphore still
prevents the OOM, but 76 requests would sit in a queue burning the client's
patience and then time out at the load balancer — a worse failure than an
honest, immediate rejection.

Raising either number means redoing the memory arithmetic above and raising
`memory` with it. `tests/test_extractor_concurrency.py` pins the in-process
half of this contract.

## Why the probes point at `/live` and not `/ready`

`/api/health/live` touches no dependencies. `/api/health/ready` returns 503
when Postgres or Redis is unreachable.

- **Startup and liveness probes use `/live`.** A liveness probe on readiness
  would restart the container every time Postgres blipped, converting a
  dependency outage into a crash loop and making recovery slower rather than
  faster.
- **Readiness is for the load balancer**, which stops routing to an instance
  that cannot serve. That is the correct response to a dependency outage.
- A missing `ffmpeg` shows as degraded on `/api/health` but deliberately does
  **not** fail readiness: it costs the merged high-quality formats while every
  metadata path keeps working, and draining the instance would turn a partial
  degradation into a total outage.

## Cost shape

The worker runs with `cpu-throttling: "false"` and `minScale: 1`, so it bills
continuously. That is unavoidable: with throttling on, Cloud Run freezes CPU
between requests, and the worker never receives a request — jobs would not run
and the TTL sweeper would not sweep, silently breaking the 15-minute media
deletion guarantee.

The API keeps throttling on and does no work between requests, so it bills per
request plus one warm instance.

## Before the first deploy

Replace the placeholders (`PROJECT_ID_PLACEHOLDER`, `REGION_PLACEHOLDER`) and
create the secrets and service accounts:

```bash
PROJECT_ID=your-project
REGION=europe-west1

# Secrets. IP_HASH_SALT especially: leak it and every stored ip_hash becomes
# brute-forceable against the IPv4 space in minutes.
python -c "import secrets; print(secrets.token_urlsafe(32))" \
  | gcloud secrets create socialdownloader-ip-hash-salt --data-file=-

for secret in database-url redis-url groq-api-key gemini-api-key; do
  gcloud secrets create "socialdownloader-$secret" --data-file=-
done

# Media bucket. A lifecycle rule is the backstop for the TTL sweeper, not a
# replacement for it: 1 day is the coarsest granularity GCS offers, and the
# requirement is 15 minutes.
gsutil mb -l "$REGION" "gs://socialdownloader-media-$PROJECT_ID"
gsutil lifecycle set lifecycle.json "gs://socialdownloader-media-$PROJECT_ID"

sed -i "s/PROJECT_ID_PLACEHOLDER/$PROJECT_ID/g; s/REGION_PLACEHOLDER/$REGION/g" \
  deploy/cloudrun/*.yaml

gcloud run services replace deploy/cloudrun/api.service.yaml --region="$REGION"
```

The API service account needs `roles/secretmanager.secretAccessor`, plus
`roles/storage.objectAdmin` on the media bucket once Phase 3 lands, and
`roles/cloudsql.client` if Postgres is Cloud SQL.
