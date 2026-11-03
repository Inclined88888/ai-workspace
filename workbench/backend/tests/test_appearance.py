"""The chosen look is saved by the app and shared with the phone (pairing API) and the terminal tools (a file)."""

import json

from test_mobile import _auth, _pair, phone  # noqa: F401  (fixture)


def test_defaults_to_sunshine(client):
    a = client.get("/api/appearance").json()
    assert a["theme"] == "sunshine" and a["dark"] is False and a["accent"] == "theme"


def test_save_cleans_values_and_resolves_dark(client):
    a = client.put("/api/appearance", json={"theme": "neon", "font": "editorial", "size": 99, "motion": "subtle"}).json()
    assert a == {"theme": "neon", "resolved": "neon", "dark": True, "font": "editorial", "size": 18, "motion": "subtle", "accent": "theme"}
    assert client.get("/api/appearance").json()["theme"] == "neon"
    a = client.put("/api/appearance", json={"theme": "<script>", "font": "x", "motion": "wild", "accent": "rainbow", "size": 3}).json()
    assert (a["theme"], a["font"], a["motion"], a["accent"], a["size"]) == ("sunshine", "modern", "full", "theme", 12)


def test_match_my_system_keeps_what_the_page_resolved(client):
    a = client.put("/api/appearance", json={"theme": "auto", "resolved": "night"}).json()
    assert (a["theme"], a["resolved"], a["dark"]) == ("auto", "night", True)
    a = client.put("/api/appearance", json={"theme": "auto"}).json()
    assert (a["resolved"], a["dark"]) == ("sunshine", False)


def test_needs_the_owner(phone):  # noqa: F811
    assert phone.get("/api/appearance").status_code == 401
    assert phone.put("/api/appearance", json={"theme": "neon"}).status_code == 401


def test_paired_phone_reads_the_desktop_look(client, phone):  # noqa: F811
    client.put("/api/appearance", json={"theme": "cherry", "font": "classic", "size": 16})
    token, _ = _pair(client, phone)
    r = phone.get("/api/mobile/appearance", headers=_auth(token))
    assert r.status_code == 200
    assert (r.json()["theme"], r.json()["font"], r.json()["size"], r.json()["dark"]) == ("cherry", "classic", 16, False)
    assert phone.get("/api/mobile/appearance").status_code == 401
    assert phone.get("/api/mobile/appearance", headers=_auth("not-a-token")).status_code == 401


def test_terminal_tools_get_a_shared_file(client, tmp_path, monkeypatch):
    f = tmp_path / "share" / "appearance.json"
    monkeypatch.setenv("AI_WORKSPACE_APPEARANCE_FILE", str(f))
    client.put("/api/appearance", json={"theme": "contrast"})
    assert json.loads(f.read_text())["theme"] == "contrast"
    client.put("/api/appearance", json={"theme": "night"})
    data = json.loads(f.read_text())
    assert data["theme"] == "night" and data["dark"] is True
