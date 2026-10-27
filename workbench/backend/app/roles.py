"""Model roles: which model does which kind of work, chosen by you and checked against what the model can really do.

Roles: chat (the assistant), planning (mission plans and step checks), research, coding, documents, vision, long
context and local. Each model's capabilities come with their evidence:
  * tested: ROOKS sent the model a real request that needed the capability and it answered correctly
    (the connection check with a tool attached; the image test; the JSON test below)
  * reported: the provider's model list says so (for example tool support or context length), not tested
  * unknown: neither
A role is refused for a model whose required capability is not tested (tools, vision, JSON) or not at least reported
(context length); a local role is refused for a model that is not on this computer or your network.
A role you set is used exactly: if that model fails, the work fails visibly; ROOKS never switches it for another.
Leaving a role empty uses the default model and its backups, as before. Tests here are capped like connection checks
and only run when you ask; nothing is tested in the background.
"""

import json

from fastapi import HTTPException

from . import db, llm, usage

ROLES = {
    "chat": ("Chat", "The assistant itself; needs tools", ("tools",)),
    "planning": ("Planning", "Mission plans and step checks; needs reliable JSON", ("json",)),
    "research": ("Research", "Deep Research reading and writing; needs JSON and a long context (32k or more)", ("json", "ctx32k")),
    "coding": ("Coding", "Code questions and GitHub work; needs tools", ("tools",)),
    "documents": ("Documents", "Long documents and reports; needs a long context (32k or more)", ("ctx32k",)),
    "vision": ("Vision", "Reading screenshots for Computer Use; needs the image test", ("vision",)),
    "long_context": ("Long context", "Very long inputs; needs 100k or more", ("ctx100k",)),
    "local": ("Local", "Private work that must stay on your network", ("local",)),
}
USED_BY = {"planning": "Mission plans, step checks and reports, unless the mission has its own model",
           "research": "Deep Research, unless you pick a model for the run",
           "chat": "Chat, when the conversation, agent and project have no model of their own",
           "vision": "Visual targeting (the same setting as Computer, Visual targeting)",
           "local": "Local-only projects that have no model of their own"}


def _info(ref: str) -> dict:
    try:
        p, model = llm.split_ref(ref)
    except HTTPException:
        return {}
    listed = next((m for m in llm._model_cache.get(p["id"], (0, []))[1] if m["model"] == model), {})
    check = llm.check_info(p["id"])
    try:
        js = json.loads(db.get_setting(f"json_check:{ref}", "") or "{}")
    except ValueError:
        js = {}
    try:
        vis = json.loads(db.get_setting(f"vision_check:{ref}", "") or "{}")
    except ValueError:
        vis = {}
    return {"p": p, "model": model, "listed": listed, "tools_tested": check.get("ok") and check.get("recommended") == model,
            "json": js, "vision": vis}


def capabilities(ref: str) -> dict:
    """Each capability as tested | reported | no | unknown, with where that came from."""
    i = _info(ref)
    if not i:
        return {"ref": ref, "error": "Unknown model or provider."}
    caps = {}
    if i["tools_tested"]:
        caps["tools"] = {"state": "tested", "how": "answered the connection check with a tool attached"}
    elif i["listed"].get("tools") is not None:
        caps["tools"] = {"state": "reported" if i["listed"]["tools"] else "no", "how": "the provider's model list"}
    else:
        caps["tools"] = {"state": "unknown", "how": "run Check on the provider"}
    caps["json"] = ({"state": "tested" if i["json"].get("ok") else "no", "how": f"JSON test {i['json'].get('at', '')}"}
                    if i["json"] else {"state": "unknown", "how": "run the JSON test"})
    caps["vision"] = ({"state": "tested" if i["vision"].get("ok") else "no", "how": f"image test {i['vision'].get('at', '')}"}
                      if i["vision"] else {"state": "unknown", "how": "run the image test (Computer, Visual targeting)"})
    ctx = i["listed"].get("ctx")
    caps["context"] = {"state": "reported", "tokens": ctx, "how": "the provider's model list"} if ctx else {"state": "unknown", "tokens": None,
                                                                                                         "how": "the provider does not report it"}
    caps["local"] = {"state": "tested" if llm.is_local(ref) else "no", "how": "where the provider's address points"}
    caps["free"] = {"state": "reported" if llm.is_free(i["p"], i["model"]) else "no", "how": "local, or an OpenRouter :free model"}
    return {"ref": ref, "capabilities": caps}


def problems(ref: str, role: str) -> list[str]:
    caps = capabilities(ref).get("capabilities")
    if caps is None:
        return ["Unknown model or provider."]
    out = []
    for need in ROLES[role][2]:
        if need == "tools" and caps["tools"]["state"] != "tested":
            out.append("tool use has not been tested (run Check on its provider; only the model that answered counts)")
        elif need == "json" and caps["json"]["state"] != "tested":
            out.append("it has not passed the JSON test")
        elif need == "vision" and caps["vision"]["state"] != "tested":
            out.append("it has not passed the image test")
        elif need.startswith("ctx"):
            want = 100_000 if need == "ctx100k" else 32_000
            got = caps["context"]["tokens"]
            if not got:
                out.append(f"its context length is not reported by the provider, so {want // 1000}k cannot be confirmed")
            elif got < want:
                out.append(f"its context is {got:,} tokens, under the {want:,} this role needs")
        elif need == "local" and caps["local"]["state"] != "tested":
            out.append("it is not on this computer or your network")
    return out


def get() -> dict[str, str]:
    try:
        saved = json.loads(db.get_setting("model_roles", "") or "{}")
    except ValueError:
        saved = {}
    out = {r: saved.get(r, "") if isinstance(saved.get(r, ""), str) else "" for r in ROLES}
    out["vision"] = db.get_setting("vision_model", "") or ""  # one source of truth with the vision settings
    return out


def model_for(role: str) -> str | None:
    """The model set for this role, or None (use the default model and its backups)."""
    return get().get(role) or None


def set_roles(changes: dict[str, str]) -> dict:
    cur = get()
    for role, ref in changes.items():
        if role not in ROLES:
            raise HTTPException(400, f"Unknown role {role}")
        if role == "vision":
            from . import vision

            vision.configure(ref, None, None, False, None)  # a new vision model starts off; the image test and consent come next
            continue
        if ref and (why := problems(ref, role)):
            raise HTTPException(400, f"{ref} cannot be the {ROLES[role][0]} model: " + "; ".join(why) + ".")
        cur[role] = ref or ""
    cur.pop("vision", None)
    db.set_setting("model_roles", json.dumps(cur))
    return describe()


def describe() -> dict:
    cur = get()
    return {"roles": [{"role": r, "label": label, "about": about, "ref": cur.get(r, ""),
                       "used_by": USED_BY.get(r, "Not used automatically yet: choose this model in chat when you need it"),
                       "problems": problems(cur[r], r) if cur.get(r) else []} for r, (label, about, _) in ROLES.items()]}


async def json_check(ref: str) -> dict:
    """A capped test that the model follows a JSON format exactly, as planning and research need."""
    out = {"ok": False, "at": db.now(), "error": ""}
    try:
        res = await llm._chat_once(ref, [{"role": "system", "content": 'Reply with only JSON like {"sum": number, "word": "text"}.'},
                                         {"role": "user", "content": "sum is 17 plus 25; word is the colour of a clear daytime sky"}], None, ping=True)
        usage.record(res, channel="check")
        got = llm.parse_json(res["message"].get("content") or "")
        out["ok"] = isinstance(got, dict) and got.get("sum") in (42, 42.0, "42") and "blue" in str(got.get("word", "")).lower()
        if not out["ok"]:
            out["error"] = f"It replied {str(res['message'].get('content'))[:80]!r}, not the JSON asked for."
    except HTTPException as e:
        out["error"] = str(e.detail)[:200]
    db.set_setting(f"json_check:{ref}", json.dumps(out))
    return out
