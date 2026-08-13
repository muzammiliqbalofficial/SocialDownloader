from __future__ import annotations

import pytest

from app.core.security import URL_HASH_LENGTH, hash_ip, hash_url, normalise_url

SALT = "test-salt-value"


def test_ip_hash_is_stable_and_not_reversible():
    digest = hash_ip("203.0.113.7", SALT)
    assert digest == hash_ip("203.0.113.7", SALT)
    assert "203.0.113.7" not in digest
    assert len(digest) == 64


def test_ip_hash_depends_on_the_salt():
    assert hash_ip("203.0.113.7", SALT) != hash_ip("203.0.113.7", "another-salt")


def test_missing_ip_hashes_to_none():
    assert hash_ip(None, SALT) is None
    assert hash_ip("", SALT) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://www.youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
        ("HTTPS://YouTube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
        ("https://youtube.com/watch?v=abc#t=30", "https://youtube.com/watch?v=abc"),
        # Trailing slash on a path, which is how Instagram and LinkedIn links
        # are shared. A slash inside a query string is content, not decoration,
        # so it is deliberately not stripped.
        ("https://www.instagram.com/p/ABC123/", "https://instagram.com/p/ABC123"),
    ],
)
def test_equivalent_urls_dedup_together(left, right):
    assert hash_url(left, SALT) == hash_url(right, SALT)


def test_different_urls_do_not_collide():
    assert hash_url("https://youtube.com/watch?v=abc", SALT) != hash_url(
        "https://youtube.com/watch?v=xyz", SALT
    )


def test_url_hash_is_truncated_and_contains_nothing_readable():
    digest = hash_url("https://instagram.com/p/CoolPost123/", SALT)
    assert len(digest) == URL_HASH_LENGTH
    assert "instagram" not in digest
    assert "CoolPost123" not in digest


def test_normalise_keeps_the_query_that_identifies_content():
    assert normalise_url("https://www.youtube.com/watch?v=abc") == "https://youtube.com/watch?v=abc"


def test_client_ip_prefers_the_forwarded_header():
    from unittest.mock import Mock

    from app.core.security import client_ip

    request = Mock()
    request.headers = {"x-forwarded-for": "198.51.100.9, 10.0.0.1"}
    request.client = Mock(host="10.0.0.1")
    assert client_ip(request) == "198.51.100.9"


def test_client_ip_falls_back_to_the_socket():
    from unittest.mock import Mock

    from app.core.security import client_ip

    request = Mock()
    request.headers = {}
    request.client = Mock(host="192.0.2.5")
    assert client_ip(request) == "192.0.2.5"
