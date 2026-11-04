"""B2 knowledge-graph memory: multi-hop traversal with direction and relationship filters, exposed to the user (API)
and to the model (explore_graph tool), with sensitive entities kept from the model like recall does."""

import json

from conftest import CALLS, SCRIPT, events, tool_call


def _graph():
    from app.knowledge import graph

    ada = graph.upsert("person", "Ada Lovelace")
    bob = graph.upsert("person", "Bob Smith")
    acme = graph.upsert("organisation", "Acme Pty Ltd")
    apollo = graph.upsert("project", "Apollo")
    vault = graph.upsert("project", "Vault Migration", privacy="sensitive")
    graph.link(ada["id"], "works_at", acme["id"])
    graph.link(bob["id"], "works_at", acme["id"])
    graph.link(ada["id"], "assigned_to", apollo["id"])
    graph.link(apollo["id"], "part_of", acme["id"])
    graph.link(bob["id"], "assigned_to", vault["id"])
    return ada, bob, acme, apollo, vault


def test_traverse_follows_direction_rel_and_depth(client):
    ada, bob, acme, apollo, vault = _graph()
    names = lambda d: sorted(n["name"] for n in d["nodes"])  # noqa: E731

    out1 = client.get("/api/knowledge/graph/traverse", params={"eid": ada["id"], "depth": 1, "direction": "out"}).json()
    assert names(out1) == ["Acme Pty Ltd", "Apollo"]
    # incoming works_at edges into Acme: its employees, not the project that is part_of it
    staff = client.get("/api/knowledge/graph/traverse", params={"eid": acme["id"], "depth": 1, "direction": "in", "rel": "works_at"}).json()
    assert names(staff) == ["Ada Lovelace", "Bob Smith"]
    # the rel filter holds in both directions (the old query lost it on outgoing edges: "src = ? OR dst = ? AND rel = ?")
    both = client.get("/api/knowledge/graph/traverse", params={"eid": ada["id"], "depth": 1, "direction": "both", "rel": "works_at"}).json()
    assert names(both) == ["Acme Pty Ltd"]
    # two hops from Ada: Acme and Apollo, then Bob through Acme, with the path that got there
    two = client.get("/api/knowledge/graph/traverse", params={"eid": ada["id"], "depth": 2}).json()
    bob_node = next(n for n in two["nodes"] if n["name"] == "Bob Smith")
    assert bob_node["hops"] == 2 and [p["rel"] for p in bob_node["path"]] == ["works_at", "works_at"]
    # bounded, and bad input refused
    assert client.get("/api/knowledge/graph/traverse", params={"eid": ada["id"], "depth": 99}).json()["depth"] == 4
    assert client.get("/api/knowledge/graph/traverse", params={"eid": ada["id"], "direction": "up"}).status_code == 400
    assert client.get("/api/knowledge/graph/traverse", params={"eid": 999999}).status_code == 404


def test_the_model_answers_from_the_graph_and_never_sees_sensitive_entities(client):
    from app import assistant

    _graph()
    t = assistant.explore_graph("Bob Smith", depth=1)
    assert t["found"] and t["start"]["name"] == "Bob Smith"
    assert [r["name"] for r in t["related"]] == ["Acme Pty Ltd"]  # Vault Migration is sensitive
    assert assistant.explore_graph("Vault Migration")["found"] is False
    assert assistant.explore_graph("Nobody Here")["found"] is False

    SCRIPT[:] = [tool_call("explore_graph", {"name": "Acme Pty Ltd", "direction": "in", "rel": "works_at", "depth": 1}),
                 lambda msgs: {"role": "assistant", "content": "Ada and Bob work at Acme." if "Ada Lovelace" in msgs[-1]["content"]
                               and "Bob Smith" in msgs[-1]["content"] else "GRAPH NOT USED"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "who works at Acme?"}))
    res = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "explore_graph")
    assert res["ok"]
    assert next(e for e in ev if e["type"] == "message")["content"] == "Ada and Bob work at Acme."
    spec = next(t for c in CALLS for t in (c.get("tools") or []) if t["function"]["name"] == "explore_graph")
    assert set(spec["function"]["parameters"]["required"]) == {"name"}
    assert assistant.TOOL_MAP["explore_graph"].effect == "read"
    json.dumps(t)  # the tool result is plain JSON
