"""Research should explain missing search setup before creating a failed job."""


def test_research_requires_search_provider(client, monkeypatch):
    from app import search

    monkeypatch.setattr(search, "provider", lambda: "")
    setup = client.get("/api/research/setup")
    assert setup.status_code == 200
    assert setup.json() == {"ready": False, "search_provider": ""}
    before = len(client.get("/api/research").json())
    response = client.post("/api/research", json={"question": "What changed this week?"})
    assert response.status_code == 400
    assert "Chat settings" in response.json()["detail"]
    assert len(client.get("/api/research").json()) == before
