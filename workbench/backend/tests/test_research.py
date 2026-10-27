"""Deep Research end to end against a fake search engine and fake websites (served locally)."""

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

from conftest import SCRIPT, _port

PAGES = {
    "/a": ("Recycling rates 2025", "2026-03-01", "Australia recycled 63 percent of household waste in 2025 according to the national report. "
           "Kerbside collection covers most councils. " * 6),
    "/b": ("Waste data", "2019-05-01", "A 2019 estimate put household recycling at 58 percent nationally. Landfill levies differ by state. " * 6),
    "/a-copy": ("Recycling rates 2025 (syndicated)", "2026-03-01", "Australia recycled 63 percent of household waste in 2025 according to the national report. "
                "Kerbside collection covers most councils. " * 6),
    "/private/c": ("Members only", "", "Hidden page about recycling percent household waste. " * 6),
}


@pytest.fixture(scope="module")
def web():
    app = FastAPI()

    @app.get("/search")
    def search(q: str):
        base = f"http://127.0.0.1:{port}"
        return {"results": [{"title": PAGES[p][0], "url": base + p + "?utm_source=x", "content": PAGES[p][2][:100]} for p in PAGES]
                + [{"title": "dup", "url": base + "/a", "content": "same page again"}]}

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /private/\n")

    def page(p):
        title, date, body = PAGES[p]
        return HTMLResponse(f"<html><head><title>{title}</title><meta name='date' content='{date}'></head>"
                            f"<body><article><h1>{title}</h1><p>{body}</p></article></body></html>")

    for p in PAGES:
        app.get(p)(lambda p=p: page(p))
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.5)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def responder(msgs):
    system = msgs[0]["content"]
    if system.startswith("You plan web research"):
        return {"role": "assistant", "content": json.dumps({"sub_questions": [
            {"q": "What share of household waste is recycled in Australia?", "queries": ["australia household recycling rate"]},
            {"q": "How has it changed?", "queries": ["australia recycling trend"]}]})}
    if system.startswith("Extract up to 6 facts"):
        src = msgs[1]["content"]
        if "63 percent" in src:
            return {"role": "assistant", "content": json.dumps([
                {"claim": "63% of household waste was recycled in 2025", "quote": "recycled 63 percent of household waste in 2025", "kind": "figure"},
                {"claim": "Invented figure", "quote": "recycled 99 percent of everything", "kind": "figure"}])}
        return {"role": "assistant", "content": json.dumps([
            {"claim": "A 2019 estimate was 58%", "quote": "put household recycling at 58 percent", "kind": "figure"}])}
    if system.startswith("You are a careful research analyst"):
        return {"role": "assistant", "content": (
            "## Answer\nAbout 63% in 2025 [1], up from an estimated 58% in 2019 [2].\n"
            "## Key findings\n- 63% recycled in 2025 [1]\n- Earlier estimate 58% [2, 7]\n- Levies differ by state\n"
            "## Where sources disagree\nThe figures differ by year [1][2].\n"
            "## Inferences\nInference: the rate is rising.\n## Open questions\nRegional detail.")}
    return {"role": "assistant", "content": "ok"}


def wait(client, rid, statuses=("done", "error", "cancelled"), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/research/{rid}").json()
        if r["status"] in statuses:
            return r
        time.sleep(0.1)
    raise AssertionError(r)


def test_full_research_pipeline(client, web, monkeypatch):
    from app import config, research

    monkeypatch.setattr(config, "SEARXNG_URL", web)
    monkeypatch.setattr(research, "_public_host", lambda url: url.startswith(web))  # the fake sites run on localhost
    monkeypatch.setattr(research, "_proxied", lambda scheme: True)
    # every fake site shares one host; treat each top path as its own site so the per-site caps do not hide pages
    monkeypatch.setattr(research, "domain", lambda url: "site-" + url.split("/")[3].split("?")[0].split("-")[0])
    SCRIPT[:] = [responder] * 50
    try:
        rid = client.post("/api/research", json={"question": "What share of Australian household waste is recycled?"}).json()["id"]
        r = wait(client, rid)
        assert r["status"] == "done", r["error"]
        assert len(r["plan"]) == 2
        urls = [s["url"] for s in r["sources"]]
        assert not any("/private/" in u for u in urls)
        assert any("robots.txt" in s["why"] for s in r["skipped"]), (r["skipped"], [s["url"] for s in r["sources"]])                       # robots.txt honoured
        assert len(urls) == 2                                                             # tracking params and syndicated copy folded
        # the page and its syndicated copy score the same, so either may be kept; the other is folded into it
        a = next(s for s in r["sources"] if "/a" in s["url"])
        assert a["freshness"] == "recent" and a["date"] == "2026-03-01" and a["duplicates"]
        old = next(s for s in r["sources"] if "/b" in s["url"])
        assert old["freshness"] == "older"
        assert [c["claim"] for c in a["claims"]] == ["63% of household waste was recycled in 2025"]  # invented quote dropped
        assert r["stats"]["claims_dropped_unquoted"] == 1
        assert r["stats"]["invalid_removed"] == 1 and "[2, 7]" not in r["report"] and "[2]" in r["report"]
        assert r["stats"]["unsourced_findings"] == 1 and "*(no source given)*" in r["report"]
        for section in ("## Answer", "## Key findings", "## Where sources disagree", "## Inferences", "## Open questions", "## Sources"):
            assert section in r["report"]
        assert "found_by" in r["sources"][0] and r["sources"][0]["found_by"][0].startswith("searxng")
    finally:
        SCRIPT.clear()


def test_research_can_be_cancelled_and_retried(client, web, monkeypatch):
    from app import config, research

    monkeypatch.setattr(config, "SEARXNG_URL", web)
    monkeypatch.setattr(research, "_public_host", lambda url: url.startswith(web))
    monkeypatch.setattr(research, "_proxied", lambda scheme: True)

    def slow_plan(msgs):
        time.sleep(1.0)
        return responder(msgs)

    SCRIPT[:] = [slow_plan] + [responder] * 50
    try:
        rid = client.post("/api/research", json={"question": "What share of Australian household waste is recycled?"}).json()["id"]
        time.sleep(0.2)
        assert client.post(f"/api/research/{rid}/cancel").status_code == 200
        r = wait(client, rid)
        assert r["status"] == "cancelled"
        assert client.post(f"/api/research/{rid}/cancel").status_code == 409
        assert client.post(f"/api/research/{rid}/retry").json()["status"] == "running"
        assert wait(client, rid)["status"] == "done"
    finally:
        SCRIPT.clear()


def test_helpers():
    from app.research import canonical, check_citations, credibility, freshness, near_duplicate

    assert canonical("https://www.Example.com/x/?utm_source=a&id=3#top") == "https://example.com/x?id=3"
    assert credibility("https://www.abs.gov.au/x")[0] == "higher" and credibility("https://reddit.com/r/x")[0] == "lower"
    assert credibility("https://someone.substack.com/p")[0] == "lower" and credibility("https://example.com")[0] == "medium"
    assert freshness("not a date") == ("unknown", None)
    report, stats = check_citations("## Key findings\n- a [1]\n- b [3]\n", 2)
    assert stats == {"cited": [1], "invalid_removed": 1, "unsourced_findings": 1}
    text = "word " * 50 + "unique tail"
    assert near_duplicate(text, text + " extra") and not near_duplicate("alpha beta gamma delta epsilon zeta", "one two three four five six")


def test_relevance_matches_word_forms():
    """Regression: a page about 'examples' of a 'domain' scored zero for 'example domains used' and was dropped."""
    from app.research import relevance

    assert relevance("What are example domains used for?", "This domain is for use in documentation examples.") >= 0.66
    assert relevance("tax deadlines", "Gardening tips for spring") == 0


# Rounds, gaps, the evidence ledger, independent verification, saving and monitoring
PAGES2 = {
    "/blog": ("Recycling blog", "2026-01-10", "Our estimate is that councils recycle 40 percent of kerbside waste this year. " * 6),
    "/gov": ("National waste statistics", "2026-02-01", "Official statistics show councils recycle 42 percent of kerbside waste in 2025. " * 6),
}


@pytest.fixture(scope="module")
def web2():
    app = FastAPI()
    searched: list[str] = []

    @app.get("/search")
    def search(q: str):
        searched.append(q)
        base = f"http://127.0.0.1:{port}"
        path = "/gov" if "official" in q else "/blog"
        return {"results": [{"title": PAGES2[path][0], "url": base + path, "content": PAGES2[path][2][:100]}]}

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse("User-agent: *\nAllow: /\n")

    def page(p):
        title, date, body = PAGES2[p]
        return HTMLResponse(f"<html><head><title>{title}</title><meta name='date' content='{date}'></head>"
                            f"<body><article><h1>{title}</h1><p>{body}</p></article></body></html>")

    for p in PAGES2:
        app.get(p)(lambda p=p: page(p))
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.5)
    yield f"http://127.0.0.1:{port}", searched
    server.should_exit = True


def responder2(msgs):
    system, user = msgs[0]["content"], msgs[1]["content"] if len(msgs) > 1 else ""
    if system.startswith("You plan web research"):
        return {"role": "assistant", "content": json.dumps({"sub_questions": [{"q": "What share of kerbside waste do councils recycle?",
                                                                                "queries": ["council kerbside recycling share"]}]})}
    if system.startswith("Extract up to 6 facts"):
        if "40 percent" in user:
            return {"role": "assistant", "content": json.dumps([{"claim": "Councils recycle 40% of kerbside waste", "quote": "councils recycle 40 percent of kerbside waste", "kind": "figure"}])}
        return {"role": "assistant", "content": json.dumps([{"claim": "Councils recycled 42% of kerbside waste in 2025", "quote": "councils recycle 42 percent of kerbside waste in 2025", "kind": "figure"}])}
    if system.startswith("You review the evidence gathered"):
        if "c2" not in user:
            return {"role": "assistant", "content": json.dumps({"enough": False, "gaps": [{"q": "An official figure", "queries": ["official kerbside recycling statistics"]}], "contradictions": []})}
        return {"role": "assistant", "content": json.dumps({"enough": True, "gaps": [], "contradictions": [["c1", "c2"], ["c1", "c9"]]})}
    if system.startswith("You independently check claims"):
        out = []
        if "c1:" in user:
            out.append({"id": "c1", "verdict": "not_supported", "why": "the passage is the blog's own estimate"})
        if "c2:" in user:
            out.append({"id": "c2", "verdict": "supported", "why": "the passage states it"})
        return {"role": "assistant", "content": json.dumps(out)}
    if system.startswith("You are a careful research analyst"):
        return {"role": "assistant", "content": (
            "## Answer\nAbout 42% in 2025 [2]; a blog estimates 40% [1].\n## Key findings\n- 42% officially [2]\n"
            "## Where sources disagree\n40% [1] against 42% [2].\n## Inferences\n- The true figure is probably near 42%.\n## Open questions\nTrend.")}
    return {"role": "assistant", "content": "ok"}


@pytest.fixture()
def r2(client, web2, monkeypatch):
    from app import config, research

    base, searched = web2
    monkeypatch.setattr(config, "SEARXNG_URL", base)
    monkeypatch.setattr(research, "_public_host", lambda url: url.startswith(base))
    monkeypatch.setattr(research, "_proxied", lambda scheme: True)
    monkeypatch.setattr(research, "domain", lambda url: "site-" + url.split("/")[3].split("?")[0])
    real = research.credibility
    monkeypatch.setattr(research, "credibility", lambda url: ("higher", "government") if "/gov" in url else real(url))
    searched.clear()
    SCRIPT[:] = [responder2] * 80
    yield client, searched
    SCRIPT.clear()


def test_a_gap_starts_a_bounded_second_round_and_the_ledger_records_every_claim(r2):
    from app import db

    client, searched = r2
    rid = client.post("/api/research", json={"question": "What share of kerbside waste do councils recycle?", "depth": 3}).json()["id"]
    r = wait(client, rid, timeout=30)
    assert r["status"] == "done", r["error"]
    assert len(r["rounds"]) == 2 and r["rounds"][0]["thin"] and r["rounds"][1]["new_sources"] == 1, r["rounds"]
    assert "official kerbside recycling statistics" in searched  # the gap became the next round's search
    assert "depth checks" in r["stats"]["stopped_because"], r["stats"]
    ev = {e["source_n"]: e for e in r["evidence"] if e["source_n"]}
    blog, gov = ev[1], ev[2]
    page = PAGES2["/gov"][2]
    assert gov["basis"] == "fact" and gov["verification"] == "supported" and gov["excerpt"] in page and gov["location"].startswith("characters")
    assert gov["published"] == "2026-02-01" and gov["retrieved_at"] and gov["round"] == 2 and gov["url"].endswith("/gov")
    assert blog["basis"] == "uncertain" and blog["verification"] == "not_supported" and blog["confidence"] < gov["confidence"]
    assert blog["contradicts"] == [gov["id"]] and gov["contradicts"] == [blog["id"]]  # a contradiction naming an unknown claim (c9) is ignored
    inf = [e for e in r["evidence"] if e["basis"] == "inference"]
    assert len(inf) == 1 and inf[0]["source_n"] is None and "near 42%" in inf[0]["claim"]
    assert "## Limitations" in r["report"] and "2 of up to 2 round(s)" in r["report"] and "not supported by their passage" in r["report"]
    assert r["stats"]["verification"]["supported"] == 1 and r["stats"]["contradictions"] == 1

    from app import projects

    p = projects.create("Waste project")
    doc = client.post(f"/api/research/{rid}/save", json={"project_id": p["id"]}).json()
    assert doc["project_id"] == p["id"] and "## Evidence" in doc["body"] and "| inference |" in doc["body"]
    assert db.one("SELECT COUNT(*) AS n FROM evidence WHERE research_id = ? AND project_id = ?", rid, p["id"])["n"] == 3

    r = client.post(f"/api/research/{rid}/monitor", json={"cron": "0 8 * * 1"}).json()
    topic = db.one("SELECT * FROM topics WHERE id = ?", r["monitor"]["id"])
    assert json.loads(topic["sources"]) == [f"search:{r['question']}"] and topic["project_id"] == p["id"] and topic["notify"] == "inbox"
    assert client.post(f"/api/research/{rid}/monitor", json={}).status_code == 409
    client.delete(f"/api/research/{rid}")
    assert not db.one("SELECT 1 FROM topics WHERE id = ?", topic["id"]) and not db.one("SELECT 1 FROM evidence WHERE research_id = ?", rid)
    projects.set_status(p["id"], "deleted")
    projects.purge(p["id"], "Waste project")


def test_rounds_are_bounded_by_depth(r2):
    client, searched = r2
    rid = client.post("/api/research", json={"question": "What share of kerbside waste do councils recycle?", "depth": 1}).json()["id"]
    r = wait(client, rid, timeout=30)
    assert r["status"] == "done", r["error"]
    assert len(r["rounds"]) == 1 and "round limit (1)" in r["stats"]["stopped_because"]
    assert "official kerbside recycling statistics" not in searched
    assert "Evidence is still thin" in r["report"]


def test_the_depth_gate():
    from app import research

    one = {"url": "http://a.example/x", "credibility": "medium", "subs": {0}, "claims": [{"claim": "x"}]}
    two = {"url": "http://b.gov/y", "credibility": "higher", "subs": {0}, "claims": [{"claim": "y"}]}
    subs = [{"q": "main"}]
    assert "claims from fewer than two sites" in research.needs_more([one], subs)
    assert research.needs_more([one, two], subs) == []
    assert any("sub-question" in w for w in research.needs_more([one, two], subs + [{"q": "uncovered"}]))
