from __future__ import annotations

import json

from app.core.logging import bind_request_id, configure_logging, get_logger, redact_url


def test_url_redaction_keeps_the_host_and_drops_the_path():
    redacted = redact_url("https://www.instagram.com/p/PrivateThing123/")
    assert redacted is not None
    assert "PrivateThing123" not in redacted
    assert "instagram.com" in redacted


def test_redaction_is_stable_for_the_same_url():
    url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert redact_url(url) == redact_url(url)


def test_redaction_distinguishes_different_content():
    assert redact_url("https://youtube.com/watch?v=aaa") != redact_url(
        "https://youtube.com/watch?v=bbb"
    )


def test_redaction_handles_none_and_junk():
    assert redact_url(None) is None
    assert redact_url("not a url") is not None


def test_records_carry_the_bound_request_id(capsys):
    """Asserted against real rendered output rather than structlog's
    capture_logs, which swaps out the processor chain being tested."""
    configure_logging(level="INFO", json_logs=True)
    bind_request_id("req-xyz")
    try:
        get_logger("test").info("something.happened", platform="youtube")
    finally:
        bind_request_id(None)

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["request_id"] == "req-xyz"
    assert payload["platform"] == "youtube"


def test_records_omit_the_request_id_outside_a_request(capsys):
    configure_logging(level="INFO", json_logs=True)
    bind_request_id(None)
    get_logger("test").info("worker.event")

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "request_id" not in payload


def test_json_output_is_parseable(capsys):
    configure_logging(level="INFO", json_logs=True)
    get_logger("test").info("structured.event", platform="youtube", content_type="video")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "structured.event"
    assert payload["platform"] == "youtube"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_configure_is_idempotent():
    configure_logging(level="INFO", json_logs=True)
    configure_logging(level="DEBUG", json_logs=False)
    get_logger("test").info("still.works")
