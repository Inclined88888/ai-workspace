"""Builds the bundled model catalog snapshot (app/data/models_catalog.json) from models.dev's api.json.

    python -m app.catalog_build path/to/api.json     # or no argument to download it

The runtime keeps the same trimmed shape when it refreshes (catalog.refresh), so the two never drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import catalog

if __name__ == "__main__":
    raw = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else catalog.download()
    out = catalog.trim(raw)
    catalog.SNAPSHOT.write_text(json.dumps(out, separators=(",", ":"), sort_keys=True))
    print(f"{sum(len(v) for v in out['providers'].values())} models from {len(out['providers'])} providers -> {catalog.SNAPSHOT}")
