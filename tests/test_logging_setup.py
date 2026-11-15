import io
import json
import logging

import pytest

from autoclip.core import logging_setup


@pytest.mark.parametrize("json_mode", [False, True])
def test_api_keys_bearers_and_exception_details_are_redacted(monkeypatch, json_mode):
    secret = "fixture-private-key-0123456789"
    monkeypatch.setenv("EXAMPLE_API_KEY", secret)
    monkeypatch.setenv("LOG_FORMAT", "json" if json_mode else "text")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    logging_setup.configure_logging()
    try:
        raise ValueError("Bearer fixture-access-token-123")
    except ValueError:
        logging.getLogger("redaction").exception("request key=%s", secret)
    output = stream.getvalue()
    assert secret not in output
    assert "fixture-access-token-123" not in output
    assert "[REDACTED]" in output


def test_configure_logging_does_not_raise_on_unicode(monkeypatch, capsys):
    logging_setup.configure_logging()
    logger = logging.getLogger("test_unicode")
    # This is the exact failure mode L-01 fixes: German umlauts + emoji used to throw
    # UnicodeEncodeError against a cp1252 console codepage.
    logger.info("Ümlaute äöüß und Emoji 🚀🔴 dürfen nicht crashen")


def test_json_format_produces_valid_json_lines(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    logging_setup.configure_logging()

    logger = logging.getLogger("test_json")
    logger.warning("structured message")

    output = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(output)
    assert payload["level"] == "WARNING"
    assert payload["message"] == "structured message"
    assert payload["logger"] == "test_json"


def test_text_format_is_plain_by_default(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    logging_setup.configure_logging()

    logger = logging.getLogger("test_text")
    logger.warning("plain message")

    output = stream.getvalue().strip().splitlines()[-1]
    assert "plain message" in output
    assert not output.startswith("{")
