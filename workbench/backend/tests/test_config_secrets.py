"""APP_SECRET and TOOLS_API_KEY: generated once into the data folder when not given, reused after, env always wins.

The helper is called directly; reloading app.config would point the whole test session at another database.
"""
import os
import stat

import pytest


def test_generated_once_then_reused(client, tmp_path, monkeypatch):  # client: the session env (DATA_DIR, secrets) before app.config loads
    from app.config import _load_or_generate_secret

    monkeypatch.delenv("X_SECRET", raising=False)
    first = _load_or_generate_secret("X_SECRET", tmp_path, 32, "x_secret")
    assert len(first) >= 32
    path = tmp_path / "x_secret"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _load_or_generate_secret("X_SECRET", tmp_path, 32, "x_secret") == first


def test_explicit_env_wins_and_a_weak_one_is_refused(client, tmp_path, monkeypatch):  # client: the session env (DATA_DIR, secrets) before app.config loads
    from app.config import _load_or_generate_secret

    monkeypatch.setenv("X_SECRET", "s" * 40)
    assert _load_or_generate_secret("X_SECRET", tmp_path, 32, "x_secret") == "s" * 40
    assert not (tmp_path / "x_secret").exists()
    monkeypatch.setenv("X_SECRET", "short")
    with pytest.raises(RuntimeError):
        _load_or_generate_secret("X_SECRET", tmp_path, 32, "x_secret")


def test_a_truncated_secret_file_is_replaced(client, tmp_path, monkeypatch):  # client: the session env (DATA_DIR, secrets) before app.config loads
    from app.config import _load_or_generate_secret

    monkeypatch.delenv("X_SECRET", raising=False)
    (tmp_path / "x_secret").write_text("abc")
    assert len(_load_or_generate_secret("X_SECRET", tmp_path, 32, "x_secret")) >= 32
