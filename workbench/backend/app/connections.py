"""One directory of every way ROOKS can reach a model, grouped by what each really is. They are not interchangeable:

  A  API inference providers: a key, billed per use or free under a provider's free tier (OpenAI, Anthropic,
     OpenRouter, Gemini, OpenCode). These power ROOKS chat.
  B  Coding agents with their own sign-in (OpenAI Codex CLI, Claude Code), on your paired computer. They run coding
     tasks in a folder; they do not power ROOKS chat, and ROOKS never uses their sign-in for anything else.
  C  Local runtimes on this computer or your network (Ollama, llama.cpp, LM Studio, the experimental AirLLM bridge).
     ROOKS never installs one, downloads a model or starts an inference process by itself.
  D  Provider libraries inside ROOKS (any-llm): not a connection, an engine some connections can use.
  E  Other servers that speak the OpenAI API (a gateway, vLLM or a hosted endpoint): billing is whatever that server
     does, so ROOKS treats it as possibly paid unless it is on your network.

Every status shown comes from a recorded check (provider_check, the capability tests in verify.py, the companion's
report); nothing is assumed from a key being accepted.
"""

import json
from urllib.parse import urlparse

from . import anyllm_engine, db, llm, verify

KNOWN_API = {"api.openai.com": "OpenAI", "api.anthropic.com": "Anthropic", "openrouter.ai": "OpenRouter",
             "generativelanguage.googleapis.com": "Google Gemini", "opencode.ai": "OpenCode"}
LOCAL_SETUP = [
    {"id": "ollama", "name": "Ollama", "setup": "Install Ollama from ollama.com yourself, download a model with `ollama pull`, then "
     "connect it in Settings, AI models. ROOKS can download a model for you only after you agree to its size and licence."},
    {"id": "llamacpp", "name": "llama.cpp", "setup": "Start `llama-server --jinja -m <model.gguf>` yourself (port 8080), then connect "
     "it in Settings, AI models. --jinja is needed for tool calls."},
    {"id": "airllm", "name": "AirLLM bridge (experimental)", "setup": "Off by default. Runs very large models on a small GPU by loading "
     "one layer at a time, so it is slow (minutes per answer) and has no tool calling or streaming. See addons/airllm/README.md."},
]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _category(p: dict) -> str:
    if llm._is_openrouter(p):
        return "A"
    if llm._local_endpoint(p["base_url"]) or p["kind"] == "ollama":
        return "C"
    host = _host(p["base_url"])
    return "A" if any(host == h or host.endswith("." + h) for h in KNOWN_API) else "E"


def _check(pid: int) -> dict:
    try:
        return json.loads(db.get_setting(f"provider_check:{pid}", "") or "{}")
    except ValueError:
        return {}


def _billing(p: dict, cat: str) -> tuple[str, str]:
    """(class, words): class is free, paid, local or unknown."""
    host = _host(p["base_url"])
    if cat == "C":
        if p["kind"] == "ollama":
            return "local", "Runs on your hardware; nothing is billed (Ollama's -cloud models are the exception: ollama.com runs them)."
        if llm.confirmed_free(p):
            return "local", "Runs on your hardware; you confirmed no paid API is behind it."
        return "unknown", "On your network. ROOKS cannot tell whether a paid API is behind it until you confirm it is not."
    if llm._is_openrouter(p):
        return "free", "Models ending in :free cost nothing (OpenRouter's limits apply); every other model is billed per token."
    if "generativelanguage.googleapis.com" in host:
        return ("free", "You confirmed this key's Google project has no billing account: free tier.") if llm.confirmed_free(p) else \
            ("unknown", "Free tier or billed, depending on the key's Google project. ROOKS cannot read that; confirm it in Settings.")
    if "opencode.ai/inference/go" in p["base_url"]:
        return "paid", "OpenCode Go subscription (a flat monthly fee, with OpenCode's usage limits)."
    if cat == "A":
        return "paid", f"Billed per token by {p['name']} to the API key you added."
    return "unknown", "Billing is whatever this server does. ROOKS treats it as possibly paid."


def _capabilities(ref: str) -> dict:
    """Per capability: tested ok, tested failed, or not tested; with when and what happened."""
    got = verify.results(ref)
    out = {}
    for cap, label in verify.CAPS.items():
        r = got.get(cap)
        out[cap] = {"label": label, "state": ("passed" if r["ok"] else "failed") if r else "not_tested",
                    "at": (r or {}).get("at", ""), "detail": (r or {}).get("detail", "")}
    return out


def _provider_entry(p: dict) -> dict:
    cat = _category(p)
    chk = _check(p["id"])
    cls, words = _billing(p, cat)
    ref = f"{p['id']}/{chk['recommended']}" if chk.get("recommended") else ""
    if not ref:  # no model passed the connection check: show the one you tested, if any, so its failures are visible
        tested = db.one("SELECT key FROM settings WHERE key LIKE ? ORDER BY key LIMIT 1", f"cap_check:{p['id']}/%")
        ref = tested["key"][len("cap_check:"):] if tested else ""
    if not p["enabled"]:
        status, why = "off", "Turned off in Settings."
    elif not chk:
        status, why = "not_checked", "Not checked yet. Check sends one small request."
    elif chk.get("ok"):
        status, why = "connected", f"{chk['recommended']} answered a request with a tool attached ({chk.get('checked_at', '')})."
    else:
        status, why = "failed", chk.get("error") or "The last check failed."
    e = {"id": f"provider:{p['id']}", "provider_id": p["id"], "category": cat, "name": p["name"], "kind": p["kind"],
         "host": _host(p["base_url"]), "billing": cls, "billing_detail": words, "status": status, "status_detail": why,
         "model": ref, "capabilities": _capabilities(ref) if ref else {}, "mode_block": llm.mode_allows(ref) if ref else "",
         "setup": "/settings#models", "powers_chat": True}
    if p["kind"] == "anthropic":
        e["engine"] = llm.engine(p) or "compatibility"
        e["engine_detail"] = ("Native Messages API through any-llm." if e["engine"] == "any-llm" else
                              "Anthropic's OpenAI compatibility endpoint." + ("" if anyllm_engine.available() else
                              " The native engine needs the optional any-llm package."))
    return e


def _coding_entries() -> list[dict]:
    cached = json.loads(db.get_setting("coding_agents", "{}") or "{}")
    agents = cached.get("agents") or {}
    out = []
    for key, name in (("codex", "OpenAI Codex CLI"), ("claude", "Claude Code")):
        a = agents.get(key) or {}
        if not cached:
            status, why = "not_checked", "Pair the ROOKS Companion, then check the computer's coding agents."
        elif not a.get("installed"):
            status, why = "not_found", a.get("reason") or cached.get("reason") or "Not installed on the paired computer."
        else:
            status, why = ("ready" if a.get("ready") else "needs_setup"), a.get("reason") or f"{a.get('version', '')} on {cached.get('device')}"
        billing = ("Signed in with ChatGPT: your plan's Codex limits. Signed in with an API key: OpenAI bills that key."
                   if key == "codex" else
                   "Only with your Anthropic API key, billed per use. ROOKS never uses a Claude subscription.")
        out.append({"id": f"coding:{key}", "category": "B", "name": name, "status": status, "status_detail": why,
                    "billing": "subscription" if key == "codex" and a.get("sign_in") == "chatgpt" else "paid",
                    "billing_detail": a.get("billing") or billing, "sign_in": a.get("sign_in", ""), "sandbox": a.get("sandbox", ""),
                    "powers_chat": False, "setup": "/computer",
                    "limits": ("Runs coding tasks in one allowed folder on your computer, after you approve each task. It does not "
                               "power ROOKS chat." if key == "codex" else
                               "ROOKS chat cannot use a Claude subscription. ROOKS chat can use an Anthropic API key (API providers), "
                               "billed per use; Claude Code can run coding tasks with one.")})
    return out


# One honest state per connection, from what was recorded (the detailed status stays beside it):
#   verified     a real request with a tool attached succeeded (providers), recorded with its time
#   untested     connected or installed, but ROOKS has not seen it do the work yet
#   available    known to ROOKS, not set up here
#   experimental works only in a limited way and is not meant for everyday use (the AirLLM bridge)
#   problem      the last check failed;  off: turned off by you
STATES = {"verified": "Verified", "untested": "Connected, not tested", "available": "Available, not set up",
          "experimental": "Experimental", "problem": "Check failed", "off": "Off"}
_STATE_OF = {"connected": "verified", "not_checked": "untested", "failed": "problem", "off": "off", "ready": "untested",
             "needs_setup": "available", "not_found": "available", "not_connected": "available", "installed": "untested",
             "not_installed": "available"}


def _is_airllm(e: dict, p: dict | None = None) -> bool:
    return bool(e.get("experimental") or (p and llm.experimental(p)))


def _with_state(e: dict, p: dict | None = None) -> dict:
    if _is_airllm(e, p):
        e["experimental"] = True
        e["powers_chat"] = False  # plain answers only: no tools, no streaming (docs/INTEGRATIONS.md, C)
        e["state"] = "off" if e["status"] == "off" else "experimental"
        e.setdefault("limits", "Plain answers only, minutes per answer, no tool calling or streaming. Not for everyday use; "
                               "Automatic never picks it because it cannot use tools.")
    elif e["category"] == "B" and e["status"] == "not_checked":
        e["state"] = "available"  # no computer paired: nothing is connected yet (for a provider, a saved key is)
    else:
        e["state"] = _STATE_OF.get(e["status"], "untested")
    e["state_label"] = STATES[e["state"]]
    return e


def directory() -> dict:
    providers = db.all("SELECT * FROM providers ORDER BY id")
    entries = [_with_state(_provider_entry(p), p) for p in providers]
    connected_local = {p["kind"] for p in providers if _category(p) == "C"} | {
        "llamacpp" for p in providers if _category(p) == "C" and ":8080" in p["base_url"]}
    for s in LOCAL_SETUP:
        if s["id"] not in connected_local and not any(s["name"].split()[0].lower() in e["name"].lower() for e in entries):
            entries.append({"id": f"local:{s['id']}", "category": "C", "name": s["name"], "status": "not_connected",
                            "status_detail": "Not connected. Nothing is installed, downloaded or started by ROOKS.", "billing": "local",
                            "billing_detail": "Runs on your hardware.", "setup_text": s["setup"], "powers_chat": s["id"] != "airllm",
                            "experimental": s["id"] == "airllm"})
    entries += _coding_entries()
    ver = anyllm_engine.available()
    entries.append({"id": "library:any-llm", "category": "D", "name": "any-llm (Mozilla AI)", "status": "installed" if ver else "not_installed",
                    "status_detail": (f"Version {ver}. Used by the Anthropic connections you switch to it." if ver else
                                      "Optional. Install with pip install any-llm-sdk==1.40.2 to use Anthropic's native API."),
                    "billing": "none", "billing_detail": "A library, not a service: billing is the connection's.", "powers_chat": False,
                    "licence": "Apache-2.0"})
    entries = [e if "state" in e else _with_state(e) for e in entries]
    groups = {"A": "API providers", "B": "Coding agents with their own sign-in", "C": "Local runtimes",
              "D": "Provider libraries", "E": "Other OpenAI-compatible servers"}
    return {"mode": llm.ai_mode(), "groups": groups, "entries": entries, "states": STATES,
            "note": "A key that is accepted proves only that. Chat, tools, streaming, JSON, images and embeddings are each tested "
                    "separately, and only when you ask."}
