"""North-star suite pieces that one product must hold together: mail roles, session search, chat modes.

The full story (inbox → browse → code → away message) needs live mail and a browser. These tests lock the
contracts those steps share so the pieces cannot drift apart.
"""


def test_mail_accounts_have_a_life_role(client):
    from app import db

    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(mail_accounts)")}
    assert "life_role" in cols


def test_connections_doctor_says_docker_is_optional(client):
    r = client.get("/api/connections/doctor", headers={"X-Workbench": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["docker_required"] is False
    assert "notes" in body and body["notes"]


def test_plan_mode_is_in_the_system_prompt(client):
    from app.assistant import Ctx, system_prompt

    ctx = Ctx(channel="web", conversation_id=1, work_mode="plan")
    text = system_prompt(ctx, "hello")
    assert "Mode: plan" in text


def test_a_chat_app_message_shares_memory_with_chat(client):
    from app.assistant import Ctx, system_prompt

    text = system_prompt(Ctx(channel="telegram", conversation_id=1), "hello")
    assert "same memory and skills" in text


def test_build_mode_is_in_the_system_prompt(client):
    from app.assistant import Ctx, system_prompt

    ctx = Ctx(channel="web", conversation_id=1, work_mode="build")
    text = system_prompt(ctx, "hello")
    assert "Mode: build" in text
