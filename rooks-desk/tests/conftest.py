import re

import pytest


@pytest.fixture(autouse=True)
def desk_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOKS_DESK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROOKS_DESK_FOLDERS", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    for k in ("WORKBENCH_URL", "ROOKS_DESK_PROVIDER", "ROOKS_DESK_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def el(name: str, action: str = "click", **extra):
    """A scripted step that finds element NAME in the prompt's list and acts on its id."""
    def step(prompt: str):
        m = re.search(r"\[(\d+)\] [a-z ]+ '" + re.escape(name), prompt)
        assert m, f"element {name!r} not in prompt:\n{prompt[-1500:]}"
        return {"thought": f"{action} {name}", "action": action, "args": {"id": int(m.group(1)), **extra}}
    return step
