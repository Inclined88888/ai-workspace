"""feat/memory-core on the phone: read-only search, People and Projects over the device-token API (/api/mobile/knowledge/*).

Read-only by design: editing and forgetting happen on the computer. Sensitive memories are never sent to the phone;
it shows that one exists and to open it on the computer.
"""
from fastapi import APIRouter, Depends, Query

from ..mobile import require_device
from . import graph, memory, schema
from .recall import search_all

device = APIRouter(prefix="/api/mobile/knowledge", dependencies=[Depends(require_device)])
HIDDEN = "Sensitive. Open it on your computer to see it."


def _mem(m: dict) -> dict:
    return {"id": m["id"], "tier": m["tier"], "content": HIDDEN if m["privacy"] == "sensitive" else m["content"],
            "sensitive": m["privacy"] == "sensitive", "observed_at": m["observed_at"], "you_told_me": m["source_kind"] == "user"}


@device.get("/search")
def m_search(q: str = Query("", max_length=200)):
    schema.ensure()
    if len(q.strip()) < 2:
        return []
    out = []
    for h in search_all(q, limit=30):
        if h["kind"] == "memory":
            try:
                m = memory.get(int(h["ref"]))
            except Exception:  # noqa: BLE001
                continue
            if m["privacy"] == "sensitive" or m["scope"] == "private":
                continue
        out.append(h)
    return out


@device.get("/entities")
def m_entities(type: str = "person", q: str = ""):
    schema.ensure()
    return [{"id": e["id"], "type": e["type"], "name": e["name"],
             "sub": " · ".join(str(e["attrs"].get(k)) for k in ("role", "company", "kind", "status") if e["attrs"].get(k))}
            for e in graph.list_(type if type in ("person", "project") else "person", q) if e["privacy"] != "sensitive"]


@device.get("/entities/{eid}")
def m_entity(eid: int):
    e = graph.get(eid)
    links = [{"id": n["id"], "type": n["type"], "name": n["name"], "rel": n["rel"]} for n in graph.neighbours(eid, 1)]
    mems = {m["id"]: m for m in memory.list_(subject_id=eid) + memory.list_(project_id=eid)}
    return {"id": e["id"], "type": e["type"], "name": e["name"], "attrs": {k: v for k, v in e["attrs"].items() if k in ("role", "company", "email", "phone", "kind", "status")},
            "links": links, "memories": [_mem(m) for m in mems.values() if m["scope"] != "private"]}
