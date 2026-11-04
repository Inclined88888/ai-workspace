"""A customer collects mail, a site change and team work without anyone retyping the name."""

from datetime import date

import pytest


@pytest.fixture
def clean(client):
    from app import db, projects

    projects.ensure()
    for t in ("project_items", "project_dismissed", "projects"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings WHERE key IN ('active_project_id', 'trade')")
    projects._invalidate()
    yield client


def test_mail_site_and_team_land_on_the_customer(clean):
    from app import projects
    from app.team import store

    c = clean
    p = c.post("/api/projects", json={"name": "Jones house", "kind": "client", "keywords": "Jones"}).json()
    assert projects.file_mail(1, [{"uid": "9", "subject": "Jones house plans", "from": "Sam <sam@example.com>"}]) == 1
    assert projects.file_mail(1, [{"uid": "9", "subject": "Jones house plans", "from": "Sam <sam@example.com>"}]) == 0
    assert projects.file_mail(1, [{"uid": "10", "subject": "Lunch", "from": "Pat"}]) == 0
    assert projects.file_site_change(3, 44, "Council", "Jones house: due Friday → Monday") is True

    store.ensure()
    today = date.today().isoformat()
    store.add_task({"title": "Chase the engineer on the Jones house", "project_id": p["id"], "due": today, "mine": True})

    detail = c.get(f"/api/projects/{p['id']}").json()
    assert any(i["title"] == "Jones house plans" for i in detail["items"]["mail"])
    assert any("Friday" in i["title"] for i in detail["items"]["update"])
    lines = c.get("/api/dashboard").json()["customers"]
    assert any(x["name"] == "Jones house" and x["what"].startswith("Mail:") for x in lines)
    assert any("due today" in x["what"] for x in lines)


def test_trade_is_a_choice_on_the_day(clean):
    c = clean
    assert c.get("/api/dashboard").json()["trade"] == ""
    assert c.put("/api/trade", json={"trade": "architect"}).status_code == 200
    assert c.get("/api/status").json()["trade"] == "architect"
    assert c.put("/api/trade", json={"trade": "nope"}).status_code == 400
