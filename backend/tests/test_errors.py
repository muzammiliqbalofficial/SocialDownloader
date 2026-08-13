from __future__ import annotations

import pytest

from app.core.errors import ERROR_CATALOG, AppError, ErrorCode

# The taxonomy is fixed by section 10. This list is written out by hand rather
# than derived from the enum, so that deleting a code fails the test.
SPEC_CODES = {
    "INVALID_URL",
    "UNSUPPORTED_PLATFORM",
    "UNSUPPORTED_CONTENT_TYPE",
    "PRIVATE_CONTENT",
    "LOGIN_REQUIRED",
    "DRM_PROTECTED",
    "GEOBLOCKED",
    "CONTENT_REMOVED",
    "RATE_LIMITED",
    "EXTRACTOR_OUTDATED",
    "DURATION_EXCEEDED",
    "FILESIZE_EXCEEDED",
    "AI_UNAVAILABLE",
    "UPSTREAM_TIMEOUT",
    "INTERNAL_ERROR",
}


def test_taxonomy_matches_the_specification_exactly():
    assert {str(c) for c in ErrorCode} == SPEC_CODES


def test_every_code_has_a_catalog_entry():
    assert set(ERROR_CATALOG) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_entry_has_a_usable_message_and_action(code):
    spec = ERROR_CATALOG[code]
    assert spec.message.strip() and spec.message.endswith((".", "!"))
    assert spec.action.strip()
    assert 400 <= spec.status <= 599


@pytest.mark.parametrize("code", list(ErrorCode))
def test_no_message_is_a_generic_failure(code):
    """Section 4: never a bare 'failed' state."""
    text = ERROR_CATALOG[code].message.lower()
    assert text not in {"failed.", "error.", "something went wrong."}


def test_extractor_outdated_speaks_plainly_about_platform_change():
    """Section 10 calls this one out specifically: the user sees an explanation,
    not a stack trace."""
    spec = ERROR_CATALOG[ErrorCode.EXTRACTOR_OUTDATED]
    assert "changed" in spec.message.lower()
    assert spec.retryable is True
    assert spec.status == 503


def test_drm_error_is_not_retryable_and_promises_no_bypass():
    spec = ERROR_CATALOG[ErrorCode.DRM_PROTECTED]
    assert spec.retryable is False
    assert "not attempt" in spec.action.lower() or "will not" in spec.action.lower()


def test_login_required_refuses_to_offer_sign_in():
    spec = ERROR_CATALOG[ErrorCode.LOGIN_REQUIRED]
    assert "don't sign in" in spec.action.lower()


def test_app_error_carries_status_and_payload():
    err = AppError(ErrorCode.RATE_LIMITED, detail="10 requests per minute")
    assert err.status_code == 429
    payload = err.to_payload("req-1")["error"]
    assert payload["code"] == "RATE_LIMITED"
    assert payload["detail"] == "10 requests per minute"
    assert payload["retryable"] is True
    assert payload["request_id"] == "req-1"


def test_app_error_preserves_its_cause():
    original = ValueError("boom")
    err = AppError(ErrorCode.INTERNAL_ERROR, cause=original)
    assert err.__cause__ is original


async def test_app_error_is_rendered_by_the_handler(app, client):
    from app.core.errors import AppError as _AppError

    @app.get("/api/_test/boom")
    async def boom():
        raise _AppError(ErrorCode.GEOBLOCKED)

    response = await client.get("/api/_test/boom")
    assert response.status_code == 451
    body = response.json()["error"]
    assert body["code"] == "GEOBLOCKED"
    assert body["request_id"] == response.headers["X-Request-ID"]


async def test_unhandled_exception_becomes_internal_error_without_leaking(app):
    """A raw exception must not reach the client: no message, no traceback."""
    import httpx

    @app.get("/api/_test/explode")
    async def explode():
        raise RuntimeError("secret-internal-hostname-db-01")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/_test/explode")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-internal-hostname" not in response.text


async def test_unknown_route_maps_into_the_taxonomy(client):
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] in {str(c) for c in ErrorCode}
    assert body["action"]
