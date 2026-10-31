"""Compression summaries really come from the configured model (not the silent truncation fallback)."""

from conftest import CALLS, SCRIPT


def test_summary_comes_from_the_model(client):
    from app import compression

    SCRIPT[:] = [{"role": "assistant", "content": "User is planning a Lisbon trip in May."}]
    CALLS.clear()
    msgs = [{"role": "user", "content": "I want to go to Lisbon in May"}, {"role": "assistant", "content": "Great choice"}]
    assert compression._default_summarize(msgs) == "User is planning a Lisbon trip in May."
    assert CALLS and "Lisbon" in CALLS[-1]["messages"][-1]["content"]


def test_multimodal_content_is_summarised_as_text():
    from app import compression

    parts = [{"type": "text", "text": "look at this"}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    assert compression._text(parts) == "look at this"
    assert compression._text(None) == ""
