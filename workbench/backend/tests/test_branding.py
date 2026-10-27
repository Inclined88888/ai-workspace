"""ROOKS rename: old default names stored by earlier installs show as ROOKS and ROOKS; chosen names are kept."""


def test_workspace_name_maps_old_default(client):
    from app import config, db

    assert config.display_name(None) == "ROOKS"
    assert config.display_name("AI Workspace") == "ROOKS"
    assert config.display_name("Acme HQ") == "Acme HQ"
    db.set_setting("workspace_name", "AI Workspace")  # what the old setup screen saved by default
    assert client.get("/api/status").json()["workspace_name"] == "ROOKS"
    db.set_setting("workspace_name", "Acme HQ")
    assert client.get("/api/status").json()["workspace_name"] == "Acme HQ"
    db.set_setting("workspace_name", "AI Workspace")


def test_assistant_name_maps_old_default(client):
    from app import assistant, db

    db.set_setting("assistant_name", "Atlas")
    assert client.get("/api/assistant/settings").json()["assistant_name"] == "ROOKS"
    assert assistant.system_prompt(assistant.Ctx(channel="web", conversation_id=0), "").startswith("You are ROOKS,")
    client.put("/api/assistant/settings", json={"assistant_name": "Friday"})
    assert assistant.assistant_name() == "Friday"
    client.put("/api/assistant/settings", json={"assistant_name": ""})
    assert assistant.assistant_name() == "ROOKS"


def test_city_timezone_from_setup_resolves(client):
    from app import config

    assert config.resolve_timezone("brisbane") == "Australia/Brisbane"
    assert config.resolve_timezone("Australia/Brisbane") == "Australia/Brisbane"
    assert config.resolve_timezone("new york") == "America/New_York"
    assert config.resolve_timezone("not a place") == "UTC"
    assert config.resolve_timezone("") == "UTC"
