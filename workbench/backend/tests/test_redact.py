"""R7: secrets are removed from every log record, whichever logger emits it and however the message is built."""

import logging

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
