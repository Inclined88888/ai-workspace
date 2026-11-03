"""Fine-tuning (Phase 4): turn your own conversations into a training set, train on a hosted provider, use the result.

Why hosted: training needs GPUs and gigabytes of weights, which this small CPU-only container deliberately doesn't carry.
So the pipeline is:

1. Dataset. You pick conversations; each becomes one chat example of user and assistant text only. Tool calls, tool
   output (third-party content), system prompts and memories are left out, and every message goes through the same
   secret redaction as the logs. Saved as chat JSONL ({"messages": [...]} per line), the format OpenAI, Mistral, Together,
   Unsloth, Axolotl and MLX-LM all read, so the same file also works for training on your own GPU (docs/finetune.md).
2. Job. On a connection whose provider offers OpenAI-style fine-tuning (/files + /fine_tuning/jobs: OpenAI, Mistral,
   or any compatible server), the file is uploaded and a job started. It costs money, so only you start it, from the
   page; the assistant has no tool for it.
3. Result. Refreshing a finished job adds the fine-tuned model to that connection's model list, so it can be picked
   anywhere (chat, router order, teammates) as "<connection>/<model>".
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, llm
from .adapters import credential
from .redact import redact
from .security import require_user

MIN_EXAMPLES = 10  # OpenAI's floor; below it a job is refused before anything is uploaded
MAX_EXAMPLE_CHARS = 60_000
DONE = ("succeeded", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS finetune_datasets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  examples INTEGER NOT NULL,
  chars INTEGER NOT NULL,
  conversation_ids TEXT NOT NULL,
  system TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finetune_jobs (
  id INTEGER PRIMARY KEY,
  dataset_id INTEGER NOT NULL,
  connection_id INTEGER NOT NULL,
  base_model TEXT NOT NULL,
  provider_file_id TEXT NOT NULL DEFAULT '',
  provider_job_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'starting',
  fine_tuned_model TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def ensure() -> None:
    db.conn().executescript(SCHEMA)


def _dir() -> Path:
    d = config.DATA_DIR / "finetune"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Datasets -----------------------------------------------------------------------------------------------------------
def example(conversation_id: int, system: str = "") -> dict | None:
    """One conversation as one chat example: user/assistant text only, redacted, starting with the user, ending with the
    assistant. None when nothing usable is left."""
    rows = db.all("SELECT role, content FROM messages WHERE conversation_id = ? AND role IN ('user', 'assistant') "
                  "ORDER BY id", conversation_id)
    msgs: list[dict] = []
    for r in rows:
        text = redact((r["content"] or "").strip())
        if not text:
            continue
        if msgs and msgs[-1]["role"] == r["role"]:  # a tool round in between: fold into one turn
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": r["role"], "content": text})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    while msgs and msgs[-1]["role"] != "assistant":
        msgs.pop()
    if len(msgs) < 2:
        return None
    while sum(len(m["content"]) for m in msgs) > MAX_EXAMPLE_CHARS and len(msgs) > 2:  # keep the most recent turns
        msgs = msgs[2:]
    if system.strip():
        msgs.insert(0, {"role": "system", "content": system.strip()})
    return {"messages": msgs}


def build_dataset(name: str, conversation_ids: list[int], system: str = "") -> dict:
    ensure()
    ids = sorted({int(i) for i in conversation_ids})
    found = {r["id"] for r in db.all(f"SELECT id FROM conversations WHERE id IN ({','.join('?' * len(ids))})", *ids)} if ids else set()
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(404, f"No conversation {missing[0]}")
    examples = [e for e in (example(i, system) for i in ids) if e]
    if not examples:
        raise HTTPException(422, "None of those conversations has a user message answered by the assistant.")
    path = _dir() / f"{uuid.uuid4().hex[:12]}.jsonl"
    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    did = db.run("INSERT INTO finetune_datasets(name, path, examples, chars, conversation_ids, system, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", name.strip()[:120] or "Dataset", str(path), len(examples),
                 sum(len(x) for x in lines), json.dumps(ids), system.strip()[:4000], db.now())
    return dataset(did)


def dataset(did: int) -> dict:
    ensure()
    d = db.one("SELECT * FROM finetune_datasets WHERE id = ?", did)
    if not d:
        raise HTTPException(404, "No such dataset")
    d["conversation_ids"] = json.loads(d["conversation_ids"])
    d["ready"] = d["examples"] >= MIN_EXAMPLES
    d["note"] = "" if d["ready"] else f"Hosted fine-tuning needs at least {MIN_EXAMPLES} examples; add more conversations."
    d.pop("path")
    return d


# Jobs ---------------------------------------------------------------------------------------------------------------
def _conn(cid: int) -> dict:
    c = db.one("SELECT * FROM connections WHERE id = ? AND enabled = 1", cid)
    if not c:
        raise HTTPException(404, "No such connection, or it is turned off")
    return c


def _err(conn: dict, r: httpx.Response) -> HTTPException:
    if r.status_code == 404:
        return HTTPException(400, f"{conn['name']} doesn't offer OpenAI-style fine-tuning (no /fine_tuning/jobs).")
    return HTTPException(502, f"{conn['name']} error {r.status_code}: {redact(r.text[:300])}")


async def start_job(dataset_id: int, connection_id: int, base_model: str, suffix: str = "") -> dict:
    ensure()
    d = db.one("SELECT * FROM finetune_datasets WHERE id = ?", dataset_id)
    if not d:
        raise HTTPException(404, "No such dataset")
    if d["examples"] < MIN_EXAMPLES:
        raise HTTPException(422, f"This dataset has {d['examples']} examples; hosted fine-tuning needs at least {MIN_EXAMPLES}.")
    conn = _conn(connection_id)
    base = conn["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {credential(conn)}"}
    now = db.now()
    jid = db.run("INSERT INTO finetune_jobs(dataset_id, connection_id, base_model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                 dataset_id, connection_id, base_model, now, now)
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            up = await c.post(f"{base}/files", headers=headers, data={"purpose": "fine-tune"},
                              files={"file": (f"dataset-{dataset_id}.jsonl", Path(d["path"]).read_bytes(), "application/jsonl")})
            if up.status_code >= 400:
                raise _err(conn, up)
            fid = up.json().get("id", "")
            body = {"training_file": fid, "model": base_model}
            if suffix.strip():
                body["suffix"] = re.sub(r"[^a-z0-9-]", "-", suffix.strip().lower())[:18]
            r = await c.post(f"{base}/fine_tuning/jobs", headers=headers, json=body)
            if r.status_code >= 400:
                db.run("UPDATE finetune_jobs SET provider_file_id = ? WHERE id = ?", fid, jid)
                raise _err(conn, r)
            j = r.json()
    except HTTPException as e:
        db.run("UPDATE finetune_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?", str(e.detail)[:500], db.now(), jid)
        raise
    except httpx.HTTPError as e:
        db.run("UPDATE finetune_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?", type(e).__name__, db.now(), jid)
        raise HTTPException(502, f"{conn['name']} didn't answer ({type(e).__name__})") from e
    db.run("UPDATE finetune_jobs SET provider_file_id = ?, provider_job_id = ?, status = ?, updated_at = ? WHERE id = ?",
           fid, j.get("id", ""), j.get("status", "queued"), db.now(), jid)
    return job(jid)


def job(jid: int) -> dict:
    ensure()
    j = db.one("SELECT j.*, c.name AS provider FROM finetune_jobs j LEFT JOIN connections c ON c.id = j.connection_id WHERE j.id = ?", jid)
    if not j:
        raise HTTPException(404, "No such job")
    j["model_ref"] = f"{j['connection_id']}/{j['fine_tuned_model']}" if j["fine_tuned_model"] else ""
    return j


def _register(conn: dict, model: str) -> None:
    """Add the fine-tuned model to the connection's extra models so every model picker offers it."""
    have = [m.strip() for m in (conn["models"] or "").split(",") if m.strip()]
    if model not in have:
        db.run("UPDATE connections SET models = ? WHERE id = ?", ",".join(have + [model]), conn["id"])
    llm._model_cache.pop(conn["id"], None)


async def refresh(jid: int) -> dict:
    j = job(jid)
    if j["status"] in DONE or not j["provider_job_id"]:
        return j
    conn = _conn(j["connection_id"])
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{conn['base_url'].rstrip('/')}/fine_tuning/jobs/{j['provider_job_id']}",
                        headers={"Authorization": f"Bearer {credential(conn)}"})
    if r.status_code >= 400:
        raise _err(conn, r)
    d = r.json()
    status, model = d.get("status", j["status"]), d.get("fine_tuned_model") or ""
    error = redact(str((d.get("error") or {}).get("message", "") if isinstance(d.get("error"), dict) else d.get("error") or ""))[:500]
    db.run("UPDATE finetune_jobs SET status = ?, fine_tuned_model = ?, error = ?, updated_at = ? WHERE id = ?",
           status, model, error, db.now(), jid)
    if status == "succeeded" and model:
        _register(conn, model)
    return job(jid)


async def cancel(jid: int) -> dict:
    j = job(jid)
    if j["status"] in DONE:
        return j
    conn = _conn(j["connection_id"])
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{conn['base_url'].rstrip('/')}/fine_tuning/jobs/{j['provider_job_id']}/cancel",
                         headers={"Authorization": f"Bearer {credential(conn)}"})
    if r.status_code >= 400:
        raise _err(conn, r)
    db.run("UPDATE finetune_jobs SET status = 'cancelled', updated_at = ? WHERE id = ?", db.now(), jid)
    return job(jid)


# HTTP ---------------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/finetune", dependencies=[Depends(require_user)])


class DatasetIn(BaseModel):
    name: str = Field("", max_length=120)
    conversation_ids: list[int] = Field(..., min_length=1, max_length=5000)
    system: str = Field("", max_length=4000)


class JobIn(BaseModel):
    dataset_id: int
    connection_id: int
    base_model: str = Field(..., min_length=1, max_length=200)
    suffix: str = Field("", max_length=40)


@router.get("/conversations")
def candidates(limit: int = 300):
    """Conversations worth training on: ones with at least one user message answered by the assistant."""
    return db.all(
        "SELECT c.id, c.title, c.channel, c.updated_at, "
        "SUM(m.role = 'user') AS user_messages, SUM(m.role = 'assistant') AS assistant_messages "
        "FROM conversations c JOIN messages m ON m.conversation_id = c.id "
        "GROUP BY c.id HAVING user_messages > 0 AND assistant_messages > 0 ORDER BY c.updated_at DESC LIMIT ?", min(limit, 2000))


@router.get("/datasets")
def list_datasets():
    ensure()
    return [dataset(r["id"]) for r in db.all("SELECT id FROM finetune_datasets ORDER BY id DESC")]


@router.post("/datasets")
def create_dataset(body: DatasetIn):
    return build_dataset(body.name, body.conversation_ids, body.system)


@router.get("/datasets/{did}/download")
def download(did: int):
    ensure()
    d = db.one("SELECT * FROM finetune_datasets WHERE id = ?", did)
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404, "No such dataset")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", d["name"]).strip("-") or "dataset"
    return FileResponse(d["path"], media_type="application/jsonl", filename=f"{safe}.jsonl")


@router.delete("/datasets/{did}")
def delete_dataset(did: int):
    ensure()
    d = db.one("SELECT * FROM finetune_datasets WHERE id = ?", did)
    if d:
        Path(d["path"]).unlink(missing_ok=True)
        db.run("DELETE FROM finetune_datasets WHERE id = ?", did)
    return {"ok": True}


@router.get("/jobs")
def list_jobs():
    ensure()
    return [job(r["id"]) for r in db.all("SELECT id FROM finetune_jobs ORDER BY id DESC")]


@router.post("/jobs")
async def create_job(body: JobIn):
    return await start_job(body.dataset_id, body.connection_id, body.base_model.strip(), body.suffix)


@router.post("/jobs/{jid}/refresh")
async def refresh_job(jid: int):
    return await refresh(jid)


@router.post("/jobs/{jid}/cancel")
async def cancel_job(jid: int):
    return await cancel(jid)
