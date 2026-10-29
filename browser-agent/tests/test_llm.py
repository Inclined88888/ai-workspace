import pytest

from browser_agent import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "WORKBENCH_URL", "BROWSER_REQUIRE_WORKBENCH",
              "BROWSER_LLM_PROVIDER", "BROWSER_LLM_MODEL", "OPENAI_COMPATIBLE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_auto_falls_back_to_ollama():
    assert llm.resolve_provider(None) == "ollama"


def test_auto_prefers_anthropic_then_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert llm.resolve_provider("auto") == "gemini"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm.resolve_provider("auto") == "anthropic"


def test_auto_skips_providers_without_default_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert llm.resolve_provider("auto") == "ollama"


def test_unknown_provider():
    with pytest.raises(ValueError):
        llm.resolve_provider("nope")


def test_openai_requires_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    with pytest.raises(ValueError, match="--model"):
        llm.make_llm("openai")


def test_env_model_only_applies_to_its_provider(monkeypatch):
    monkeypatch.setenv("BROWSER_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("BROWSER_LLM_MODEL", "gemini-custom")
    _, name, model = llm.make_llm("ollama")
    assert (name, model) == ("ollama", "qwen3:8b")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    _, name, model = llm.make_llm(None)
    assert (name, model) == ("gemini", "gemini-custom")


@pytest.mark.parametrize("provider,model", [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-flash-latest"),
                                            ("ollama", "qwen3:8b")])
def test_factories_build(monkeypatch, provider, model):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    chat, name, used = llm.make_llm(provider)
    assert name == provider and used == model
    assert getattr(chat, "model", model) == model


def test_openai_compatible_needs_base_url():
    with pytest.raises(ValueError, match="base-url"):
        llm.make_llm("openai-compatible", "m")
    chat, _, _ = llm.make_llm("openai-compatible", "m", base_url="http://localhost:1234/v1")
    assert chat.model == "m"


def test_workbench_provider_points_browser_use_at_the_bridge(monkeypatch):
    monkeypatch.setenv("WORKBENCH_URL", "http://workbench:8000/")
    monkeypatch.setenv("BROWSER_API_KEY", "k" * 30)
    assert llm.resolve_provider(None) == "workbench"
    chat, name, model = llm.make_llm()
    assert (name, model) == ("workbench", "default")
    assert str(chat.base_url).rstrip("/") == "http://workbench:8000/v1" and chat.api_key == "k" * 30


def test_in_the_stack_only_workbench_is_allowed(monkeypatch):
    """R1: with BROWSER_REQUIRE_WORKBENCH the service cannot call a provider directly, even with a key in its env."""
    monkeypatch.setenv("BROWSER_REQUIRE_WORKBENCH", "true")
    monkeypatch.setenv("WORKBENCH_URL", "http://workbench:8000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm.available() == ["workbench"]
    assert llm.resolve_provider("auto") == "workbench"
    for name in ("anthropic", "ollama", "openai-compatible"):
        with pytest.raises(ValueError, match="R1"):
            llm.resolve_provider(name)
    monkeypatch.delenv("WORKBENCH_URL")
    with pytest.raises(ValueError, match="WORKBENCH_URL"):
        llm.resolve_provider(None)
