"""Change sets: every file the agent writes during one prompt is snapshotted first, so `/undo` (or `rooks undo`) puts the
files back exactly as they were. Shell commands cannot be undone; the receipt says which commands ran."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path


class ChangeSets:
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / ".rooks" / "changesets"
        self.current: dict[str, str | None] | None = None
        self.label = ""

    def begin(self, label: str) -> None:
        self.current, self.label = {}, label[:200]

    def snapshot(self, path: Path) -> None:
        """Record a file's content before its first change in this change set (None = it did not exist)."""
        if self.current is None:
            self.begin("manual")
        rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        if rel not in self.current:  # type: ignore[operator]
            self.current[rel] = base64.b64encode(path.read_bytes()).decode() if path.exists() else None  # type: ignore[index]

    def commit(self) -> Path | None:
        if not self.current:
            self.current = None
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        gitignore = self.root / ".rooks" / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")
        f = self.dir / f"{time.time_ns()}.json"
        f.write_text(json.dumps({"label": self.label, "files": self.current}))
        self.current = None
        return f

    def stack(self) -> list[Path]:
        return sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []

    def undo(self) -> tuple[str, list[str]] | None:
        """Restore the newest change set and remove it. Returns (label, files restored), or None if there is none."""
        sets = self.stack()
        if not sets:
            return None
        data = json.loads(sets[-1].read_text())
        restored = []
        for rel, content in data["files"].items():
            p = self.root / rel
            if content is None:
                if p.exists():
                    p.unlink()
                restored.append(f"{rel} (removed: it was new)")
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(base64.b64decode(content))
                restored.append(rel)
        sets[-1].unlink()
        return data["label"], restored
