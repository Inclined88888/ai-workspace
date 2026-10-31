"""Ask from anywhere (feat/v02-anywhere-voice): copy the selection, keep the user's clipboard, paste back."""

from rooks_desk import grab
from rooks_desk.backends.fake import FakeBackend


class Clip:
    def __init__(self, value="what I copied earlier"):
        self.value = value
        self.history = []

    def get(self):
        return self.value

    def put(self, v):
        self.history.append(v)
        self.value = v
        return True


class Selecting(FakeBackend):
    """A fake desktop where the front app has some text selected: the copy shortcut puts it on the clipboard."""

    def __init__(self, clip, selected="Quote #88: $4,200 incl. GST"):
        super().__init__()
        self.clip, self.selected = clip, selected

    def hotkey(self, keys):
        super().hotkey(keys)
        if keys[-1] == "c" and self.selected:
            self.clip.value = self.selected


def test_grab_copies_the_selection_and_restores_the_clipboard():
    clip = Clip()
    b = Selecting(clip)
    b.open_app("Text Editor")
    out = grab.grab(b, copy=True, screenshot=True, get=clip.get, put=clip.put)
    assert out["text"] == "Quote #88: $4,200 incl. GST" and out["selected"] is True
    assert clip.value == "what I copied earlier"  # put back exactly
    assert out["window"]["app"] == "Text Editor"
    assert out["image"].startswith("data:image/png;base64,")
    assert ("hotkey", "+".join(grab.COPY_KEYS)) in b.events


def test_nothing_selected_falls_back_to_the_clipboard():
    clip = Clip()
    b = Selecting(clip, selected="")
    out = grab.grab(b, copy=True, wait=0.2, get=clip.get, put=clip.put)
    assert out["text"] == "" and out["selected"] is False and out["clipboard"] == "what I copied earlier"
    assert clip.value == "what I copied earlier" and out["image"] == ""


def test_paste_back_focuses_the_window_and_presses_paste():
    clip = Clip()
    b = Selecting(clip)
    b.open_app("Text Editor")
    b.open_app("Mail")
    win = next(w for w in b.windows() if w.app == "Text Editor")
    r = grab.paste(b, "Thanks, Tuesday works.", win.to_dict(), put=clip.put)
    assert r == {"copied": True, "pasted": True}
    assert clip.value == "Thanks, Tuesday works."
    assert ("focus", "Text Editor") in b.events and b.events[-1] == ("hotkey", "+".join(grab.PASTE_KEYS))
