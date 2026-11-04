"""B6 redaction from the terminal: `rooks redact` hides secrets in piped text offline, and --check gates CI.
Fake secrets are assembled at run time so the repository's own secret scanner (gitleaks hook) stays quiet."""

from conftest import rooks

AWS = "AKIA" + "Q" * 16
OPENAI = "sk-" + "proj-" + "a1B2" * 8
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmU=\n-----END " + "OPENSSH PRIVATE KEY-----"
LOG = f"""deploying with {AWS}
OPENAI_API_KEY={OPENAI}
contact ada@example.com or +61 412 345 678
{PEM}
built 0.40.12 on 2026-11-04 at 192.168.1.20 in 1500 ms
"""


def test_rooks_redact_hides_every_secret_and_keeps_ordinary_text(project):
    r = rooks(project, "redact", stdin=LOG)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for secret in (AWS, OPENAI, "ada@example.com", "412 345 678", "b3BlbnNzaC1rZXktdjEAAAAA"):
        assert secret not in out
    assert "<redacted:aws-key>" in out and "<redacted:private-key>" in out and "<email>" in out and "<phone>" in out
    assert "OPENAI_API_KEY=<redacted>" in out or "<redacted:key>" in out
    # versions, dates, IP addresses and counts are not personal data
    assert "built 0.40.12 on 2026-11-04 at 192.168.1.20 in 1500 ms" in out


def test_check_fails_on_secrets_and_passes_clean_text(project, tmp_path):
    bad = rooks(project, "redact", "--check", stdin=LOG)
    assert bad.returncode == 1
    for kind in ("aws-key", "private-key", "email", "phone"):
        assert kind in bad.stdout
    assert AWS not in bad.stdout  # the summary never echoes a secret

    f = tmp_path / "clean.txt"
    f.write_text("all tests passed in 4.2s\n")
    ok = rooks(project, "redact", "--check", str(f))
    assert ok.returncode == 0 and "clean" in ok.stdout
    assert rooks(project, "redact", str(tmp_path / "missing.txt")).returncode == 2


def test_the_vendored_rules_still_match_the_app():
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    app = (here.parent / "workbench" / "backend" / "app" / "redact.py").read_text()
    mine = (here / "rooks_code" / "redact.py").read_text()
    block = lambda s: s[s.index("_PATTERNS = ["):s.index("\n]\n", s.index("_PATTERNS = ["))]  # noqa: E731
    assert block(app) == block(mine)
