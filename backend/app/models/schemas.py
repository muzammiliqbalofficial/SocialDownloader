"""Pydantic response schemas shared across the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ComponentHealth(BaseModel):
    status: Literal["ok", "down", "unknown"]
    detail: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    components: dict[str, ComponentHealth] = Field(default_factory=dict)


class FeatureFlags(BaseModel):
    """What the frontend should render. Absent AI keys hide the AI tab entirely
    rather than showing a control that fails on click."""

    transcription: bool
    summarization: bool
    batch: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    action: str
    detail: str | None = None
    retryable: bool = False
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
