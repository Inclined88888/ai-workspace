"""Mail through the account signed in on this Mac (mac_mail.py): Outlook without app passwords or an app registration."""

import json

import pytest

H = {"X-Workbench": "1"}
ACCOUNTS = [{"name": "Exchange", "emails": ["azad.singh@outlook.com.au"], "enabled": True, "type": "imap"},
            {"name": "Google", "emails": ["me@gmail.com"], "enabled": False, "type": "imap"}]


@pytest.fixture
def mac(monkeypatch):
    from app import mac_mail

    calls = []

    def fake(script, args, timeout=90, lang="JavaScript"):
        calls.append((script, args))
        if script == mac_mail.ACCOUNTS_JS:
            return json.dumps(ACCOUNTS)
        if script == mac_mail.LIST_JS:
            return json.dumps([{"uid": "7", "from": "Boss <b@x.com>", "subject": "Old", "date": "2026-11-01T01:00:00.000Z", "unread": False},
                               {"uid": "9", "from": "Ann <a@x.com>", "subject": "New", "date": "2026-11-02T01:00:00.000Z", "unread": True}])
        if script == mac_mail.GET_JS:
            return json.dumps({"uid": args[1], "from": "Ann <a@x.com>", "reply_to": "a@x.com", "to": "azad.singh@outlook.com.au",
                               "cc": "", "subject": "New", "date": "2026-11-02T01:00:00.000Z", "message_id": "<m1@x>", "body": "Hi"}) \
                if args[1] == "9" else "null"
        if script == mac_mail.COMPOSE_JS:
            return "sent" if args[5] == "send" else "draft"
        raise AssertionError("unexpected script")

    monkeypatch.setattr(mac_mail, "available", lambda: True)
    monkeypatch.setattr(mac_mail, "_osa", fake)
    yield calls
    from app import db

    db.run("DELETE FROM mail_accounts WHERE kind = 'mac'")


def test_outlook_offers_the_mac_sign_in_instead_of_a_dead_end(client, mac, monkeypatch):
    from app import mail_autodetect

    monkeypatch.setenv("MAIL_AUTODETECT_ONLINE", "0")
    monkeypatch.setattr(mail_autodetect, "_one_click_ready", lambda p: False)
    from app import mac_mail

    monkeypatch.setattr(mac_mail, "available", lambda: True)
    d = mail_autodetect.detect("azad.singh@outlook.com.au")
    assert d["kind"] == "unavailable" and d["mac"] is True and "normal sign-in page" in d["message"]
    assert d["steps"]  # the calendar-only route is still offered


def test_a_mistyped_address_is_called_a_typo(monkeypatch):
    from app import mail_autodetect

    monkeypatch.setenv("MAIL_AUTODETECT_ONLINE", "0")
    assert "typo" in mail_autodetect.detect("azad,.singh@outlook.com.au")["message"]
    assert "typo" in mail_autodetect.detect("azad..singh@outlook.com")["message"]
    assert mail_autodetect.detect("azad.singh@outlook.com")["kind"] != "invalid"


def test_connect_read_and_reply_through_apple_mail(client, mac):
    listed = client.get("/api/mail/mac", headers=H).json()
    assert listed["available"] and [a["connected"] for a in listed["accounts"]] == [False, False]
    off = client.post("/api/mail/mac", headers=H, json={"name": "Google", "email": "me@gmail.com"})
    assert off.status_code == 400 and "turn on Mail" in off.json()["detail"]
    nope = client.post("/api/mail/mac", headers=H, json={"name": "Exchange", "email": "someone@else.com"})
    assert nope.status_code == 404
    aid = client.post("/api/mail/mac", headers=H, json={"name": "Exchange", "email": "azad.singh@outlook.com.au"}).json()["id"]
    again = client.post("/api/mail/mac", headers=H, json={"name": "Exchange", "email": "azad.singh@outlook.com.au"}).json()["id"]
    assert again == aid
    assert client.get("/api/mail/mac", headers=H).json()["accounts"][0]["connected"]
    acct = next(a for a in client.get("/api/mail/accounts", headers=H).json() if a["id"] == aid)
    assert acct["kind"] == "mac" and acct["username"] == "Exchange"
    msgs = client.get(f"/api/mail/{aid}/messages", headers=H).json()
    assert [m["subject"] for m in msgs] == ["New", "Old"]  # newest first
    one = client.get(f"/api/mail/{aid}/messages/9", headers=H).json()
    assert one["body"] == "Hi" and one["reply_to"] == "a@x.com"
    assert client.get(f"/api/mail/{aid}/messages/8", headers=H).status_code == 404
    assert client.get(f"/api/mail/{aid}/messages/x;rm", headers=H).status_code in (400, 404)
    # the user's words travel as arguments, never inside the script text
    from app import mac_mail, mail

    mail.send(dict(acct, password_enc=""), "ann@x.com", 'Re: "New"', "Thanks'); do shell script(\"rm -rf ~\")", "<m1@x>")
    script, args = mac[-1]
    assert script == mac_mail.COMPOSE_JS and args[:6] == ["azad.singh@outlook.com.au", "ann@x.com", "", 'Re: "New"',
                                                         "Thanks'); do shell script(\"rm -rf ~\")", "send"]


def test_drafts_from_chat_also_land_in_apple_mail(client, mac):
    from app import mac_mail

    aid = client.post("/api/mail/mac", headers=H, json={"name": "Exchange", "email": "azad.singh@outlook.com.au"}).json()["id"]
    from app.tools import DraftIn, save_draft

    r = save_draft(DraftIn(account_id=aid, to="ann@x.com", subject="Hi", body="Draft body"))
    assert "Apple Mail Drafts" in r["status"]
    assert mac[-1][0] == mac_mail.COMPOSE_JS and mac[-1][1][5] == "draft"


def test_off_unless_opted_in(monkeypatch):
    from app import mac_mail

    monkeypatch.delenv("MAC_ACCOUNTS", raising=False)
    assert not mac_mail.available()


def test_not_on_a_mac_means_not_offered(client, monkeypatch):
    from app import mac_mail

    monkeypatch.setattr(mac_mail, "available", lambda: False)
    assert client.get("/api/mail/mac", headers=H).json() == {"available": False, "accounts": []}
    with pytest.raises(Exception):
        mac_mail.accounts()
