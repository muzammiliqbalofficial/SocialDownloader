"""Aggregate API router.

Routes for analyze, jobs, download and registry are added in Phases 2-3; this
module is where they get mounted.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import errors_route, health

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(errors_route.router)
