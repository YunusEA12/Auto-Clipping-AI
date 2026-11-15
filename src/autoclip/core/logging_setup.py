"""Shared logging configuration for every script in the pipeline.

Two things every module used to configure independently, and inconsistently:

1. UTF-8 safety (L-01): every module called logging.basicConfig() on its own, each one
   pinning the root logger's console handler to whatever encoding sys.stdout happened to
   have — cp1252 on a default German Windows console. German umlauts and emoji in log
   messages (both routine in this codebase's German-language log strings and emoji status
   markers) then throw UnicodeEncodeError, crashing or truncating console output — reproduced
   constantly during development. This reconfigures stdout to UTF-8 explicitly and replaces
   anything still unencodable instead of crashing on it.

2. Structured logging (L-03): every log call is plain text, so the only way to monitor a
   24/7 fleet is to read the console or one of the hand-rolled JSON state snapshots. Setting
   LOG_FORMAT=json in the environment switches every module using this helper to emit one
   JSON object per line instead — still opt-in, since plain text stays far more readable for
   interactive/manual runs, but the capability now exists where it didn't before.

Usage (replaces `logging.basicConfig(level=logging.INFO, format="...")` in every module):
    import logging_setup
    logging_setup.configure_logging()
    logger = logging.getLogger(__name__)
"""

import json
import logging
import os
import re
import sys

_TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def redact(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 8 and any(
            word in name.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "WEBHOOK")
        ):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,})", "[REDACTED]", text)
    return re.sub(r"(?i)([?&](?:key|api_key|access_token|client_secret)=)[^&\s]+", r"\1[REDACTED]", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.upper(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc_info"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    stream = sys.stdout
    if hasattr(stream, "reconfigure"):
        try:
            # errors="backslashreplace" (not "replace"): keeps the offending character's
            # actual code point visible in the log line instead of silently swapping it for
            # "?", which matters when the unencodable text is itself the useful part of the
            # message (a filename, a foreign-language guideline rule, etc.).
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass  # best-effort — a stream that can't be reconfigured just keeps its default

    use_json = os.environ.get("LOG_FORMAT", "").strip().lower() == "json"

    # force=True: this may run after another module already called basicConfig() (import
    # order across ~19 entry points isn't fixed), and a plain basicConfig() call is a no-op
    # once the root logger already has a handler. force=True always (re)installs OUR handler
    # with the UTF-8-safe stream, regardless of which module gets here first.
    logging.basicConfig(level=level, format=_TEXT_FORMAT, stream=stream, force=True)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonFormatter() if use_json else RedactingFormatter(_TEXT_FORMAT))
