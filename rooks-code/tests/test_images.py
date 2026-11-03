"""Images in prompts: @shot.png and /image send the picture, OpenAI and Anthropic shapes, sessions stay small."""

import base64
import json

import pytest

from conftest import rooks
from fakes import FakeModel, anthropic_reply, openai_reply
from rooks_code import images, providers

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_mentions_become_image_parts_only_for_real_images(tmp_path):
    (tmp_path / "shot.png").write_bytes(PNG)
    (tmp_path / "fake.png").write_text("not an image")
    (tmp_path / "my shot.png").write_bytes(PNG)
    assert images.content("look at @fake.png and @missing.png", tmp_path) == "look at @fake.png and @missing.png"
    assert images.content("mail me at a@shot.png", tmp_path) == "mail me at a@shot.png"  # not a mention
    c = images.content("what is wrong in @shot.png?", tmp_path)
    assert c[0] == {"type": "text", "text": "what is wrong in @shot.png?"}
    assert c[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert len(images.content(r"see @my\ shot.png", tmp_path)) == 2
    assert images.text_of(c) == "what is wrong in @shot.png?\n[image]"


def test_image_command(tmp_path):
    (tmp_path / "a b.png").write_bytes(PNG)
    (tmp_path / "x.txt").write_text("hi")
    prompt, err = images.image_command('"a b.png" why red?', tmp_path)
    assert not err and prompt.endswith(" why red?") and len(images.content(prompt, tmp_path)) == 2
    prompt, err = images.image_command("a b.png", tmp_path)
    assert not err and prompt.endswith("What does this image show?")
    assert images.image_command("x.txt", tmp_path)[1].startswith("x.txt is not")
    assert images.image_command("nope.png", tmp_path)[1] == "no such file: nope.png"
    assert images.image_command("", tmp_path)[1].startswith("usage")


def test_anthropic_gets_image_blocks():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]}]
    out = providers.Anthropic.convert(msgs)
    assert out == [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]}]


@pytest.mark.parametrize("kind", ["openai-compatible", "anthropic"])
def test_cli_sends_the_picture_and_saves_a_note(project, kind, monkeypatch):
    (project / "shot.png").write_bytes(PNG)
    fake = FakeModel([openai_reply("A red square.") if kind != "anthropic" else anthropic_reply("A red square.")])
    try:
        if kind == "anthropic":
            monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
            r = rooks(project, "-P", "anthropic", "--base-url", fake.url, "-m", "claude-x", "what is in @shot.png")
        else:
            r = rooks(project, "-P", "openai-compatible", "--base-url", fake.url, "-m", "fake-model", "what is in @shot.png")
    finally:
        fake.close()
    assert r.returncode == 0, r.stderr
    body = fake.requests[0]["body"]
    user = [m for m in body["messages"] if m["role"] == "user"][-1]
    parts = user["content"]
    if kind == "anthropic":
        assert any(p["type"] == "image" and p["source"]["media_type"] == "image/png" for p in parts)
    else:
        assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,") for p in parts)
    saved = next((project / ".rooks" / "runs").glob("*.messages.json"))
    data = json.loads(saved.read_text())
    assert "base64" not in saved.read_text()
    assert data["messages"][0]["content"] == "what is in @shot.png\n[image]"
