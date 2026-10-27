import pytest


@pytest.fixture
def llm(client):
    from app import llm
    return llm


def test_opencode_models_url_and_filter(llm):
    zen = llm.PRESETS["opencode-zen"]["base_url"]
    go = llm.PRESETS["opencode-go"]["base_url"]
    assert llm._models_url(zen) == "https://opencode.ai/inference/v1/models"
    assert llm._models_url(go) == "https://opencode.ai/inference/go/v1/models"
    # free models only run inside the OpenCode app; Claude, GPT, Grok, Gemini use other endpoints
    for m in ["big-pickle", "glm-5.3-flash-free", "claude-haiku-5-5", "gpt-6", "grok-4.7", "gemini-4-argon", "test"]:
        assert not llm._keep_model(zen, m), m
    for m in ["glm-5.3", "kimi-k3", "deepseek-v4-pro", "qwen3.8-max", "minimax-m3"]:
        assert llm._keep_model(zen, m), m
    # Zen serves these Qwen models on Anthropic Messages; Go serves every Qwen model and MiniMax M3 there
    for m in ["qwen3.8-flash", "qwen3.6-plus", "qwen3.5-plus"]:
        assert not llm._keep_model(zen, m), m
    for m in ["qwen3.8-max", "minimax-m3", "muse-spark-1.3-contributor", "iceman-contributor-free"]:
        assert not llm._keep_model(go, m), m
    for m in ["glm-5.3", "minimax-m3.1-flash", "mimo-v2.6-pro"]:
        assert llm._keep_model(go, m), m


def test_gemini_filter_keeps_chat_models_only(llm):
    g = llm.PRESETS["gemini"]["base_url"]
    assert llm._models_url(g) == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    assert llm._keep_model(g, "gemini-3.8-flash")
    assert llm._keep_model(g, "gemma-4-27b-it")
    for m in ["gemini-3.8-flash-tts", "gemini-embedding-2", "gemini-3.8-live", "imagen-4", "veo-3"]:
        assert not llm._keep_model(g, m), m


def test_free_flag(llm):
    from app import db

    assert llm.is_free({"id": 0, "kind": "ollama", "base_url": "http://localhost:11434"}, "qwen3:4b")
    assert not llm.is_free({"id": 0, "kind": "ollama", "base_url": "https://api.example.com"}, "qwen3:4b")
    # Ollama's cloud models run on ollama.com even through a local Ollama: neither free nor local
    assert not llm.is_free({"id": 0, "kind": "ollama", "base_url": "http://localhost:11434"}, "gpt-oss:120b-cloud")
    orr = {"id": 9001, "kind": "openai", "base_url": "https://openrouter.ai/api/v1"}
    # A :free name is not enough: the price from OpenRouter's own model list must be zero
    assert not llm.is_free(orr, "qwen/qwen3-coder:free")
    db.set_setting("provider_prices:9001", '{"zero": ["qwen/qwen3-coder:free"], "at": 0}')
    assert llm.is_free(orr, "qwen/qwen3-coder:free")
    assert not llm.is_free(orr, "anthropic/claude-sonnet-5.5")
    # OpenRouter reports its prices, so a confirmation can never make one of its paid models free
    db.set_setting("provider_free_confirmed:9001", "1")
    assert not llm.can_confirm_free(orr) and not llm.is_free(orr, "anthropic/claude-sonnet-5.5")
    assert not llm.is_free({"id": 9002, "kind": "openai", "base_url": "https://api.openai.com/v1"}, "gpt-6:free")
    # Gemini has a free tier, but a key from a project with billing is charged: free only once you confirm it
    gem = {"id": 9003, "kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"}
    assert not llm.is_free(gem, "gemini-3.8-flash")
    db.set_setting("provider_free_confirmed:9003", "1")
    assert llm.is_free(gem, "gemini-3.8-flash")
    # A paid provider cannot be confirmed as free
    db.set_setting("provider_free_confirmed:9002", "1")
    assert not llm.is_free({"id": 9002, "kind": "openai", "base_url": "https://api.openai.com/v1"}, "gpt-6")


def test_prices_from_listing(llm):
    assert llm._prices({"prompt": "0", "completion": "0"})["zero_price"] is True
    assert llm._prices({"prompt": "0", "completion": "0", "request": "0.001"})["zero_price"] is False
    assert llm._prices({"prompt": "0.000003", "completion": "0.000015"}) == {"price_in": 3.0, "price_out": 15.0, "zero_price": False}
    for bad in (None, {}, {"prompt": "0"}, {"prompt": "free", "completion": "0"}, "0"):
        assert llm._prices(bad) == {}, bad