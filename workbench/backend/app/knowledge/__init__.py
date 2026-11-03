"""Shared memory and data layer (feat/memory-core). The one interface every feature uses.

    from app import knowledge
    knowledge.remember("Sam prefers quotes by email", "profile", subject_id=sam["id"], scope="team:1")
    knowledge.recall("what does Sam like", scopes=knowledge.visible_scopes(team="1"))
    knowledge.context_for(user_text, budget_tokens=600, project_id=pid)   # system-prompt block + ids used
    knowledge.upsert_entity("person", "Sam Lee", {"email": "sam@x.com"}); knowledge.link(a, "works_at", b)
    knowledge.why(memory_id); knowledge.forget(memory_id); knowledge.scoped("assistant:3").add("...")

See docs/data-model.md.
"""
from __future__ import annotations

from . import adapters, crypto, graph, index, memory, recall as _recall, schema
from .graph import RELS, TYPES
from .memory import PRIVACY, TIERS, conflicts, consolidate, resolve, why
from .recall import context_for, recall, search_all, visible_scopes

API_VERSION = 1
ensure = schema.ensure
remember = memory.remember
update_memory = memory.update
forget = memory.forget
get_memory = memory.get
list_memories = memory.list_
upsert_entity = graph.upsert
get_entity = graph.get
find_entity = graph.find
list_entities = graph.list_
link = graph.link
unlink = graph.unlink
neighbours = graph.neighbours
forget_entity = graph.forget
merge_entities = graph.merge
duplicates = graph.duplicates
index_item = index.put
unindex_item = index.drop
set_embedder = index.set_embedder


class Scoped:
    """A memory scope for one assistant/teammate ("assistant:<id>"), a team ("team:<id>") or "shared".
    Recall sees this scope plus everything shared (and "private" items when include_private)."""

    def __init__(self, scope: str, include_shared: bool = True):
        self.scope = scope
        self.scopes = [scope] + (["shared"] if include_shared and scope != "shared" else [])

    def add(self, content: str, tier: str = "profile", **kw) -> dict:
        return memory.remember(content, tier, scope=self.scope, **kw)

    def list(self, **kw) -> list[dict]:
        return memory.list_(scopes=[self.scope], **kw)

    def delete(self, mid: int) -> dict:
        m = memory.get(mid)
        if m["scope"] != self.scope:
            from fastapi import HTTPException

            raise HTTPException(404, "Not found")
        return memory.forget(mid)

    def recall(self, query: str, k: int = 10, **kw) -> list[dict]:
        return _recall.recall(query, scopes=self.scopes, k=k, **kw)

    def context(self, query: str, budget_tokens: int = 600, **kw) -> dict:
        return _recall.context_for(query, budget_tokens=budget_tokens, scopes=self.scopes, **kw)


def scoped(scope: str, include_shared: bool = True) -> Scoped:
    return Scoped(scope, include_shared)


def export_all() -> dict:
    """Full export (free on every plan): entities, links, memories (decrypted, it's the owner's own copy), conflicts."""
    schema.ensure()
    from .. import db

    return {"format": "ai-workspace-knowledge", "version": API_VERSION, "schema": [m["version"] for m in schema.applied()],
            "entities": graph.list_(limit=1_000_000),
            "links": db.all("SELECT e1.uid AS src, rel, e2.uid AS dst, confidence, k.source FROM kn_edges k "
                            "JOIN kn_entities e1 ON e1.id = k.src JOIN kn_entities e2 ON e2.id = k.dst"),
            "memories": [memory._out(r) for r in db.all("SELECT * FROM kn_memories ORDER BY id")]}
