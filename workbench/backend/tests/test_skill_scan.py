"""Skills security gate: malicious fixtures must be blocked (High), benign ones must pass (Low)."""

import io
import tarfile
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "skills"
MALICIOUS = {
    "mal-curl-pipe": {"pipe-to-shell"},
    "mal-key-stealer": {"secret-files", "env-dump", "exfil-endpoint"},
    "mal-prompt-injection": {"ignore-instructions", "secrecy", "credential-grab", "approval-bypass", "hidden-chars", "hidden-comment"},
    "mal-obfuscated": {"decode-and-run"},
    "mal-persistence": {"persistence", "write-outside"},
    "mal-runtime-only": set(),  # nothing static gives it away; the sandbox trial must
}
BENIGN = ["benign-commit-messages", "benign-word-count", "benign-mcp-notes"]


def review(name):
    from app import skills_hub

    folder = FIX / name
    return skills_hub.review_folder(folder, skills_hub.read_manifest(folder))


@pytest.mark.parametrize("name", sorted(MALICIOUS))
def test_malicious_skills_are_high_risk(client, name):
    r = review(name)
    rules = {f["rule"] for f in r["static"] + r["injection"]}
    assert MALICIOUS[name] <= rules, f"{name}: missing {MALICIOUS[name] - rules}"
    assert r["risk"]["level"] == "high", (name, r["risk"])
    assert r["risk"]["reasons"]


@pytest.mark.parametrize("name", BENIGN)
def test_benign_skills_pass(client, name):
    r = review(name)
    assert r["risk"]["level"] == "low", (name, r["risk"], r["static"], r["injection"])
    assert not [f for f in r["static"] + r["injection"] if f["severity"] in ("critical", "high")]


def test_sandbox_trial_catches_what_static_rules_miss(client):
    r = review("mal-runtime-only")
    assert not [f for f in r["static"] if f["severity"] in ("critical", "high")]
    assert any(v["kind"] == "network" and "telemetry.example.net" in v["what"] for v in r["trial"]["violations"])


def test_sandbox_blocks_secret_reads_shell_and_writes_outside(client, tmp_path):
    from app import skill_sandbox

    (tmp_path / "x.py").write_text(
        "import os, subprocess\n"
        "for f in (os.path.expanduser('~/.ssh/id_rsa'), '/etc/passwd-not-ours'):\n"
        "    try: open(f, 'a')\n    except PermissionError as e: print('blocked', e)\n"
        "try: subprocess.run(['true'])\nexcept PermissionError: print('shell blocked')\n"
        "open('own.txt', 'w').write('fine')\nprint('ok')\n")
    r = skill_sandbox.run(tmp_path, "x.py")
    kinds = {v["kind"] for v in r["violations"]}
    assert {"shell"} <= kinds and ({"secret-read", "write-outside"} & kinds)
    assert "ok" in r["stdout"] and r["created_files"] == ["own.txt"]
    granted = skill_sandbox.run(tmp_path, "x.py", grants={"shell": True})
    assert "shell" not in {v["kind"] for v in granted["violations"]}


def test_benign_script_runs_in_trial(client):
    r = review("benign-word-count")
    assert r["trial"]["status"] == "passed"
    assert "words=17" in r["trial"]["runs"][0]["stdout"]


def test_permissions_in_plain_words(client):
    r = review("mal-key-stealer")
    assert "discord.com" in r["permissions"]["network"]
    assert any(line.startswith("Connect to:") for line in r["permissions_plain"])
    calm = review("benign-commit-messages")
    assert calm["permissions_plain"][0] == "No internet access needed"


def test_mcp_server_skill_is_detected(client):
    from app import skills_hub

    man = skills_hub.read_manifest(FIX / "benign-mcp-notes")
    assert man["kind"] == "mcp" and man["mcp"]["command"] == "node" and man["mcp"]["args"] == ["server.js"]


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/anthropics/skills", ("anthropics", "skills", "", "")),
    ("github.com/anthropics/skills/tree/main/skills/pdf", ("anthropics", "skills", "main", "skills/pdf")),
    ("https://github.com/o/r/blob/abc123/a/b/SKILL.md", ("o", "r", "abc123", "a/b")),
    ("o/r", ("o", "r", "", "")),
])
def test_parse_url(url, expected):
    from app import skills_hub

    p = skills_hub.parse_url(url)
    assert (p["owner"], p["repo"], p["ref"], p["path"]) == expected


@pytest.mark.parametrize("bad", ["https://gitlab.com/a/b", "nothing", "https://github.com/o/r/tree/main/../x"])
def test_parse_url_rejects(bad):
    from fastapi import HTTPException

    from app import skills_hub

    with pytest.raises(HTTPException):
        skills_hub.parse_url(bad)


def _tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, "/home/user/.ssh/id_rsa"
                t.addfile(info)
            else:
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_refuses_links_and_escapes(tmp_path):
    from app import skills_hub

    data = _tar([("o-r-sha/skills/a/SKILL.md", b"---\nname: a\n---\nhi", "file"),
                 ("o-r-sha/skills/a/key", b"", "sym"),
                 ("o-r-sha/skills/a/../../evil.txt", b"x", "file"),
                 ("o-r-sha/skills/b/SKILL.md", b"x", "file")])
    choices = skills_hub.extract(data, "skills", tmp_path / "all")
    assert choices == ["a", "b"]
    skills_hub.extract(data, "skills/a", tmp_path / "a")
    assert sorted(p.name for p in (tmp_path / "a").rglob("*")) == ["SKILL.md"]
    assert not (tmp_path / "evil.txt").exists()
