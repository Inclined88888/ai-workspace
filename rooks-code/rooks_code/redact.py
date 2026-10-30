# Vendored verbatim from workbench/backend/app/redact.py (tests/test_receipts.py keeps the copy identical).
"""Secrets never reach a log sink: every log record is redacted when it is created, whatever logger made it (R7).

Installed once at import of the app (main.py). Call sites need nothing. Patterns grow here, not at call sites.
Also applied to the run log's text columns (actions.py) and to error messages sent to clients (main.py, api.sse).
Covers: Telegram bot tokens, cookies and session ids, Authorization headers, JWTs, provider API keys, named secrets
(api_key, refresh_token, client_secret, password, ...), email addresses and phone numbers.
"""

import logging
import re

_SECRET_NAMES = (r"(?:[a-z0-9_-]*api[_-]?key|x-api-key|x-subscription-token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
                 r"auth[_-]?token|session[_-]?token|client[_-]?secret|password|passwd|secret)")

# Order matters: specific shapes first, so a key inside an Authorization header is handled once, by the header rule.
_PATTERNS = [
    # Telegram puts the bot token in the URL path: https://api.telegram.org/bot<id>:<secret>/method
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<redacted>"),
    # Cookie headers carry sessions: redact the whole value (every cookie in it)
    (re.compile(r"(?im)\b(set-cookie|cookie)(\s*[:=]\s*)[^\r\n]+"), r"\1\2<redacted>"),
    # Session cookies anywhere else (cookie jars, URLs): Workbench's own and common framework names
    (re.compile(r"\b(wb_session|sessionid|session|sid|connect\.sid|__(?:Secure|Host)-[\w-]+)=[^;\s&\"']+", re.I), r"\1=<redacted>"),
    # Authorization schemes
    (re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 <redacted>"),
    # JSON Web Tokens (OAuth access and id tokens)
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<redacted:jwt>"),
    # Provider keys by their published prefixes: OpenAI/Anthropic/OpenRouter (sk-), Google (AIza), GitHub, Slack, Tavily,
    # and Workbench's own terminal token (wb_)
    (re.compile(r"\b(?:sk-(?:ant-|or-v1-|proj-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{36,}|"
                r"xox[abprs]-[A-Za-z0-9-]{10,}|tvly-[A-Za-z0-9_-]{16,}|wb_[A-Za-z0-9_-]{30,})"), "<redacted:key>"),
    # name=value, "name": "value", name: value for secret-bearing names (query strings, JSON, headers, reprs);
    # a bare "token" only when the value looks like a credential (16+ chars), so "token: 1500" in a count stays
    (re.compile(rf"(?i)([\"']?\b{_SECRET_NAMES}\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s&,;}}]{{4,}})"), r"\1<redacted>"),
    (re.compile(r"(?i)([\"']?\btoken\b[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9._~+/-]{16,})"), r"\1<redacted>"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+(?:'[A-Za-z0-9._%+-]+)*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), "<email>"),
    # Phone numbers: international (+country) or national with separators. Never adjacent to digits or dots, so dates
    # (2026-10-29), times, versions (0.40.12) and IP addresses (192.168.100.200) are left alone. Unseparated digit runs
    # are not matched: they are indistinguishable from epoch times and ids (documented limit).
    (re.compile(r"(?<![\w+.])\+\d{1,3}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]?\d{2,4}){1,3}(?![\d.])"), "<phone>"),
    (re.compile(r"(?<![\w.])(?:\(\d{2,4}\)\s?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\d.])"), "<phone>"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj):
    """Redact every string inside a JSON-like structure, keeping its shape (error bodies, event payloads)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_obj(v) for v in obj)
    return obj


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
