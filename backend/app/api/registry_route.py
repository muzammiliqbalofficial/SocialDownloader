"""GET /api/registry -- the capability matrix the frontend renders from.

Disabled platforms are absent from this response entirely, so the UI cannot
show a tab for something that will not work (decision D-004).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.platforms.registry import describe_registry

router = APIRouter(tags=["meta"])


@router.get("/registry", summary="Platform capability registry")
async def registry() -> dict:
    settings = get_settings()
    platforms = describe_registry(settings)
    return {
        "platforms": platforms,
        # The UI should only offer to analyze links for platforms that work.
        "implemented": [p["platform"] for p in platforms if p["implemented"]],
    }
