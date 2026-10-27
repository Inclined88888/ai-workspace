"""The capabilities catalog reports real state: unconfigured things are not "ready", permissions turned off show as
off, unbuilt things are listed as not built, and the last success and failure come from the tool log."""


def test_catalog_reflects_real_configuration_and_permissions(client):
    from app import db

    c = {x["id"]: x for x in client.get("/api/capabilities").json()["capabilities"]}
    assert c["chat"]["status"] == "ready"                       # the test provider is connected
    assert c["web"]["status"] == "needs_setup" and "Tavily" in c["web"]["needs"]   # no search key in tests
    assert c["computer"]["status"] == "needs_setup"             # no companion paired
    assert c["phone"]["status"] == "optional"
    assert c["email"]["status"] == "needs_setup"
    assert all(x["status"] == "not_built" for x in c.values() if x["kind"] == "not_built")
    assert {"two_way_phone", "send_email", "plugins"} <= {k for k, x in c.items() if x["status"] == "not_built"}
    client.put("/api/permissions", json={"groups": {"tasks": "off"}})
    assert {x["id"]: x for x in client.get("/api/capabilities").json()["capabilities"]}["tasks"]["status"] == "off"
    client.put("/api/permissions", json={"groups": {"tasks": "on"}})
    db.run("INSERT INTO tool_runs(at, tool, actor, ok, ms, args, error) VALUES (?, 'list_tasks', 'assistant', 1, 5, '', '')", "2099-01-01T10:00:00+00:00")
    db.run("INSERT INTO tool_runs(at, tool, actor, ok, ms, args, error) VALUES (?, 'create_task', 'assistant', 0, 5, '', 'Invalid arguments')", "2099-01-01T11:00:00+00:00")
    t = {x["id"]: x for x in client.get("/api/capabilities").json()["capabilities"]}["tasks"]
    assert t["last_ok"] == "2099-01-01T10:00:00+00:00" and t["last_error"]["error"] == "Invalid arguments"
    db.run("DELETE FROM tool_runs WHERE at LIKE '2099-%'")
