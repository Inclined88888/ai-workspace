"""Tests for APP_SECRET and TOOLS_API_KEY generation and persistence."""
import os
import tempfile
from pathlib import Path


def test_generate_once_then_reuse_app_secret(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.delenv("APP_SECRET", raising=False)

        from app import config
        import importlib
        importlib.reload(config)

        secret1 = config.APP_SECRET
        assert len(secret1) >= 32
        assert (data_dir / "app_secret").exists()
        assert (data_dir / "app_secret").stat().st_mode & 0o777 == 0o600

        importlib.reload(config)
        secret2 = config.APP_SECRET
        assert secret2 == secret1


def test_generate_once_then_reuse_tools_api_key(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.delenv("TOOLS_API_KEY", raising=False)

        from app import config
        import importlib
        importlib.reload(config)

        key1 = config.TOOLS_API_KEY
        assert len(key1) >= 24
        assert (data_dir / "tools_api_key").exists()
        assert (data_dir / "tools_api_key").stat().st_mode & 0o777 == 0o600

        importlib.reload(config)
        key2 = config.TOOLS_API_KEY
        assert key2 == key1


def test_explicit_env_wins_app_secret(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        explicit_secret = "e" * 40
        monkeypatch.setenv("APP_SECRET", explicit_secret)

        from app import config
        import importlib
        importlib.reload(config)

        assert config.APP_SECRET == explicit_secret
        assert not (data_dir / "app_secret").exists()


def test_explicit_env_wins_tools_api_key(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        explicit_key = "k" * 30
        monkeypatch.setenv("TOOLS_API_KEY", explicit_key)

        from app import config
        import importlib
        importlib.reload(config)

        assert config.TOOLS_API_KEY == explicit_key
        assert not (data_dir / "tools_api_key").exists()


def test_persisted_secret_used_when_no_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.delenv("APP_SECRET", raising=False)
        monkeypatch.delenv("TOOLS_API_KEY", raising=False)

        persisted_secret = "p" * 40
        persisted_key = "t" * 30
        (data_dir / "app_secret").write_text(persisted_secret)
        (data_dir / "app_secret").chmod(0o600)
        (data_dir / "tools_api_key").write_text(persisted_key)
        (data_dir / "tools_api_key").chmod(0o600)

        from app import config
        import importlib
        importlib.reload(config)

        assert config.APP_SECRET == persisted_secret
        assert config.TOOLS_API_KEY == persisted_key