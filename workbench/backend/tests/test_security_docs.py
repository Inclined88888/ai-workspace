"""SECURITY.md and the threat model must keep pointing at code that exists."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "workbench" / "backend"


def _resolve(ref: str) -> bool:
    for base in (ROOT, BACKEND, ROOT / "workbench" / "backend" / "app"):
        if (base / ref).exists():
            return True
    return False


def test_security_policy_exists_and_links_threat_model():
    text = (ROOT / "SECURITY.md").read_text()
    assert "security/advisories/new" in text
    assert "docs/security/THREAT-MODEL.md" in text
    assert (ROOT / "docs/security/THREAT-MODEL.md").is_file()


def test_threat_model_references_real_files():
    text = (ROOT / "docs/security/THREAT-MODEL.md").read_text()
    refs = set(re.findall(r"`([\w./-]+\.(?:py|yml|yaml|json|md))`", text))
    assert len(refs) >= 10
    missing = sorted(r for r in refs if not _resolve(r))
    assert not missing, missing
