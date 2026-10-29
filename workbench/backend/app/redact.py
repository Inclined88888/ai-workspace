"""Secrets never reach a log sink: every log record is redacted when it is created, whatever logger made it.

Installed once at import of the app (main.py). Call sites need nothing. Patterns grow here, not at call sites.
"""

import logging
import re

# Telegram puts the bot token in the URL path: https://api.telegram.org/bot<id>:<secret>/method
_PATTERNS = [(re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<redacted>")]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def install() -> None:
    """Wrap the log record factory so message, arguments and traceback are redacted before any handler sees them."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg, record.args = redact(record.getMessage()), None
        except Exception:  # noqa: BLE001 - a malformed record must still be logged, never crash the caller
            record.msg, record.args = redact(str(record.msg)), None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return record

    factory._redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
