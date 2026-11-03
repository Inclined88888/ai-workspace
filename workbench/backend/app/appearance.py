"""The chosen look (theme, typeface, text size, motion), shared by every surface.

The web and desktop app keep the live copy in the browser (workbench/frontend/src/appearance.tsx) and save it here
whenever it changes (PUT /api/appearance). From here it reaches:
  * the phone: GET /api/mobile/appearance with its device token. The phone follows it when set to "Same as computer";
  * Rooks Code and Rooks Browser: in the desktop app (or when AI_WORKSPACE_APPEARANCE_FILE is set) it is also written to
    ~/.ai-workspace/appearance.json, which both read for their terminal colours.
Only known values are kept, so nothing a page sends can become anything but a theme name and a few numbers.
"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import config, db
from .mobile import require_device
from .security import require_user

THEMES = ("sunshine", "paper", "cherry", "neon", "night", "contrast", "auto", "linen", "gallery")
DARK = ("night", "neon")
FONTS = ("editorial", "modern", "classic", "system")
MOTION = ("full", "subtle", "off")
ACCENTS = ("theme", "sun", "cherry", "ink", "cobalt", "forest", "ochre")
DEFAULT = {"theme": "sunshine", "resolved": "sunshine", "dark": False, "font": "modern", "size": 14, "motion": "full", "accent": "theme"}

owner = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
device = APIRouter(prefix="/api/mobile")


class AppearanceIn(BaseModel):
    theme: str = "sunshine"
    resolved: str | None = None
    dark: bool | None = None
    font: str = "modern"
    size: int = 14
    motion: str = "full"
    accent: str = "theme"


def clean(data: dict) -> dict:
    theme = data.get("theme") if data.get("theme") in THEMES else DEFAULT["theme"]
    resolved = data.get("resolved") if data.get("resolved") in THEMES and data.get("resolved") != "auto" else None
    if resolved is None:
        resolved = "sunshine" if theme == "auto" else theme
    if theme != "auto":
        resolved = theme
    try:
        size = min(18, max(12, int(data.get("size", 14))))
    except (TypeError, ValueError):
        size = 14
    return {
        "theme": theme,
        "resolved": resolved,
        "dark": resolved in DARK,
        "font": data.get("font") if data.get("font") in FONTS else DEFAULT["font"],
        "size": size,
        "motion": data.get("motion") if data.get("motion") in MOTION else DEFAULT["motion"],
        "accent": data.get("accent") if data.get("accent") in ACCENTS else DEFAULT["accent"],
    }


def current() -> dict:
    raw = db.get_setting("appearance")
    if not raw:
        return dict(DEFAULT)
    try:
        return clean(json.loads(raw))
    except (ValueError, TypeError):
        return dict(DEFAULT)


def shared_file() -> Path | None:
    """Where Rooks Code and Rooks Browser look. Only written by the desktop app (it runs as the user)."""
    explicit = os.getenv("AI_WORKSPACE_APPEARANCE_FILE")
    if explicit:
        return Path(explicit)
    if config.DESKTOP:
        return Path.home() / ".ai-workspace" / "appearance.json"
    return None


def save(data: dict) -> dict:
    a = clean(data)
    db.set_setting("appearance", json.dumps(a))
    path = shared_file()
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(a, indent=2))
            tmp.replace(path)
        except OSError:
            pass  # best effort: the terminal tools fall back to their own default
    return a


@owner.get("/appearance")
def get_appearance():
    return current()


@owner.put("/appearance")
def put_appearance(data: AppearanceIn):
    return save(data.model_dump())


@device.get("/appearance")
def phone_appearance(_: dict = Depends(require_device)):
    return current()
