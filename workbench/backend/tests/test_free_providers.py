import pytest


@pytest.fixture
def llm(client):
    from app import llm
    return llm


def _models_url(base_url: str) -> str:
    from app.adapters import ChatCompletions

    return ChatCompletions().models_url({"base_url": base_url})


def test_opencode_models_url_and_filter(llm):
    zen = llm.PRESETS["opencode-zen"]["base_url"]
    go = llm.PRESETS["opencode-go"]["base_url"]
    assert _models_url(zen) == "https://opencode.ai/inference/v1/models"
    assert _models_url(go) == "https://opencode.ai/inference/go/v1/models"
    # free models only run inside the OpenCode app; Claude, GPT, Grok, Gemini use other endpoints
    for m in ["big-pickle", "glm-5.3-flash-free", "claude-haiku-5-5", "gpt-6", "grok-4.7", "gemini-4-argon", "test"]:
        assert not llm.keep_model(zen, m), m
    for m in ["glm-5.3", "kimi-k3", "deepseek-v4-pro", "qwen3.8-max", "minimax-m3"]:
        assert llm.keep_model(zen, m), m
    # Zen serves these Qwen models on Anthropic Messages; Go serves every Qwen model and MiniMax M3 there
    for m in ["qwen3.8-flash", "qwen3.6-plus", "qwen3.5-plus"]:
        assert not llm.keep_model(zen, m), m
    for m in ["qwen3.8-max", "minimax-m3", "muse-spark-1.3-contributor", "iceman-contributor-free"]:
        assert not llm.keep_model(go, m), m
    for m in ["glm-5.3", "minimax-m3.1-flash", "mimo-v2.6-pro"]:
        assert llm.keep_model(go, m), m


def test_gemini_filter_keeps_chat_models_only(llm):
    g = llm.PRESETS["gemini"]["base_url"]
    assert _models_url(g) == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    assert llm.keep_model(g, "gemini-3.8-flash")
    assert llm.keep_model(g, "gemma-4-27b-it")
    for m in ["gemini-3.8-flash-tts", "gemini-embedding-2", "gemini-3.8-live", "imagen-4", "veo-3"]:
        assert not llm.keep_model(g, m), m


def test_free_flag(llm):
    def conn(auth_kind, protocol, base_url):
        return {"name": "x", "auth_kind": auth_kind, "protocol": protocol, "base_url": base_url}

    assert llm.is_free(conn("local_ollama", "ollama_native", "http://x"), "qwen3:4b")
    assert llm.is_free(conn("api_key", "chat_completions", "https://openrouter.ai/api/v1"), "qwen/qwen3-coder:free")
    assert not llm.is_free(conn("api_key", "chat_completions", "https://openrouter.ai/api/v1"), "anthropic/claude-sonnet-5.5")
    assert not llm.is_free(conn("api_key", "responses", "https://api.openai.com/v1"), "gpt-6:free")
