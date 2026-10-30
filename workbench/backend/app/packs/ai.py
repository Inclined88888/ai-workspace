"""One JSON reply from the user's default model, for packs (patched in tests). Every caller has an offline fallback."""

from __future__ import annotations

from .. import llm


async def model_json(prompt: str, payload: str, channel: str):
    res = await llm.chat(None, [{"role": "system", "content": prompt}, {"role": "user", "content": payload}],
                         trace={"channel": f"packs:{channel}"})
    return llm.parse_json(res["message"].get("content") or "")


async def try_json(prompt: str, payload: str, channel: str):
    """The model's JSON, or None when no model is set up or the reply is not JSON (callers then use their own rules)."""
    try:
        return await model_json(prompt, payload, channel)
    except Exception:  # noqa: BLE001 - no model, quota, bad JSON: the pack still works without one
        return None
