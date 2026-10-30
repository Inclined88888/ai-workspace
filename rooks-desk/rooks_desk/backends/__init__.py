"""Platform backends: one class per OS with the same methods (see base.Backend).

    get_backend()            the backend for this computer (macOS, Windows, Linux/X11)
    get_backend("fake")      an in-memory desktop for tests and dry runs
"""

from __future__ import annotations

import os
import sys

from .base import Backend, BackendUnavailable, Element, Monitor, Window  # noqa: F401


def get_backend(name: str | None = None) -> Backend:
    name = (name or os.getenv("ROOKS_DESK_BACKEND") or "auto").lower()
    if name == "fake":
        from .fake import FakeBackend
        return FakeBackend()
    if name in ("auto", "macos", "darwin") and sys.platform == "darwin":
        from .macos import MacBackend
        return MacBackend()
    if name in ("auto", "windows", "win32") and sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if name in ("auto", "linux", "x11") and sys.platform.startswith("linux"):
        from .linux import LinuxBackend
        return LinuxBackend()
    raise BackendUnavailable(f"no desktop backend {name!r} for {sys.platform}")
