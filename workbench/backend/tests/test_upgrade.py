"""scripts/upgrade.sh against throwaway installs: checksum, unsafe packages, conflicts, backup, removal, rollback."""

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "upgrade.sh"


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


BASE = {"docker-compose.yml": "services: {}\n", "setup.sh": "#!/bin/sh\necho setup\n", "workbench/app.py": "v1\n",
        "workbench/old.py": "old\n"}


def package(tmp: Path, files: dict[str, str], name="rooks-update-test.tar.gz", checksum=True) -> Path:
    src = make_tree(tmp / "pkgsrc" / name / "ai-workspace", files)
    out = tmp / name
    with tarfile.open(out, "w:gz") as t:
        t.add(src, arcname="ai-workspace")
    if checksum:
        Path(f"{out}.sha256").write_text(hashlib.sha256(out.read_bytes()).hexdigest() + f"  {out.name}\n")
    return out


def run(tmp: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "ROOKS_DIR": str(tmp / "ai-workspace"), "ROOKS_BACKUP_DIR": str(tmp / "backups"),
           "PATH": "/usr/bin:/bin"}  # no docker on PATH: the database copy is skipped
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


@pytest.fixture()
def install(tmp_path):
    make_tree(tmp_path / "ai-workspace", {**BASE, ".env": "APP_SECRET=keep-me\n", "my-notes.txt": "mine\n"})
    return tmp_path


def test_checksum_is_required_and_checked(install):
    pkg = package(install, {**BASE, "workbench/app.py": "v2\n"}, checksum=False)
    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode != 0 and "No checksum" in r.stderr
    Path(f"{pkg}.sha256").write_text("0" * 64 + "\n")
    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode != 0 and "does not match" in r.stderr
    assert (install / "ai-workspace/workbench/app.py").read_text() == "v1\n"


def test_unsafe_packages_are_refused(install):
    pkg = package(install, {**BASE, ".env": "APP_SECRET=evil\n"})
    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode != 0 and ".env" in r.stderr
    evil = install / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        info = tarfile.TarInfo("ai-workspace/link")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(info)
    Path(f"{evil}.sha256").write_text(hashlib.sha256(evil.read_bytes()).hexdigest())
    r = run(install, "--yes", "--no-setup", str(evil))
    assert r.returncode != 0 and "outside ROOKS" in r.stderr


def test_upgrade_backs_up_applies_and_rolls_back(install):
    app = install / "ai-workspace"
    v2 = {"docker-compose.yml": BASE["docker-compose.yml"], "setup.sh": BASE["setup.sh"], "workbench/app.py": "v2\n",
          "workbench/new.py": "new\n"}
    pkg = package(install, v2, "rooks-update-v2.tar.gz")
    check = run(install, "--check", str(pkg))
    assert check.returncode == 0 and "Check only" in check.stdout
    assert (app / "workbench/app.py").read_text() == "v1\n"

    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode == 0, r.stderr
    assert (app / "workbench/app.py").read_text() == "v2\n" and (app / "workbench/new.py").exists()
    assert (app / ".env").read_text() == "APP_SECRET=keep-me\n" and (app / "my-notes.txt").exists()
    assert (app / "workbench/old.py").exists()  # no record of the old version yet, so nothing is deleted
    assert (app / ".rooks/manifest.sha256").exists()
    backups = list((install / "backups").glob("rooks-*.tar.gz"))
    assert len(backups) == 1 and oct(backups[0].stat().st_mode)[-3:] == "600"

    # Next update: an edit of yours is a conflict, unedited files the new version dropped are removed
    (app / "workbench/app.py").write_text("v2 with my fix\n")
    v3 = {k: v for k, v in v2.items() if k != "workbench/new.py"} | {"workbench/app.py": "v3\n"}
    pkg3 = package(install, v3, "rooks-update-v3.tar.gz")
    r = run(install, "--yes", "--no-setup", str(pkg3))
    assert r.returncode != 0 and "workbench/app.py" in r.stdout and "Nothing was changed" in r.stderr
    assert (app / "workbench/app.py").read_text() == "v2 with my fix\n"
    r = run(install, "--yes", "--no-setup", "--overwrite", str(pkg3))
    assert r.returncode == 0, r.stderr
    assert (app / "workbench/app.py").read_text() == "v3\n" and not (app / "workbench/new.py").exists()

    # Rollback restores the code as it was before the last update, and keeps the replaced folder
    r = run(install, "--yes", "--no-setup", "--rollback")
    assert r.returncode == 0, r.stderr
    assert (app / "workbench/app.py").read_text() == "v2 with my fix\n" and (app / "workbench/new.py").exists()
    assert (app / ".env").read_text() == "APP_SECRET=keep-me\n"
    assert list(install.glob("ai-workspace.before-rollback-*"))


def test_links_inside_rooks_are_kept(install):
    src = make_tree(install / "pkgsrc" / "l" / "ai-workspace", {**BASE, "assistant/hermes/config.yaml": "x: 1\n"})
    (src / "hermes").symlink_to("assistant/hermes")
    pkg = install / "rooks-update-links.tar.gz"
    with tarfile.open(pkg, "w:gz") as t:
        t.add(src, arcname="ai-workspace")
    Path(f"{pkg}.sha256").write_text(hashlib.sha256(pkg.read_bytes()).hexdigest())
    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode == 0, r.stderr
    link = install / "ai-workspace" / "hermes"
    assert link.is_symlink() and os.readlink(link) == "assistant/hermes" and (link / "config.yaml").read_text() == "x: 1\n"


def test_uncommitted_git_changes_are_conflicts(install):
    app = install / "ai-workspace"
    subprocess.run(["git", "init", "-q"], cwd=app, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=app, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], cwd=app, check=True)
    (app / "workbench/app.py").write_text("my uncommitted fix\n")
    pkg = package(install, {**BASE, "workbench/app.py": "v2\n"})
    env_path = os.environ["PATH"]
    r = subprocess.run(["bash", str(SCRIPT), "--yes", "--no-setup", str(pkg)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "ROOKS_DIR": str(app), "ROOKS_BACKUP_DIR": str(install / "backups"), "PATH": env_path})
    assert r.returncode != 0 and "uncommitted change in git" in r.stdout
    assert (app / "workbench/app.py").read_text() == "my uncommitted fix\n"


def test_a_different_compose_project_is_refused(install):
    (install / "ai-workspace" / "docker-compose.yml").write_text("name: ai-workspace\nservices: {}\n")
    pkg = package(install, {**BASE, "docker-compose.yml": "name: something-else\nservices: {}\n"})
    r = run(install, "--yes", "--no-setup", str(pkg))
    assert r.returncode != 0 and "hide your data" in r.stderr
