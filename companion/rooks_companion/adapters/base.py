"""The contract every desktop backend implements. Platforms are separate adapters; nothing here pretends an operation
works everywhere. An element is a dict: {id, role, label, value, secure, bbox: [x, y, w, h], path} where path lets the
adapter find the same element again (an accessibility index chain, or a native id)."""

from __future__ import annotations


class NotSupported(Exception):
    pass


class Adapter:
    name = "base"
    os = "unknown"

    def permissions(self) -> dict:
        """{accessibility: bool|None, screen_recording: bool|None, notes: str}. None means not applicable or unknown."""
        raise NotSupported

    def windows(self) -> list[dict]:
        """[{app, title, focused, bbox}] for normal windows."""
        raise NotSupported

    def active_app(self) -> str:
        raise NotSupported

    def elements(self, app: str = "") -> list[dict]:
        """Elements of the focused window (or app's front window), from accessibility, without ids (the engine adds them)."""
        raise NotSupported

    def screenshot(self) -> bytes | None:
        """PNG bytes of the screen, or None when screen capture is not permitted."""
        return None

    def open_app(self, name: str) -> None:
        raise NotSupported

    def focus(self, app: str) -> None:
        raise NotSupported

    def press(self, el: dict) -> None:
        """Activate an element, preferring its native action over a click at its position."""
        x, y, w, h = el["bbox"]
        self.click_at(x + w // 2, y + h // 2)

    def click_at(self, x: int, y: int) -> None:
        raise NotSupported

    def type_text(self, text: str) -> None:
        raise NotSupported

    def key(self, combo: str) -> None:
        raise NotSupported

    def scroll(self, direction: str, amount: int) -> None:
        raise NotSupported
