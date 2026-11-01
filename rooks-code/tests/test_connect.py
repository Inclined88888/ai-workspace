"""Task 01: rooks works in any terminal with zero guessing.

Discovery order (app.json -> localhost:3100 probe -> env keys -> Ollama), `rooks login` writes the TOML and stores the
key without printing it, and `rooks doctor` prints every check with a fix instead of dying on the first failure."""

import io
import sys
from pathlib import Path

import pytest

from rooks_code import cli, config, login


@pytest.fixture
def no_network(monkeypatch):
    """Nothing answers: no app, no Docker Workbench, no Ollama."""
    monkeypatch.setattr(config, "app_link", lambda: {})
    monkeypatch.setattr(config, "workbench_probe", lambda url="": {})
    monkeypatch.setattr(config, "_ollama_up", lambda *a, **k: False)
    for name in config.KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("WORKBENCH_URL", raising=False)
    monkeypatch.delenv("ROOKS_PROVIDER", raising=False)
    monkeypatch.delenv("ROOKS_MODEL", raising=False)
    monkeypatch.delenv("ROOKS_BASE_URL", raising=False)


def test_discovery_picks_docker_workbench_on_3100_when_no_app(no_network, tmp_path, monkeypatch):
    """A Docker Workbench that answers /healthz is used with nothing configured."""
    monkeypatch.setattr(config, "workbench_probe", lambda url="": {"url": "http://localhost:3100"})
    monkeypatch.setattr(config, "app_has_models", lambda link: False)
    s = config.resolve({}, tmp_path, {})
    assert s.provider == "workbench" and s.base_url == "http://localhost:3100/v1"


def test_every_builtin_provider_has_base_url_and_key_env():
    """Task 04: the built-in table is complete; delegates and keyless locals are the documented exceptions."""
    for name, p in config.PROVIDERS.items():
        assert p.get("kind") in ("openai", "anthropic", "delegate"), f"{name}: bad kind"
        if p.get("kind") == "delegate" or p.get("local"):
            continue
        if name in ("openai-compatible", "workbench"):
            continue  # base_url comes from the user or runtime discovery
        assert p.get("base_url"), f"{name}: no base_url"
        assert p.get("key_env"), f"{name}: no key_env"
    for name in ("nvidia", "moonshot", "cerebras", "together", "fireworks", "zai"):
        assert name in config.PROVIDERS, f"{name} missing"
        assert config.PROVIDERS[name]["base_url"].startswith("https://")


def test_provider_for_key_new_prefixes():
    assert config.provider_for_key("nvapi-abc") == "nvidia"
    assert config.provider_for_key("csk-abc") == "cerebras"
    assert config.provider_for_key("fw_abc") == "fireworks"


def test_discovery_order_app_beats_probe_beats_env_key(no_network, tmp_path, monkeypatch):
    # app wins over the probe
    monkeypatch.setattr(config, "app_link", lambda: {"url": "http://app:9", "key": "k"})
    monkeypatch.setattr(config, "app_has_models", lambda link: True)
    monkeypatch.setattr(config, "workbench_probe", lambda url="": {"url": "http://localhost:3100"})
    s = config.resolve({}, tmp_path, {})
    assert s.base_url == "http://app:9/v1"
    # probe wins over an env key
    monkeypatch.setattr(config, "app_link", lambda: {})
    monkeypatch.setattr(config, "app_has_models", lambda link: False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    s = config.resolve({}, tmp_path, {})
    assert s.provider == "workbench"
    # no probe: the env key wins
    monkeypatch.setattr(config, "workbench_probe", lambda url="": {})
    s = config.resolve({}, tmp_path, {})
    assert s.provider == "groq"


def test_stored_key_is_used_when_nothing_is_exported(no_network, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_stored_keys", lambda: {"GROQ_API_KEY": "gsk_stored"})
    s = config.resolve({}, tmp_path, {})
    assert s.provider == "groq" and s.api_key == "gsk_stored"


def test_provider_for_key_prefixes():
    assert config.provider_for_key("sk-ant-abc") == "anthropic"
    assert config.provider_for_key("sk-or-v1-abc") == "openrouter"
    assert config.provider_for_key("wb_abc") == "workbench"
    assert config.provider_for_key("gsk_abc") == "groq"
    assert config.provider_for_key("AIzaabc") == "gemini"
    assert config.provider_for_key("xai-abc") == "xai"
    assert config.provider_for_key("sk-abc") == "openai"
    assert config.provider_for_key("mystery") == ""


def test_store_key_writes_env_file_mode_600(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "keyring", None)  # force the file fallback
    where = config.store_key("GROQ_API_KEY", "gsk_secret")
    p = tmp_path / "ai-workspace" / "rooks.env"
    assert where == str(p)
    assert oct(p.stat().st_mode)[-3:] == "600"
    assert "gsk_secret" in p.read_text()
    assert config._load_env_file()["GROQ_API_KEY"] == "gsk_secret"


def test_login_workbench_writes_toml_and_stores_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "keyring", None)
    answers = iter(["a", "http://localhost:3100"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(login, "_ask_secret", lambda prompt: "wb_" + "t" * 43)
    assert login.main() == 0
    out = capsys.readouterr().out
    assert "wb_" + "t" * 43 not in out  # the token is never printed
    toml = (tmp_path / "ai-workspace" / "rooks.toml").read_text()
    assert 'provider = "workbench"' in toml and 'workbench_url = "http://localhost:3100"' in toml
    assert (tmp_path / "ai-workspace" / "rooks.env").read_text().count("wb_tt") == 1


def test_login_pasted_key_detects_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "b")
    monkeypatch.setattr(login, "_ask_secret", lambda prompt: "gsk_live_key")
    assert login.main() == 0
    out = capsys.readouterr().out
    assert "groq" in out and "gsk_live_key" not in out
    assert 'provider = "groq"' in (tmp_path / "ai-workspace" / "rooks.toml").read_text()


def test_doctor_prints_every_check_with_no_config(no_network, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["rooks", "doctor"])
    code = cli.subcommand("doctor", ["-C", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    for line in ("config file:", "provider:", "trusted folder:"):
        assert line in out
    assert "rooks login" in out  # the fix is named


def test_doctor_never_exits_before_the_last_check(no_network, tmp_path, monkeypatch, capsys):
    """A broken provider line must not stop the trusted-folder and MCP lines from printing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")  # resolve() succeeds, the model call will fail (no network)
    code = cli.subcommand("doctor", ["-C", str(tmp_path)])
    out = capsys.readouterr().out
    assert "provider: groq" in out
    assert "model call: FAILED" in out
    assert "trusted folder:" in out  # reached even though the model call failed
    assert code == 1
