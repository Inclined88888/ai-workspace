"""L4 self host: with nothing configured the backend makes no call to models.dev; Settings turns the daily update on."""


def test_model_catalog_update_is_off_until_turned_on(client, monkeypatch):
    from app import catalog

    calls = []
    monkeypatch.delenv("MODEL_CATALOG_REFRESH", raising=False)
    monkeypatch.setattr(catalog, "download", lambda: calls.append(1) or {})
    monkeypatch.setattr(catalog, "refresh_in_background", lambda: catalog.refresh())

    s = client.get("/api/settings").json()
    assert s["model_catalog_refresh"] is False and s["model_catalog_env"] is False
    assert catalog.refresh() is False and calls == []  # default: nothing leaves the machine

    s = client.put("/api/settings", json={"model_catalog_refresh": True}).json()
    assert s["model_catalog_refresh"] is True
    assert calls == [1]  # turning it on fetches (the empty fake list is rejected, so the bundled one stays)

    s = client.put("/api/settings", json={"model_catalog_refresh": False}).json()
    assert s["model_catalog_refresh"] is False
    assert catalog.refresh() is False and calls == [1]


def test_the_environment_variable_overrides_the_setting(client, monkeypatch):
    from app import catalog

    monkeypatch.setenv("MODEL_CATALOG_REFRESH", "0")
    client.put("/api/settings", json={"model_catalog_refresh": True})
    assert catalog.auto_refresh() is False and client.get("/api/settings").json()["model_catalog_env"] is True
    monkeypatch.setenv("MODEL_CATALOG_REFRESH", "1")
    client.put("/api/settings", json={"model_catalog_refresh": False})
    assert catalog.auto_refresh() is True
