"""Reports Files and Sites work to the learn-and-adapt module when it is installed (PR #29); a no-op otherwise."""

import importlib
import logging

log = logging.getLogger("learn_hook")


def observe(source: str, summary: str, ref: str = "") -> None:
    """source: "file" | "site" | "task". Never raises and never blocks the feature that called it."""
    try:
        hooks = importlib.import_module("app.adapt.hooks")
    except ImportError:
        return
    try:
        hooks.observe(source, (summary or "")[:300], None, ref)
    except Exception as e:  # noqa: BLE001
        log.debug("learn observe skipped: %s", e)
