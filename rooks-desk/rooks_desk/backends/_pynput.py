"""Mouse and keyboard through pynput (macOS Quartz events, Windows SendInput). Shared by the macOS and Windows backends."""

from __future__ import annotations

import time

from .base import normalise_keys


class PynputInput:
    def __init__(self):
        from pynput import keyboard, mouse
        self.mouse_mod, self.kb_mod = mouse, keyboard
        self.mouse = mouse.Controller()
        self.kb = keyboard.Controller()
        K = keyboard.Key
        self.special = {
            "ctrl": K.ctrl, "alt": K.alt, "shift": K.shift, "cmd": K.cmd, "enter": K.enter, "tab": K.tab, "esc": K.esc,
            "backspace": K.backspace, "delete": K.delete, "up": K.up, "down": K.down, "left": K.left, "right": K.right,
            "home": K.home, "end": K.end, "pageup": K.page_up, "pagedown": K.page_down, "space": K.space,
            "capslock": K.caps_lock,
        }
        for name in ("insert", "print_screen"):
            if hasattr(K, name):
                self.special["insert" if name == "insert" else "printscreen"] = getattr(K, name)
        for i in range(1, 21):
            if hasattr(K, f"f{i}"):
                self.special[f"f{i}"] = getattr(K, f"f{i}")

    def cursor(self):
        x, y = self.mouse.position
        return int(x), int(y)

    def move(self, x, y):
        self.mouse.position = (int(x), int(y))

    def _button(self, name):
        B = self.mouse_mod.Button
        return {"left": B.left, "right": B.right, "middle": B.middle}.get(name, B.left)

    def click(self, x, y, button="left", count=1):
        self.move(x, y)
        time.sleep(0.05)
        self.mouse.click(self._button(button), max(1, int(count)))

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4):
        b = self._button(button)
        self.move(x1, y1)
        time.sleep(0.05)
        self.mouse.press(b)
        steps = max(4, int(duration / 0.02))
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
            time.sleep(duration / steps)
        self.mouse.release(b)

    def scroll(self, x, y, dx=0, dy=-3):
        self.move(x, y)
        self.mouse.scroll(int(dx), int(dy))

    def type_text(self, text, interval=0.0):
        for ch in text:
            if ch == "\n":
                self.kb.press(self.kb_mod.Key.enter)
                self.kb.release(self.kb_mod.Key.enter)
            elif ch == "\r":
                continue
            else:
                self.kb.type(ch)
            time.sleep(interval or 0.02)

    def hotkey(self, keys):
        ks = [self.special.get(k, k) for k in normalise_keys(keys)]
        for k in ks:
            self.kb.press(k)
            time.sleep(0.01)
        for k in reversed(ks):
            self.kb.release(k)
