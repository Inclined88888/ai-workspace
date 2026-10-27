"""Spot secrets and payment card numbers so they are never kept in memory or written to the audit log.

Deliberately specific: well known key formats, private key blocks, "password is ..." phrasing and Luhn-valid card
numbers. A random looking word is not flagged, so ordinary facts are not lost to false positives.
"""

import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("API key", re.compile(r"\b(?:sk|rk|pk)-(?:[a-z]+-)*[A-Za-z0-9_\-]{20,}")),          # OpenAI, Anthropic, OpenRouter
    ("API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),                               # Google
    ("API key", re.compile(r"\btvly-[A-Za-z0-9_\-]{16,}")),                               # Tavily
    ("access token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("access token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),                     # Slack
    ("access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),                         # AWS
    ("bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),                        # Telegram
    ("access token", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),  # JWT
    ("access token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("password", re.compile(r"(?i)\b(?:password|passwd|passcode|pin code|pwd)\b\s*(?:is|=|:)\s*\S+")),
    ("secret", re.compile(r"(?i)\b(?:api[_ ]?key|secret|token)\b\s*(?:is|=|:)\s*['\"]?[A-Za-z0-9._\-/+]{12,}")),
]
_CARD = re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _cards(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits) and len(set(digits)) > 1:
            out.append(m.span())
    return out


def find(text: str) -> str:
    """The kind of sensitive value in text ('API key', 'card number', ...), or '' if none was found."""
    text = text or ""
    for kind, pat in _PATTERNS:
        if pat.search(text):
            return kind
    return "card number" if _cards(text) else ""


def redact(text: str) -> str:
    text = text or ""
    for kind, pat in _PATTERNS:
        text = pat.sub(f"[{kind} removed]", text)
    for start, end in reversed(_cards(text)):
        text = text[:start] + "[card number removed]" + text[end:]
    return text


def refuse_message(kind: str) -> str:
    return (f"Not saved: that looks like a {kind}. ROOKS never keeps secrets or card numbers in memory. "
            "Keys belong in Settings, where they are stored encrypted.")
