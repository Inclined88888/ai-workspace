"""A fake model that acts by element name, so tests read like the task instead of like element numbers."""

import re


def find(prompt: str, role: str, name: str) -> int:
    for m in re.finditer(r'^\*?\s*\[(\d+)\] (\w+) "([^"]*)"', prompt, re.M):
        if m.group(2) == role and name.lower() in m.group(3).lower():
            return int(m.group(1))
    raise AssertionError(f"no {role} named {name!r} in the prompt:\n{prompt[:3000]}")


def click(role, name, thought=""):
    return lambda p: {"thought": thought or f"click {name}", "action": "click", "args": {"id": find(p, role, name)}}


def type_(role, name, text, submit=False):
    return lambda p: {"thought": f"type into {name}", "action": "type", "args": {"id": find(p, role, name), "text": text, "submit": submit}}


def act(action, **args):
    return {"thought": action, "action": action, "args": args}
