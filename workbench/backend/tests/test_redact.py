"""R7: secrets are removed from every log record, whichever logger emits it and however the message is built."""

import logging

import pytest
from conftest import events

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def _capture(logger_name: str, emit):
    from app import main  # noqa: F401 - installs the redacting record factory

    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(logging.Formatter().format(r))
    log = logging.getLogger(logger_name)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        emit(log)
    finally:
        log.removeHandler(handler)
    return "\n".join(records)


def test_telegram_token_in_url_is_redacted_in_messages_and_arguments(client):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    out = _capture("httpx", lambda log: log.info('HTTP Request: %s %s "%s"', "POST", url, "HTTP/1.1 200 OK"))
    out += _capture("telegram", lambda log: log.warning(f"failed calling {url}"))
    assert TOKEN not in out and TOKEN.split(":")[1] not in out
    assert out.count("/bot<redacted>/getUpdates") == 2


def test_telegram_token_in_a_traceback_is_redacted(client):
    def emit(log):
        try:
            raise RuntimeError(f"Client error for url https://api.telegram.org/bot{TOKEN}/sendMessage")
        except RuntimeError:
            log.exception("send failed")

    out = _capture("telegram", emit)
    assert "Traceback" in out and TOKEN not in out and "/bot<redacted>/sendMessage" in out


def test_ordinary_messages_are_untouched(client):
    out = _capture("scheduler", lambda log: log.warning("schedule %s failed after %d s", "Daily report", 30))
    assert out == "schedule Daily report failed after 30 s"


def test_formatters_that_unpack_args_still_work(client):
    """uvicorn's access log unpacks record.args; redaction must keep its shape (it broke every access line before)."""
    from uvicorn.logging import AccessFormatter

    from app import main  # noqa: F401

    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r)
    log = logging.getLogger("uvicorn.access.test")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/hook/bot{TOKEN}/x", "1.1", 200)
    finally:
        log.removeHandler(handler)
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(records[0])
    assert line == '127.0.0.1:5000 - "GET /hook/bot<redacted>/x HTTP/1.1" 200 OK'


def test_url_objects_are_redacted(client):
    import httpx

    url = httpx.URL(f"https://api.telegram.org/bot{TOKEN}/getMe")
    out = _capture("httpx", lambda log: log.info("HTTP Request: %s %s", "POST", url))
    assert TOKEN not in out and "/bot<redacted>/getMe" in out


# R7 full pattern set: one case per pattern class, then content that must survive untouched
SECRETS = {
    "telegram bot token": (f"GET https://api.telegram.org/bot{TOKEN}/getMe", "AAHdqTcvCH"),
    "OpenAI key": ("using sk-proj-AbCdEfGhIjKlMnOpQrStUvWx12345", "AbCdEfGhIjKl"),
    "Anthropic key": ("x-api-key: sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZ", "ZZZZZZZZZZZZ"),
    "OpenRouter key": ("key=sk-or-v1-0123456789abcdef0123456789abcdef", "0123456789abcdef"),
    "Google key": ("AIzaSyA1234567890abcdefghijklmnopqrstuv in url", "SyA1234567890"),
    "Workbench CLI token": ("token wb_Q2xpVG9rZW5WYWx1ZUlzTG9uZ0Vub3VnaA", "Q2xpVG9rZW5W"),
    "Bearer header": ("Authorization: Bearer opaque.token-value_123456", "opaque.token"),
    "Basic header": ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50"),
    "JWT": ("id eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXZhbHVlLXg", "eyJzdWIiOiIx"),
    "OAuth refresh token (form)": ("grant_type=refresh_token&refresh_token=0.AXoA9fK2mQpLz8_rT3v&client_id=x", "AXoA9fK2mQ"),
    "OAuth refresh token (JSON)": ('{"access_token": "ya29.a0AfH6SMBx", "refresh_token": "1//0gLx9QpR"}', "1//0gLx9QpR"),
    "client secret": ("client_secret: 'cs_live_51HxYz'", "cs_live_51HxYz"),
    "password": ("login failed for password=hunter2222", "hunter2222"),
    "api_key query": ("GET /search?q=weather&api_key=abcd1234efgh", "abcd1234efgh"),
    "Cookie header": ("Cookie: wb_session=abc.def.ghi; theme=dark", "abc.def.ghi"),
    "Set-Cookie header": ("Set-Cookie: sessionid=7f3a9b2c1d; Path=/; HttpOnly", "7f3a9b2c1d"),
    "Cookie with any name": ("Cookie: remember_me=8f7e6d5c4b3a; theme=dark", "8f7e6d5c4b3a"),
    "session id in a URL": ("redirect to /next?sid=a1b2c3d4e5f6&x=1", "a1b2c3d4e5f6"),
    "email": ("draft to sam.o'neil@example.co.uk saved", "neil@example"),
    "email with plus": ("from ana+news@mail.example.com", "ana+news"),
    "phone, international": ("call +61 412 345 678 today", "412 345 678"),
    "phone, US style": ("reach me on +1 (415) 555-0132", "555-0132"),
    "phone, national": ("office (02) 9374 4000, mobile 0412 345 678", "9374 4000"),
}

UNTOUCHED = [
    "2026-10-29T14:13:21+00:00 schedule ran in 352 s",
    "Ollama 0.40.12 on 127.0.0.1:11434 and 192.168.100.200:8080",
    "cost $0.0105 for 1000 prompt tokens, tokens: 1500, token: 1500",
    "task #12 due 2026-11-30, cron '0 8 * * *', epoch 1730000000",
    "request 4f9c2e1a8b7d6c5e4f3a2b1c0d9e8f7a outcome verified",
    "the secret to good soup is time; the key: patience",
    'GET /api/tasks?status=todo HTTP/1.1" 200',
    "@router.get('/x') decorator and pkg@1.2.3",
    "Your Telegram user id is 123456789",
]


@pytest.mark.parametrize("name", SECRETS)
def test_each_secret_pattern_is_redacted(name):
    from app.redact import redact

    text, secret = SECRETS[name]
    out = redact(text)
    assert secret not in out, f"{name}: {out}"
    assert "<redacted" in out or "<email>" in out or "<phone>" in out


def test_legitimate_content_is_not_redacted():
    from app.redact import redact

    changed = [(t, redact(t)) for t in UNTOUCHED if redact(t) != t]
    assert not changed, changed


def test_argument_tuple_shape_and_types_are_preserved(client):
    """The PR #1 requirement: formatters unpack args, so shape, length and non-secret values must not change."""
    from app import main  # noqa: F401

    marker = object()
    record = logging.getLogger("shape").makeRecord("shape", logging.INFO, __file__, 1, "%s %s %d %s %r",
                                                   ("a@b.example", f"/bot{TOKEN}/x", 42, "plain", marker), None)
    assert isinstance(record.args, tuple) and len(record.args) == 5
    assert record.args[0] == "<email>" and record.args[1] == "/bot<redacted>/x"
    assert record.args[2] == 42 and record.args[3] == "plain" and record.args[4] is marker
    record = logging.getLogger("shape").makeRecord("shape", logging.INFO, __file__, 1, "%(who)s", ({"who": "+61 412 345 678"},), None)
    assert record.args == {"who": "<phone>"}


def test_every_pattern_is_redacted_in_real_log_output(client):
    out = "\n".join(_capture("any", lambda log, t=t: log.warning("event: %s", t)) for t, _ in SECRETS.values())
    for name, (_, secret) in SECRETS.items():
        assert secret not in out, name


def test_provider_errors_quoting_a_key_reach_the_client_redacted(client):
    from app import db

    ev = events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{client.pid}/leaky-model"}))
    err = next(e["error"] for e in ev if e.get("type") == "error")
    assert "LEAKED" not in err and "<redacted:key>" in err
    row = db.one("SELECT failure_reason FROM actions WHERE kind = 'model_call' AND model = ? ORDER BY id DESC", f"{client.pid}/leaky-model")
    assert "LEAKED" not in row["failure_reason"]


def test_validation_errors_do_not_echo_secrets(client):
    r = client.post("/api/providers", json={"name": "x", "kind": "openai", "base_url": "https://a.example/v1",
                                            "api_key": "sk-proj-" + "S" * 600})
    assert r.status_code == 422 and "SSSSSSSSSSSSSSSSSSSS" not in r.text
