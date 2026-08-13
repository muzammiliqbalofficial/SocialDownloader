"""Exposes the error taxonomy so the frontend can render messages it has never
seen -- a code added on the backend degrades to a correct message, not to
"unknown error"."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import ERROR_CATALOG

router = APIRouter(tags=["meta"])


@router.get("/errors", summary="Error taxonomy")
async def error_taxonomy() -> dict[str, dict]:
    return {
        str(code): {
            "message": spec.message,
            "action": spec.action,
            "retryable": spec.retryable,
            "status": spec.status,
        }
        for code, spec in ERROR_CATALOG.items()
    }
