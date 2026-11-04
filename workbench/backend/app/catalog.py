"""Model facts from a catalog, not guesses from names: context window, price, whether it takes images, release date.

Source: models.dev (github.com/sst/models.dev, MIT), the open catalog OpenCode and others use. A trimmed snapshot ships
in app/data/models_catalog.json so a first start offline still knows the common models; refresh() downloads a newer
copy at most once a day into DATA_DIR. Anything the catalog does not know falls back to the old behaviour (name rules,
a 300,000 character window, no price), so a missing entry never breaks a call.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

SOURCE = "https://models.dev/api.json"
SNAPSHOT = Path(__file__).parent / "data" / "models_catalog.json"
MAX_AGE_S = 24 * 3600

# Which catalog provider answers for a base URL. Order matters: the first host found in the URL wins.
HOSTS: list[tuple[str, str]] = [
    ("generativelanguage.googleapis.com", "google"),
    ("api.groq.com", "groq"),
    ("openrouter.ai", "openrouter"),
    ("api.openai.com", "openai"),
    ("chatgpt.com", "openai"),
    ("api.anthropic.com", "anthropic"),
    ("api.x.ai", "xai"),
    ("api.mistral.ai", "mistral"),
    ("api.deepseek.com", "deepseek"),
    ("integrate.api.nvidia.com", "nvidia"),
    ("api.cerebras.ai", "cerebras"),
    ("api.together.xyz", "togetherai"),
    ("api.together.ai", "togetherai"),
    ("api.fireworks.ai", "fireworks-ai"),
    ("opencode.ai", "opencode"),
    ("api.moonshot.ai", "moonshotai"),
    ("api.z.ai", "zai"),
]
KEEP = sorted({p for _, p in HOSTS})

_lock = threading.Lock()
_data: dict | None = None
_by_id: dict[str, dict] = {}


def _cache_path() -> Path:
    return Path(os.getenv("DATA_DIR", "/data")) / "models_catalog.json"  # config.DATA_DIR, without its secret checks


def trim(raw: dict) -> dict:
    """models.dev api.json reduced to what the router and cost accounting read, for the providers Workbench speaks."""
    providers: dict[str, dict] = {}
    for pid in KEEP:
        models = ((raw.get(pid) or {}).get("models")) or {}
        out = {}
        for mid, m in models.items():
            lim, cost = m.get("limit") or {}, m.get("cost") or {}
            entry = {
                "ctx": int(lim.get("context") or 0),
                "out": int(lim.get("output") or 0),
                "img": "image" in ((m.get("modalities") or {}).get("input") or []),
                "tools": bool(m.get("tool_call")),
                "reasoning": bool(m.get("reasoning")),
                "released": str(m.get("release_date") or ""),
            }
            if isinstance(cost.get("input"), (int, float)) and isinstance(cost.get("output"), (int, float)):
                entry["in_usd"], entry["out_usd"] = float(cost["input"]), float(cost["output"])
                for src, dst in (("cache_read", "cache_read_usd"), ("cache_write", "cache_write_usd")):
                    if isinstance(cost.get(src), (int, float)):
                        entry[dst] = float(cost[src])
            out[mid] = entry
        if out:
            providers[pid] = out
    return {"source": SOURCE, "fetched": int(time.time()), "providers": providers}


def download() -> dict:
    r = httpx.get(SOURCE, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def _index(data: dict) -> None:
    """Bare model ids across providers, for servers the catalog does not list (LiteLLM, vLLM, LM Studio)."""
    global _by_id
    idx: dict[str, dict] = {}
    for pid in ("openai", "anthropic", "google", "xai", "mistral", "deepseek", "moonshotai", "zai", "groq", "togetherai",
                "nvidia", "fireworks-ai", "cerebras", "openrouter", "opencode"):
        for mid, m in (data["providers"].get(pid) or {}).items():
            for key in {mid.lower(), mid.lower().rsplit("/", 1)[-1]}:
                idx.setdefault(key, m)
    _by_id = idx


def load() -> dict:
    """The newest catalog on disk: the daily download when present, else the bundled snapshot."""
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is None:
            data = None
            for path in (_cache_path(), SNAPSHOT):
                try:
                    data = json.loads(path.read_text())
                    if data.get("providers"):
                        break
                except (OSError, ValueError):
                    data = None
            loaded = data or {"providers": {}, "fetched": 0}
            _index(loaded)
            _data = loaded
            return loaded
    return _data


def reset() -> None:
    """Forget the loaded catalog (tests, and after a refresh)."""
    global _data, _by_id
    with _lock:
        _data, _by_id = None, {}


def auto_refresh() -> bool:
    """Off unless the user turns it on in Settings: nothing leaves this machine unasked. The bundled snapshot is used
    meanwhile. MODEL_CATALOG_REFRESH=1 or 0 overrides the setting (servers, tests)."""
    env = os.getenv("MODEL_CATALOG_REFRESH", "")
    if env in ("0", "1"):
        return env == "1"
    from . import db

    try:
        return db.get_setting("model_catalog_refresh", "0") == "1"
    except Exception:  # noqa: BLE001 - no database yet: stay off
        return False


def refresh(force: bool = False) -> bool:
    """Download a newer catalog when the one in use is over a day old. Never raises: offline keeps the old one."""
    if not force and not auto_refresh():
        return False
    if not force and time.time() - (load().get("fetched") or 0) < MAX_AGE_S:
        return False
    try:
        trimmed = trim(download())
    except (httpx.HTTPError, ValueError):
        return False
    if not trimmed["providers"]:
        return False
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(trimmed, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass
    reset()
    return True


def refresh_in_background() -> None:
    threading.Thread(target=refresh, name="model-catalog-refresh", daemon=True).start()


def provider_for(base_url: str) -> str | None:
    return next((p for host, p in HOSTS if host in (base_url or "")), None)


_DATED = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2}|latest|preview(-\d{2}-\d{2})?|\d{4})$")


def _provider_entry(base_url: str, model: str) -> dict | None:
    """The entry the connection's own provider lists, the only one whose price applies to this connection."""
    table = load()["providers"].get(provider_for(base_url) or "", {})
    name = model.removeprefix("models/")
    for key in (name, name.removesuffix(":free"), _DATED.sub("", name)):
        if key in table:
            return table[key]
    return None


def lookup(base_url: str, model: str) -> dict | None:
    """Catalog facts for a model as a provider lists it, or None when the catalog does not know it. Servers the catalog
    does not cover (LiteLLM, vLLM, LM Studio) are matched by the bare model id across providers."""
    if not model:
        return None
    hit = _provider_entry(base_url, model)
    if hit:
        return hit
    low = model.removeprefix("models/").lower().removesuffix(":free")
    for key in (low, low.rsplit("/", 1)[-1], _DATED.sub("", low.rsplit("/", 1)[-1])):
        if key in _by_id:
            return _by_id[key]
    return None


def context_tokens(base_url: str, model: str) -> int | None:
    m = lookup(base_url, model)
    return m["ctx"] if m and m.get("ctx") else None


def sees(base_url: str, model: str) -> bool | None:
    """True or False when the catalog knows the model's inputs; None when it does not."""
    m = lookup(base_url, model)
    return None if m is None else bool(m.get("img"))


def price(base_url: str, model: str) -> tuple[float, float] | None:
    """(USD per million input tokens, per million output tokens), or None when unknown."""
    if model.endswith(":free") and provider_for(base_url) == "openrouter":
        return 0.0, 0.0
    m = _provider_entry(base_url, model)  # another provider's price for the same weights says nothing about this one
    if not m or "in_usd" not in m:
        return None
    return m["in_usd"], m["out_usd"]


def cache_prices(base_url: str, model: str) -> tuple[float | None, float | None]:
    """(USD per million cached tokens read, per million written), each None when the catalog has no price."""
    m = _provider_entry(base_url, model) or {}
    return m.get("cache_read_usd"), m.get("cache_write_usd")


def released(base_url: str, model: str) -> str:
    m = lookup(base_url, model)
    return (m or {}).get("released", "")


def status() -> dict:
    data = load()
    return {"source": data.get("source", SOURCE), "fetched": data.get("fetched", 0),
            "models": sum(len(v) for v in data["providers"].values()), "providers": sorted(data["providers"])}
