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


def _scrub(value):
    """An argument is replaced by its redacted text only when its text holds a secret (str, URL objects, exceptions)."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    clean = redact(text)
    return value if clean == text else clean


def install() -> None:
    """Wrap the log record factory so message, arguments and traceback are redacted before any handler sees them."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        # Keep the shape of args: formatters such as uvicorn's access log unpack them
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_scrub(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _scrub(v) for k, v in record.args.items()}
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return record

    factory._redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
