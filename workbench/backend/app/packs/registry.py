"""Pack catalogue and one-click add, through the same security gate as skills from GitHub (feat/v02-packs).

Adding a pack: for every skill folder in the bundle, copy it to the hub's staging area, run the hub's own review
(static scan, prompt-injection scan, permissions, sandboxed trial, risk) and install it only if the risk is low and it
asks for no extra permissions. If any skill fails the gate nothing is installed and the user sees why.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from fastapi import HTTPException

from .. import db, skills_hub
from ..features_v02 import tier as feature_tier
from . import store

BUNDLES = Path(__file__).parent / "bundles"
PACK_IDS = ("trade", "legal", "study")


def allowed(feature: str) -> bool:
    """Pro gating hook. Uses account.allowed(feature) when the accounts branch (feat/accounts-and-pro) is merged."""
    try:
        from .. import account  # type: ignore[attr-defined]
    except ImportError:
        return True
    try:
        return bool(account.allowed(feature))
    except Exception:  # noqa: BLE001 - a broken licence check must not lock people out of their own data
        return True


def require_feature(feature: str) -> None:
    if not allowed(feature):
        raise HTTPException(402, f"This is part of Pro. Upgrade on the Account page to use it. ({feature})")


def manifest(pack_id: str) -> dict:
    if pack_id not in PACK_IDS:
        raise HTTPException(404, "No such pack")
    folder = BUNDLES / pack_id
    data = json.loads((folder / "pack.json").read_text())
    skills = []
    for sk in sorted((folder / "skills").iterdir()):
        if (sk / "SKILL.md").is_file():
            man = skills_hub.read_manifest(sk)
            skills.append({"folder": sk.name, "name": skills_hub.skill_name(man["meta"], {"path": sk.name, "repo": pack_id}),
                           "description": str(man["meta"].get("description", ""))})
    templates = sorted(p.name for p in (folder / "templates").iterdir()) if (folder / "templates").is_dir() else []
    return {**data, "skills": skills, "templates": templates, "feature": f"packs.{pack_id}", "tier": feature_tier(f"packs.{pack_id}")}


def template(pack_id: str, name: str) -> str:
    p = (BUNDLES / pack_id / "templates" / name).resolve()
    if BUNDLES.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "No such template")
    return p.read_text()


def installed(pack_id: str) -> dict | None:
    store.ensure()
    row = db.one("SELECT * FROM packs_installed WHERE pack_id = ?", pack_id)
    if row:
        row["skills"] = json.loads(row["skills"])
        row["gate"] = json.loads(row["gate"])
    return row


def is_installed(pack_id: str) -> bool:
    try:
        return installed(pack_id) is not None
    except Exception:  # noqa: BLE001 - used by tool filters; never break a chat turn
        return False


def require(pack_id: str) -> None:
    if not installed(pack_id):
        raise HTTPException(409, f"Add the {manifest(pack_id)['name']} first (Packs page).")
    require_feature(f"packs.{pack_id}")


def catalogue() -> list[dict]:
    out = []
    for pid in PACK_IDS:
        m = manifest(pid)
        row = installed(pid)
        out.append({**m, "installed": bool(row), "installed_at": row["installed_at"] if row else None,
                    "gate": row["gate"] if row else {}, "allowed": allowed(m["feature"])})
    return out


def _digest(folder: Path) -> str:
    h = hashlib.sha1()  # noqa: S324 - a content id shown like a commit SHA, not a security boundary
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(folder)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def add(pack_id: str) -> dict:
    """One click: every skill in the pack through the security gate, then installed together, or none at all."""
    m = manifest(pack_id)
    store.ensure()
    if installed(pack_id):
        return {**catalogue()[PACK_IDS.index(pack_id)], "already": True}
    staged: list[tuple[int, dict]] = []
    gate: dict[str, dict] = {}
    try:
        for sk in m["skills"]:
            src = BUNDLES / pack_id / "skills" / sk["folder"]
            sha = _digest(src)
            sid = db.run(
                "INSERT INTO hub_skills (name, kind, source_url, owner, repo, path, ref, commit_sha, status, created_at, updated_at) "
                "VALUES (?, 'skill', ?, 'ai-workspace', 'packs', ?, ?, ?, 'checking', ?, ?)",
                sk["name"], f"pack:{pack_id}/{sk['folder']}", f"{pack_id}/{sk['folder']}", m["version"], sha, db.now(), db.now())
            folder = skills_hub.staged_dir(sid)
            shutil.rmtree(folder, ignore_errors=True)
            shutil.copytree(src, folder)
            man = skills_hub.read_manifest(folder)
            review = skills_hub.review_folder(folder, man)
            perms = review["permissions"]
            extra = [k for k in ("network", "files", "env", "tools") if perms.get(k)] + (["shell"] if perms.get("shell") else [])
            files = sorted(str(p.relative_to(folder)) for p in skills_hub.skill_scan.iter_files(folder))
            report = {**review, "manifest": {"kind": man["kind"], "meta": skills_hub._jsonable(man["meta"]), "mcp": None}, "files": files,
                      "source": {"owner": "ai-workspace", "repo": "packs", "path": f"{pack_id}/{sk['folder']}", "ref": m["version"], "sha": sha}}
            db.run("UPDATE hub_skills SET status = 'reviewed', risk = ?, description = ?, report = ?, updated_at = ? WHERE id = ?",
                   review["risk"]["level"], sk["description"][:300], json.dumps(report), db.now(), sid)
            gate[sk["name"]] = {"risk": review["risk"]["level"], "reasons": review["risk"].get("reasons", []),
                                "findings": len(review["static"]) + len(review["injection"]), "extra_permissions": extra}
            staged.append((sid, sk))
            if review["risk"]["level"] != "low" or extra:
                raise HTTPException(409, f"{sk['name']} did not pass the security check ({review['risk']['level']} risk"
                                         + (f", asks for {', '.join(extra)}" if extra else "") + "). Nothing was added.")
        ids = []
        for sid, _ in staged:
            done = skills_hub.install(sid, {})
            db.run("UPDATE skills SET steps = replace(steps, ?, ?) WHERE name = ? AND source = 'github'",
                   f"(Installed from github.com/ai-workspace/packs at {done['commit_sha'][:12]}.",
                   f"(Part of the {m['name']} {m['version']}, bundled with the app and checked by the security gate.", done["name"])
            skills_hub.audit(sid, db.one("SELECT name FROM hub_skills WHERE id = ?", sid)["name"], "pack", f"added with the {m['name']}")
            ids.append(sid)
    except BaseException:
        for sid, _ in staged:
            row = db.one("SELECT * FROM hub_skills WHERE id = ?", sid)
            if row and row["status"] == "installed":
                skills_hub.uninstall(sid)
            else:
                shutil.rmtree(skills_hub.staged_dir(sid), ignore_errors=True)
                db.run("UPDATE hub_skills SET status = 'rejected', updated_at = ? WHERE id = ?", db.now(), sid)
        raise
    db.run("INSERT INTO packs_installed (pack_id, version, skills, gate, installed_at) VALUES (?, ?, ?, ?, ?)",
           pack_id, m["version"], json.dumps(ids), json.dumps(gate), db.now())
    return catalogue()[PACK_IDS.index(pack_id)]


def remove(pack_id: str) -> dict:
    """Removes the pack's skills and shortcuts. The user's quotes, clauses, cards and notebook stay (they are their data)."""
    row = installed(pack_id)
    if not row:
        raise HTTPException(404, "That pack is not added")
    for sid in row["skills"]:
        r = db.one("SELECT status FROM hub_skills WHERE id = ?", sid)
        if r and r["status"] in ("installed", "disabled"):
            skills_hub.uninstall(sid)
    db.run("DELETE FROM packs_installed WHERE pack_id = ?", pack_id)
    return catalogue()[PACK_IDS.index(pack_id)]
