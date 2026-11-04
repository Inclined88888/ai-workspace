"""B3 packs from the terminal, over real processes: list, info, add and remove change the same state the app's Packs
page shows. Adding runs the app's skill security gate."""

import pytest

from conftest import rooks
from fakes import FakeModel
from live_app import app, setup  # noqa: F401 - app is a fixture

pytest.importorskip("uvicorn")


def test_rooks_pack_lists_adds_and_removes_the_same_packs_as_the_app(project, app):  # noqa: F811
    model = FakeModel([])
    try:
        env = {"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": setup(app, model.url)}
        r = rooks(project, "pack", "list", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        for pid in ("trade", "legal", "study"):
            assert f"  {pid} " in r.stdout

        r = rooks(project, "pack", "add", "trade", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "added" in r.stdout
        web = {p["id"]: p for p in app.call("GET", "/api/packs")}
        assert web["trade"]["installed"] is True  # the app's Packs page sees it

        info = rooks(project, "pack", "info", "trade", env=env)
        assert info.returncode == 0 and "added" in info.stdout and "skill:" in info.stdout

        r = rooks(project, "pack", "remove", "trade", env=env)
        assert r.returncode == 0 and "removed" in r.stdout
        assert {p["id"]: p for p in app.call("GET", "/api/packs")}["trade"]["installed"] is False

        missing = rooks(project, "pack", "add", "nope", env=env)
        assert missing.returncode == 1 and missing.stderr.startswith("rooks: ")
        assert rooks(project, "pack", "add", env=env).returncode == 2  # needs an id
    finally:
        model.close()
