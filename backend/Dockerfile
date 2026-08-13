# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by yt-dlp to merge separate video/audio streams and to
# extract audio; without it the format picker silently loses the best options.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so the dependency layer caches across code changes.
COPY requirements.txt requirements-dev.txt ./


FROM base AS dev
RUN pip install -r requirements-dev.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


FROM base AS prod
RUN pip install -r requirements.txt
COPY . .

# Non-root: Cloud Run does not require it, but a container that never needs
# root should not run as it.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/socialdl \
    && chown -R appuser /app /tmp/socialdl
USER appuser

EXPOSE 8000

# Cloud Run supplies $PORT; default to 8000 elsewhere. Shell form so it expands.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}
