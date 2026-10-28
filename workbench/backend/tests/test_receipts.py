"""Receipts, claim checks and the per model reliability record."""

from conftest import SCRIPT, events, tool_call


def test_tool_results_carry_a_receipt_checked_against_the_database(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Book dentist", "due": "2026-11-20"}), {"role": "assistant", "content": "Task created successfully!"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add book dentist due 2026-11-20"}))
    r = next(e for e in ev if e["type"] == "tool_result")["receipt"]
    assert r["verified"] is True and "Book dentist" in r["evidence"] and "2026-11-20" in r["evidence"]
    assert not [e for e in ev if e["type"] == "claim_check"]  # backed claim passes without a retry


def test_a_tool_that_returns_but_changed_nothing_fails_its_check(client, monkeypatch):
    from app import assistant, db

    monkeypatch.setattr(assistant.TOOL_MAP["create_task"], "fn", lambda title, **kw: {"id": 999999, "title": title})
    SCRIPT[:] = [tool_call("create_task", {"title": "Ghost task"}), {"role": "assistant", "content": "That did not work."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add ghost task"}))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"] is False and res["receipt"]["verified"] is False and "could not be confirmed" in res["preview"]
    assert db.one("SELECT outcome FROM tool_runs ORDER BY id DESC LIMIT 1")["outcome"] == "check_failed"


def test_false_done_is_withdrawn_and_corrected(client):
    from app import db

    SCRIPT[:] = [{"role": "assistant", "content": "I've added the task to your list."},
                 lambda msgs: {"role": "assistant", "content": "Sorry, I have not changed anything yet. Want me to add it?"
                               if "Automatic check" in msgs[-1]["content"] else "NO NUDGE"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add buy milk"}))
    assert any(e["type"] == "claim_check" and e["retrying"] for e in ev)
    final = next(e for e in ev if e["type"] == "message")["content"]
    assert final.startswith("Sorry") and "Not verified" not in final
    msgs = client.get(f"/api/conversations/{ev[0]['conversation_id']}").json()["messages"]
    # the false answer never stays in the record; only the hidden check message quotes it
    assert not any("I've added the task" in m["content"] for m in msgs if m["role"] == "assistant")
    assert [m["name"] for m in msgs if "I've added the task" in m["content"]] == ["claim_check"]
    assert db.one("SELECT outcome FROM turn_checks ORDER BY id DESC LIMIT 1")["outcome"] == "fixed"


def test_a_model_that_acts_after_the_nudge_gets_a_backed_answer(client):
    SCRIPT[:] = [{"role": "assistant", "content": "Done! Reminder set for Friday."},
                 tool_call("create_task", {"title": "Call mum", "due": "2026-10-30"}),
                 {"role": "assistant", "content": "I've added it: Call mum, due Friday."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "remind me to call mum friday"}))
    assert next(e for e in ev if e["type"] == "tool_result")["receipt"]["verified"] is True
    assert "Not verified" not in next(e for e in ev if e["type"] == "message")["content"]


def test_a_repeated_false_claim_reaches_the_user_flagged(client):
    from app import db

    SCRIPT[:] = [{"role": "assistant", "content": "I have sent the email to Sam."}, {"role": "assistant", "content": "I have sent the email to Sam."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "email sam"}))
    final = next(e for e in ev if e["type"] == "message")["content"]
    assert "**Not verified:**" in final
    assert db.one("SELECT outcome FROM turn_checks ORDER BY id DESC LIMIT 1")["outcome"] == "unbacked"


def test_claim_detection_is_narrow():
    from app.receipts import claims

    yes = ["I've added the task to your list.", "Task created successfully!", "Added.", "Your meeting has been scheduled for 9am.",
           "Reminder set for Friday.", "I have sent the email to Sam.", "The reply was sent to Ana.", "✅ Saved to your notes."]
    no = ["Would you like me to add it?", "I can create that task for you.", "Once you approve, it will be scheduled.",
          "You have 2 completed tasks.", "Here is the updated list:", "I have not created anything yet.",
          "I couldn't save the note because the title was missing.", "The page says tickets were sold out."]
    assert all(claims(s) for s in yes), [s for s in yes if not claims(s)]
    assert not any(claims(s) for s in no), [s for s in no if claims(s)]


def test_reliability_is_measured_per_model_and_shown_with_models(client):
    rel = client.get("/api/reliability").json()["models"]
    fake = next(m for m in rel if m["model"].endswith("/fake-model"))
    assert fake["calls"] > 0 and 0 < fake["success_rate"] <= 1 and fake["false_claims_caught"] >= 2
    model = next(m for m in client.get("/api/models").json()["models"] if m["ref"] == fake["model"])
    assert model["reliability"]["calls"] == fake["calls"]


def test_an_unreachable_model_is_a_recorded_failure_not_a_crash(client):
    from app import db

    pid = client.post("/api/providers", json={"name": "Down", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "models": "m"}).json()["id"]
    ev = events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{pid}/m"}))
    assert any(e.get("type") == "error" and "unreachable" in e["error"] for e in ev)
    assert db.one("SELECT outcome FROM turn_checks WHERE model = ? ORDER BY id DESC", f"{pid}/m")["outcome"] == "model_error"
    client.delete(f"/api/providers/{pid}")


def test_an_answer_that_misstates_what_was_saved_is_caught(client):
    """Benchmark leak, qwen3:4b: saved priority 3 but told the user 'priority: low'."""
    from app import db

    leak = "Task added! ID: 11. Title: clean the gutters. Due: 2026-11-08 (priority: low)."
    SCRIPT[:] = [tool_call("create_task", {"title": "clean the gutters", "due": "2026-11-08", "priority": 3}),
                 {"role": "assistant", "content": leak},
                 lambda msgs: {"role": "assistant", "content": "Correction: it was saved as high priority."
                               if "saved priority is high" in msgs[-1]["content"] else "NO NUDGE"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Add 'clean the gutters' for 2026-11-08, low priority."}))
    check = next(e for e in ev if e["type"] == "claim_check")
    assert check["retrying"] and "priority: low" in check["claim"]
    assert next(e for e in ev if e["type"] == "message")["content"].startswith("Correction")
    # stated twice: reaches the user flagged with the real value
    SCRIPT[:] = [tool_call("create_task", {"title": "sweep the deck", "due": "2026-11-09", "priority": 3}),
                 {"role": "assistant", "content": "Added, due 2026-11-10, priority: low."},
                 {"role": "assistant", "content": "Added, due 2026-11-10, priority: low."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add sweep the deck"}))
    final = next(e for e in ev if e["type"] == "message")["content"]
    assert "**Not verified:**" in final and "saved" in final
    assert db.one("SELECT outcome FROM turn_checks ORDER BY id DESC LIMIT 1")["outcome"] == "unbacked"


def test_a_correct_restatement_passes(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Oil change", "due": "2026-12-05", "priority": 1}),
                 {"role": "assistant", "content": "Added 'Oil change', due 2026-12-05, low priority."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "oil change dec 5 low priority"}))
    assert not [e for e in ev if e["type"] == "claim_check"]
