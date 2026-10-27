"""Pick the desktop adapter for this computer. Each platform is its own adapter; unsupported ones say so."""

from __future__ import annotations

import os
import sys

from .base import Adapter, NotSupported


def detect() -> Adapter | None:
    if sys.platform == "darwin":
        from .macos import MacOSAdapter

        return MacOSAdapter()
    if sys.platform.startswith("win"):
        from .windows import WindowsAdapter

        return WindowsAdapter()
    if os.environ.get("DISPLAY"):
        try:
            from .x11 import X11Adapter

            return X11Adapter()
        except (ImportError, NotSupported):
            return None
    return None


__all__ = ["Adapter", "NotSupported", "detect"]
